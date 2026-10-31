"""GGG treasury standard: recognition → allocation → execution (issue #261).

Three stages, one module:

1. **Recognition** — :func:`sweep_deposits` books incoming ICRC-1 transfers
   that no invoice claimed as ``unallocated`` revenue on the source fund
   (usually ROOT). Invoice-matched payments keep their own pipeline.
2. **Allocation** — :func:`run_allocation` splits the recognized revenue of
   a fiscal epoch into department funds using the adopted
   :class:`ggg.AllocationRule` (basis points). Interfund movements are booked
   as *equity* (``transfer_out`` / ``transfer_in``) plus cash lines, so they
   never inflate the income statement.
3. **Execution** — existing spending (payroll, procurement) books expenses
   against the allocated funds; nothing to do here.

Epochs are calendar-aligned :class:`ggg.FiscalPeriod` rows whose length
(monthly/quarterly/semiannual/annual) is policy-configurable via
:class:`ggg.TreasuryConfig`. A length change takes effect after the current
open period ends: the first period under the new length may be partial
(starts the day after the old one closed) and every later one is aligned.

Allocations are idempotent per (fund, period, sequence): re-running only
distributes what is still unallocated, so a mid-epoch ad-hoc allocation and
the automatic close-of-epoch run compose safely. All amounts are raw units
of the realm's accounting currency.

Governance mirrors ``core.payroll``: 1/1 policies act directly, anything
else goes through an org-scoped proposal built by
:func:`build_treasury_proposal_code`.
"""

import json

from ic_python_logging import get_logger

logger = get_logger("core.treasury_allocation")

# Daily check: cheap, idempotent, self-healing across upgrades (IC timers
# are lost on upgrade; the seeded TaskManager schedule is not).
TREASURY_TICK_SECONDS = 86_400
TREASURY_TASK_NAME = "treasury_allocation_schedule"

BASIS_POINTS = 10_000

EPOCH_MONTHS = {
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "annual": 12,
}

EPOCH_LENGTHS = frozenset(set(EPOCH_MONTHS) | {"weekly", "biweekly", "minutes"})

# Monday 1970-01-05 — anchor for 14-day biweekly periods.
_BIWEEKLY_ANCHOR_ORD = 4


# ---------------------------------------------------------------------------
# Time & config
# ---------------------------------------------------------------------------


def _now_ts() -> int:
    """Current unix time from IC time when available (canister-safe)."""
    ts = 0
    try:
        from _cdk import ic

        ts = int(ic.time()) // 1_000_000_000
    except Exception:
        pass
    if ts <= 0:
        import time

        ts = int(time.time())
    return ts


# NOTE: the canister's frozen datetime module is unreliable (fromtimestamp
# returns 1970 regardless of input, no date/timedelta), so all date math
# below is pure integer arithmetic on (year, month, day) tuples and ISO
# "YYYY-MM-DD" strings.


def civil_from_ts(ts: int) -> tuple:
    """Unix timestamp → (year, month, day) in UTC — no datetime involved.

    Howard Hinnant's civil_from_days algorithm; exact for all Gregorian dates.
    """
    z = ts // 86_400 + 719_468
    era = (z if z >= 0 else z - 146_096) // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1_460 + doe // 36_524 - doe // 146_096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    if month <= 2:
        year += 1
    return (year, month, day)


def _today() -> tuple:
    """Today as a (year, month, day) tuple (canister-safe)."""
    return civil_from_ts(_now_ts())


def _iso(ymd: tuple) -> str:
    return f"{ymd[0]:04d}-{ymd[1]:02d}-{ymd[2]:02d}"


def _parse_iso(iso: str) -> tuple:
    parts = str(iso)[:10].split("-")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _next_day_iso(iso: str) -> str:
    y, m, d = _parse_iso(iso)
    if d < _days_in_month(y, m):
        return _iso((y, m, d + 1))
    if m < 12:
        return _iso((y, m + 1, 1))
    return _iso((y + 1, 1, 1))


def _ymd_to_ord(y: int, m: int, d: int) -> int:
    """Proleptic Gregorian days since 1970-01-01 (UTC)."""
    y_adj = y - (1 if m <= 2 else 0)
    era = y_adj // 400 if y_adj >= 0 else (y_adj - 399) // 400
    yoe = y_adj - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _ord_to_ymd(ord_day: int) -> tuple:
    """Inverse of :func:`_ymd_to_ord` — matches :func:`civil_from_ts`."""
    return civil_from_ts(int(ord_day) * 86_400)


def _monday_of_week(ymd: tuple) -> tuple:
    """Monday (ISO) on or before *ymd*."""
    ord_day = _ymd_to_ord(*ymd)
    return _ord_to_ymd(ord_day - (ord_day + 3) % 7)


def _biweekly_start(ymd: tuple) -> tuple:
    """Start (Monday) of the 14-day epoch containing *ymd*."""
    ord_day = _ymd_to_ord(*ymd)
    start_ord = _BIWEEKLY_ANCHOR_ORD + ((ord_day - _BIWEEKLY_ANCHOR_ORD) // 14) * 14
    return _ord_to_ymd(start_ord)


def _ts_to_iso(ts: int) -> str:
    ymd = civil_from_ts(int(ts))
    rem = int(ts) % 86_400
    hours = rem // 3600
    minutes = (rem % 3600) // 60
    seconds = rem % 60
    return f"{_iso(ymd)}T{hours:02d}:{minutes:02d}:{seconds:02d}Z"


def _parse_iso_ts(iso: str) -> int:
    """Parse an ISO date or datetime string to a unix timestamp (UTC)."""
    raw = str(iso or "").strip()
    if not raw:
        return 0
    if "T" in raw:
        date_part, time_part = raw[:10], raw[11:19]
        y, m, d = _parse_iso(date_part)
        parts = time_part.replace("Z", "").split(":")
        hours = int(parts[0]) if len(parts) > 0 else 0
        minutes = int(parts[1]) if len(parts) > 1 else 0
        seconds = int(parts[2]) if len(parts) > 2 else 0
        return _ymd_to_ord(y, m, d) * 86_400 + hours * 3600 + minutes * 60 + seconds
    y, m, d = _parse_iso(raw[:10])
    return _ymd_to_ord(y, m, d) * 86_400 + 86_399


def _period_end_passed(end_incl: str, cfg) -> bool:
    """True when an open period's inclusive end is strictly before now."""
    end_incl = str(end_incl or "").strip()
    if not end_incl:
        return False
    length = (cfg.epoch_length or "monthly").lower()
    if length == "minutes" or "T" in end_incl:
        return _parse_iso_ts(end_incl) < _now_ts()
    return end_incl[:10] < _iso(_today())


def treasury_currency() -> str:
    """The realm's accounting currency, empty until a treasury ledger resolves."""
    from core.realm_currency import realm_currency

    return realm_currency()


def get_treasury_config():
    """The TreasuryConfig singleton, created with defaults on first use."""
    from ggg import TreasuryConfig

    cfg = TreasuryConfig["1"]
    if cfg is None:
        cfg = TreasuryConfig(id="1")
    return cfg


def set_epoch_config(
    epoch_length: str,
    anchor_month: int = None,
    epoch_minutes: int = None,
    triggered_by: str = "",
) -> dict:
    """Change the epoch length (policy-gated by the caller).

    Takes effect when the currently open period ends; see module docstring.
    """
    epoch_length = (epoch_length or "").strip().lower()
    if epoch_length == "bi-weekly":
        epoch_length = "biweekly"
    if epoch_length not in EPOCH_LENGTHS:
        return {"error": f"epoch_length must be one of {sorted(EPOCH_LENGTHS)}"}
    if epoch_length == "minutes":
        from core.runtime_flags import is_test_mode

        if not is_test_mode():
            return {"error": "Minute-based epochs are only available in test mode"}
        mins = int(epoch_minutes or 0)
        if mins < 1 or mins > 10_080:
            return {"error": "epoch_minutes must be between 1 and 10080"}
    cfg = get_treasury_config()
    cfg.epoch_length = epoch_length
    if epoch_length == "minutes":
        cfg.epoch_minutes = int(epoch_minutes or 0)
    else:
        cfg.epoch_minutes = 0
    if anchor_month is not None:
        cfg.anchor_month = min(max(int(anchor_month), 1), 12)
    logger.info(
        f"Treasury epoch config set to {epoch_length} "
        f"(anchor month {cfg.anchor_month}, minutes {cfg.epoch_minutes or 0}, "
        f"by {triggered_by or 'unknown'})"
    )
    return {
        "success": True,
        "epoch_length": cfg.epoch_length,
        "epoch_minutes": int(cfg.epoch_minutes or 0),
        "anchor_month": int(cfg.anchor_month or 1),
    }


# ---------------------------------------------------------------------------
# Calendar-aligned epochs
# ---------------------------------------------------------------------------


def _epoch_start_for(ymd: tuple, months: int, anchor: int) -> tuple:
    """(year, month) of the first day of the epoch containing *ymd*."""
    total = ymd[0] * 12 + (ymd[1] - 1)
    a = anchor - 1
    idx = (total - a) // months
    start_total = a + idx * months
    return (start_total // 12, start_total % 12 + 1)


def epoch_id_for(ymd: tuple, cfg=None) -> str:
    """Calendar-aligned epoch id containing *ymd* under the current config."""
    cfg = cfg or get_treasury_config()
    length = (cfg.epoch_length or "monthly").lower()
    if length == "minutes":
        period_sec = max(int(cfg.epoch_minutes or 0), 1) * 60
        idx = _now_ts() // period_sec
        return f"m{idx}"
    if length == "weekly":
        return f"W{_iso(_monday_of_week(ymd))}"
    if length == "biweekly":
        return f"B{_iso(_biweekly_start(ymd))}"
    months = EPOCH_MONTHS.get(length, 1)
    anchor = min(max(int(cfg.anchor_month or 1), 1), 12)
    start_y, start_m = _epoch_start_for(ymd, months, anchor)
    if length == "monthly":
        return f"{start_y:04d}-{start_m:02d}"
    if length == "quarterly":
        q = ((start_m - anchor) % 12) // 3 + 1
        return f"{start_y:04d}-Q{q}"
    if length == "semiannual":
        h = ((start_m - anchor) % 12) // 6 + 1
        return f"{start_y:04d}-H{h}"
    return f"FY{start_y:04d}"


def epoch_bounds_for(ymd: tuple, cfg=None) -> tuple:
    """(start_iso, inclusive end_iso) of the epoch containing *ymd*."""
    cfg = cfg or get_treasury_config()
    length = (cfg.epoch_length or "monthly").lower()
    if length == "minutes":
        ts = _now_ts()
        period_sec = max(int(cfg.epoch_minutes or 0), 1) * 60
        start_ts = (ts // period_sec) * period_sec
        end_ts = start_ts + period_sec - 1
        return (_ts_to_iso(start_ts), _ts_to_iso(end_ts))
    if length == "weekly":
        start = _monday_of_week(ymd)
        end_ord = _ymd_to_ord(*start) + 6
        return (_iso(start), _iso(_ord_to_ymd(end_ord)))
    if length == "biweekly":
        start = _biweekly_start(ymd)
        end_ord = _ymd_to_ord(*start) + 13
        return (_iso(start), _iso(_ord_to_ymd(end_ord)))
    months = EPOCH_MONTHS.get(length, 1)
    anchor = min(max(int(cfg.anchor_month or 1), 1), 12)
    start_y, start_m = _epoch_start_for(ymd, months, anchor)
    end_total = start_y * 12 + (start_m - 1) + months - 1
    end_y, end_m = end_total // 12, end_total % 12 + 1
    return (
        _iso((start_y, start_m, 1)),
        _iso((end_y, end_m, _days_in_month(end_y, end_m))),
    )


def _period_range(period) -> tuple:
    """(start_iso, exclusive end_iso) for date-string comparisons.

    LedgerEntry.entry_date may carry a time component, so the exclusive
    bound is the instant *after* the period's inclusive end.
    """
    start_iso = str(period.start_date or "")
    end_incl = str(period.end_date or "")
    if "T" in end_incl:
        end_excl = _ts_to_iso(_parse_iso_ts(end_incl) + 1)
        return start_iso, end_excl
    start_iso = start_iso[:10]
    end_incl = end_incl[:10]
    try:
        end_excl = _next_day_iso(end_incl)
    except Exception:
        end_excl = "9999-12-31"
    return start_iso, end_excl


def ensure_epoch_periods() -> dict:
    """Close finished FiscalPeriods and open the current epoch's period.

    Called by the daily tick and lazily by the read model. Returns the ids
    of periods that were closed by this call (the schedule allocates them).
    """
    from ggg import FiscalPeriod, FiscalPeriodStatus

    cfg = get_treasury_config()
    today = _today()
    closed_now = []
    latest_end = None
    for period in FiscalPeriod.instances():
        end_incl = str(period.end_date or "")
        end_cmp = end_incl if "T" in end_incl else end_incl[:10]
        if end_cmp:
            if latest_end is None or end_cmp > latest_end:
                latest_end = end_cmp
        if period.status == FiscalPeriodStatus.OPEN and _period_end_passed(
            end_incl, cfg
        ):
            period.close()
            closed_now.append(period.id)

    eid = epoch_id_for(today, cfg)
    current = FiscalPeriod[eid]
    if current is None:
        start_iso, end_iso = epoch_bounds_for(today, cfg)
        # Partial first period after an epoch-length change: never overlap
        # an already-recorded period.
        if latest_end and latest_end >= start_iso:
            try:
                start_iso = _next_day_iso(latest_end)
            except Exception:
                pass
        current = FiscalPeriod(
            id=eid,
            name=f"Fiscal period {eid}",
            start_date=start_iso,
            end_date=end_iso,
            status=FiscalPeriodStatus.OPEN,
        )
        logger.info(f"Opened fiscal period {eid} ({start_iso} → {end_iso})")
    return {"current": eid, "closed": closed_now}


def current_epoch_id() -> str:
    return ensure_epoch_periods()["current"]


# ---------------------------------------------------------------------------
# Funds
# ---------------------------------------------------------------------------


def _source_fund():
    from ggg import Fund

    cfg = get_treasury_config()
    code = (cfg.source_fund_code or "ROOT").strip() or "ROOT"
    fund = Fund[code]
    if fund is None:
        # Fall back to the realm's first fund so a mis-seeded realm still works.
        funds = list(Fund.instances())
        fund = funds[0] if funds else None
    return fund


# ---------------------------------------------------------------------------
# Stage 1 — recognition: sweep unmatched deposits
# ---------------------------------------------------------------------------


def _invoice_claimed_amounts() -> dict:
    """Multiset {raw_amount: count} of amounts owned by the invoice pipeline.

    Any incoming transfer whose amount exactly matches a live invoice's
    nonce-adjusted amount is (or will be) booked by invoice accounting, so
    the sweep must leave it alone. Matching by exact amount is the same rule
    the invoice refresh itself uses.
    """
    from ggg import Invoice

    claimed = {}
    for inv in Invoice.instances():
        if inv.status not in ("Pending", "Paid"):
            continue
        try:
            amount = int(inv.get_nonce_amount_raw())
        except Exception:
            continue
        if amount > 0:
            claimed[amount] = claimed.get(amount, 0) + 1
    return claimed


def recognize_unmatched_deposits() -> dict:
    """Book incoming transfers that no invoice claimed as unallocated revenue.

    Sync helper (no yield). Idempotent via deterministic transaction ids.
    """
    from _cdk import ic
    from ggg import Category, EntryType, FiscalPeriod, LedgerEntry, Token
    from ic_basilisk_toolkit.entities import WalletTransfer

    currency = treasury_currency()
    if not currency:
        from core.realm_currency import no_treasury_token_error

        return no_treasury_token_error()
    token = Token[currency]
    if token is None:
        return {"error": f"No registered token for accounting currency '{currency}'"}

    fund = _source_fund()
    if fund is None:
        return {"error": "No source fund — seed the realm's funds first"}

    period_id = current_epoch_id()
    period = FiscalPeriod[period_id]
    self_principal = ic.id().to_str()
    claimed = _invoice_claimed_amounts()

    swept = 0
    swept_amount = 0
    skipped_invoice = 0
    for wt in WalletTransfer.instances():
        try:
            if wt.token is None or wt.token.name != currency:
                continue
        except Exception:
            continue
        if wt.kind not in ("transfer", "mint"):
            continue
        if wt.principal_to != self_principal or wt.principal_from == self_principal:
            continue
        amount = int(wt.amount or 0)
        if amount <= 0:
            continue
        txid = f"SWEEP-{currency}-{wt.tx_id}"
        if LedgerEntry.find({"transaction_id": txid}):
            continue
        if claimed.get(amount, 0) > 0:
            claimed[amount] -= 1
            skipped_invoice += 1
            continue
        desc = f"Unassigned deposit from {wt.principal_from}"
        LedgerEntry.create_transaction(
            txid,
            [
                {
                    "entry_type": EntryType.ASSET,
                    "category": Category.CASH,
                    "debit": amount,
                    "fund": fund,
                    "fiscal_period": period,
                    "entry_date": _iso(_today()),
                    "currency": currency,
                    "description": desc,
                    "reference": str(wt.tx_id or ""),
                },
                {
                    "entry_type": EntryType.REVENUE,
                    "category": Category.UNALLOCATED,
                    "credit": amount,
                    "fund": fund,
                    "fiscal_period": period,
                    "entry_date": _iso(_today()),
                    "currency": currency,
                    "description": desc,
                    "reference": str(wt.tx_id or ""),
                },
            ],
        )
        swept += 1
        swept_amount += amount

    result = {
        "success": True,
        "currency": currency,
        "swept": swept,
        "swept_amount": swept_amount,
        "skipped_invoice_matched": skipped_invoice,
        "period": period_id,
    }
    logger.info(f"Treasury recognition: {result}")
    return result


def sweep_deposits():
    """Generator: refresh the treasury wallet and book unmatched deposits.

    Each new incoming transfer (to the canister's default account) that no
    invoice claims is recognized as ``unallocated`` revenue on the source
    fund, dated today (recognition date), in the current open period.
    Idempotent via the deterministic transaction id ``SWEEP-<token>-<tx id>``.
    """
    from ggg import Token
    from ic_basilisk_toolkit.wallet import Wallet

    currency = treasury_currency()
    if not currency:
        from core.realm_currency import no_treasury_token_error

        return no_treasury_token_error()
    token = Token[currency]
    if token is None:
        return {"error": f"No registered token for accounting currency '{currency}'"}

    fund = _source_fund()
    if fund is None:
        return {"error": "No source fund — seed the realm's funds first"}

    wallet = Wallet()
    try:
        refresh = yield wallet.refresh(currency)
    except Exception as e:
        logger.warning(f"Treasury sweep: wallet refresh failed: {e}")
        refresh = {"new_txs": 0, "error": str(e)}

    recognize = recognize_unmatched_deposits()
    if recognize.get("error"):
        return recognize

    result = {
        "success": True,
        "currency": currency,
        "new_txs": int(refresh.get("new_txs", 0) or 0),
        "swept": recognize.get("swept", 0),
        "swept_amount": recognize.get("swept_amount", 0),
        "skipped_invoice_matched": recognize.get("skipped_invoice_matched", 0),
        "period": recognize.get("period"),
    }
    logger.info(f"Treasury sweep: {result}")
    return result


# ---------------------------------------------------------------------------
# Allocation rules
# ---------------------------------------------------------------------------


def _parse_rules(rules_json: str) -> list:
    try:
        rules = json.loads(rules_json or "[]")
    except Exception:
        rules = []
    return rules if isinstance(rules, list) else []


def active_allocation_rule():
    """The adopted rule currently in force (newest wins), or None."""
    from ggg import AllocationRule, AllocationRuleStatus

    adopted = [
        r
        for r in AllocationRule.instances()
        if r.status == AllocationRuleStatus.ADOPTED
    ]
    if not adopted:
        return None
    return sorted(adopted, key=lambda r: r.id)[-1]


def set_allocation_rule(
    rules: list, description: str = "", triggered_by: str = ""
) -> dict:
    """Adopt a new percentage split, superseding the previous adopted rule.

    ``rules`` is ``[{"fund": code, "percent_bp": int}, ...]``; basis points
    must not exceed 10000 and any remainder stays in the source fund.
    """
    from ggg import AllocationRule, AllocationRuleStatus, Fund

    if not isinstance(rules, list) or not rules:
        return {"error": "rules must be a non-empty list"}

    source = _source_fund()
    source_code = source.code if source else "ROOT"
    total_bp = 0
    cleaned = []
    seen = set()
    for row in rules:
        fund_code = str((row or {}).get("fund", "")).strip()
        try:
            bp = int((row or {}).get("percent_bp", 0))
        except Exception:
            bp = 0
        if not fund_code or bp <= 0:
            continue
        if fund_code == source_code:
            return {"error": "Cannot allocate the source fund to itself"}
        if fund_code in seen:
            return {"error": f"Duplicate fund '{fund_code}' in rules"}
        if Fund[fund_code] is None:
            return {"error": f"Fund '{fund_code}' not found"}
        seen.add(fund_code)
        total_bp += bp
        cleaned.append({"fund": fund_code, "percent_bp": bp})
    if not cleaned:
        return {"error": "No valid allocation lines"}
    if total_bp > BASIS_POINTS:
        return {"error": f"Percentages exceed 100% ({total_bp} bp)"}

    previous = active_allocation_rule()
    if previous is not None:
        previous.status = AllocationRuleStatus.SUPERSEDED

    next_num = len(list(AllocationRule.instances())) + 1
    rule = AllocationRule(
        id=f"ALLOC-RULE-{next_num:03d}",
        source_fund_code=source_code,
        rules=json.dumps(cleaned, sort_keys=True),
        status=AllocationRuleStatus.ADOPTED,
        description=description or f"Adopted by {triggered_by or 'unknown'}",
    )
    logger.info(f"Allocation rule {rule.id} adopted: {cleaned}")
    return {
        "success": True,
        "rule_id": rule.id,
        "rules": cleaned,
        "reserve_bp": BASIS_POINTS - total_bp,
        "superseded": previous.id if previous else None,
    }


# ---------------------------------------------------------------------------
# Pool math (read model helpers)
# ---------------------------------------------------------------------------


def _entries_in_period(period, fund=None, entry_type=None) -> list:
    from ggg import LedgerEntry

    start_iso, end_excl = _period_range(period)
    filters = {}
    if entry_type:
        filters["entry_type"] = entry_type
    entries = LedgerEntry.find(filters) if filters else list(LedgerEntry.instances())
    out = []
    for e in entries:
        if fund is not None and e.fund != fund:
            continue
        d = str(e.entry_date or "")[:10]
        if not d or d < start_iso or d >= end_excl:
            continue
        out.append(e)
    return out


def recognized_revenue(period, fund=None) -> int:
    """Net revenue recognized on *fund* during *period* (raw units)."""
    from ggg import EntryType

    fund = fund or _source_fund()
    entries = _entries_in_period(period, fund=fund, entry_type=EntryType.REVENUE)
    return sum((e.credit or 0) - (e.debit or 0) for e in entries)


def allocated_out(period, fund=None) -> int:
    """Total already moved out of *fund* by allocations in *period*."""
    from ggg import Category, EntryType

    fund = fund or _source_fund()
    entries = _entries_in_period(period, fund=fund, entry_type=EntryType.EQUITY)
    return sum((e.debit or 0) for e in entries if e.category == Category.TRANSFER_OUT)


# ---------------------------------------------------------------------------
# Stage 2 — allocation engine
# ---------------------------------------------------------------------------


def run_allocation(period_id: str = None, triggered_by: str = "") -> dict:
    """Distribute the unallocated pool of one epoch into funds by rule.

    Idempotent and re-runnable: each run only distributes what is still
    unallocated (recognized revenue minus previous allocations), so ad-hoc
    mid-epoch runs and the automatic close run compose. Interfund movements
    are equity + cash lines; the income statement is untouched.
    """
    from ggg import (
        Budget,
        BudgetStatus,
        Category,
        EntryType,
        FiscalPeriod,
        Fund,
        LedgerEntry,
    )

    ensure_epoch_periods()
    period_id = (period_id or "").strip() or epoch_id_for(_today())
    period = FiscalPeriod[period_id]
    if period is None:
        return {"error": f"Fiscal period '{period_id}' not found"}

    source = _source_fund()
    if source is None:
        return {"error": "No source fund — seed the realm's funds first"}

    rule = active_allocation_rule()
    if rule is None:
        return {"error": "No adopted allocation rule — set one first"}
    lines = _parse_rules(rule.rules)
    if not lines:
        return {"error": f"Allocation rule {rule.id} has no lines"}

    pool = recognized_revenue(period, source)
    already = allocated_out(period, source)
    remaining = pool - already
    if remaining <= 0:
        return {
            "success": True,
            "period": period_id,
            "pool": pool,
            "already_allocated": already,
            "allocated_now": 0,
            "message": "Nothing left to allocate for this period",
        }

    # Sequence number makes repeated (ad-hoc + close) runs idempotent per run.
    seq = 0
    while LedgerEntry.find(
        {"transaction_id": f"ALLOC-{period_id}-{seq}-{source.code}"}
    ):
        seq += 1

    currency = treasury_currency()
    if not currency:
        from core.realm_currency import no_treasury_token_error

        return no_treasury_token_error()
    today_iso = _iso(_today())
    allocations = []
    allocated_now = 0
    for line in lines:
        target = Fund[line["fund"]]
        if target is None:
            logger.warning(f"Allocation target fund '{line['fund']}' missing; skipped")
            continue
        amount = remaining * int(line["percent_bp"]) // BASIS_POINTS
        if amount <= 0:
            continue
        txid = f"ALLOC-{period_id}-{seq}-{target.code}"
        LedgerEntry.create_transaction(
            txid,
            [
                {
                    "entry_type": EntryType.EQUITY,
                    "category": Category.TRANSFER_OUT,
                    "debit": amount,
                    "fund": source,
                    "fiscal_period": period,
                    "entry_date": today_iso,
                    "currency": currency,
                    "description": f"Allocation to {target.code} ({rule.id})",
                },
                {
                    "entry_type": EntryType.ASSET,
                    "category": Category.CASH,
                    "credit": amount,
                    "fund": source,
                    "fiscal_period": period,
                    "entry_date": today_iso,
                    "currency": currency,
                    "description": f"Allocation to {target.code} ({rule.id})",
                },
                {
                    "entry_type": EntryType.ASSET,
                    "category": Category.CASH,
                    "debit": amount,
                    "fund": target,
                    "fiscal_period": period,
                    "entry_date": today_iso,
                    "currency": currency,
                    "description": f"Allocation from {source.code} ({rule.id})",
                },
                {
                    "entry_type": EntryType.EQUITY,
                    "category": Category.TRANSFER_IN,
                    "credit": amount,
                    "fund": target,
                    "fiscal_period": period,
                    "entry_date": today_iso,
                    "currency": currency,
                    "description": f"Allocation from {source.code} ({rule.id})",
                },
            ],
        )
        budget_id = f"BUD-{target.code}-{period_id}"
        budget = Budget[budget_id]
        if budget is None:
            budget = Budget(
                id=budget_id,
                name=f"{target.code} allocation {period_id}",
                fund=target,
                fiscal_period=period,
                category="allocation",
                budget_type="revenue",
                planned_amount=pool * int(line["percent_bp"]) // BASIS_POINTS,
                status=BudgetStatus.ADOPTED,
            )
        budget.update_actual(amount)
        allocated_now += amount
        allocations.append(
            {"fund": target.code, "amount": amount, "percent_bp": line["percent_bp"]}
        )

    logger.info(
        f"Allocation run for {period_id} (seq {seq}, by "
        f"{triggered_by or 'unknown'}): {allocated_now} across "
        f"{len(allocations)} fund(s)"
    )
    return {
        "success": True,
        "period": period_id,
        "rule_id": rule.id,
        "pool": pool,
        "already_allocated": already,
        "allocated_now": allocated_now,
        "reserve_left": pool - already - allocated_now,
        "allocations": allocations,
        "sequence": seq,
    }


# ---------------------------------------------------------------------------
# Standing schedule (daily tick)
# ---------------------------------------------------------------------------


def set_treasury_schedule(
    enabled: bool, auto_allocate: bool = None, triggered_by: str = ""
) -> dict:
    """Enable/disable the daily treasury tick (sweep + auto-allocation)."""
    cfg = get_treasury_config()
    if auto_allocate is not None:
        cfg.auto_allocate = "true" if auto_allocate else "false"

    if not enabled:
        try:
            from ggg import Task

            for task in Task.instances():
                if task.name == TREASURY_TASK_NAME:
                    for schedule in task.schedules:
                        schedule.disabled = True
        except Exception as e:
            logger.warning(f"Could not disable treasury task: {e}")
        logger.info(f"Treasury schedule disabled (by {triggered_by or 'unknown'})")
        return {"success": True, "schedule_active": False}

    from core.quarter_bootstrap import seed_recurring_codex_task

    seed_recurring_codex_task(
        TREASURY_TASK_NAME,
        _tick_step_code(),
        TREASURY_TICK_SECONDS,
    )
    logger.info(
        f"Treasury schedule enabled (auto_allocate={cfg.auto_allocate}, "
        f"by {triggered_by or 'unknown'})"
    )
    return {
        "success": True,
        "schedule_active": True,
        "auto_allocate": cfg.auto_allocate == "true",
    }


def _tick_step_code() -> str:
    """Codex shim for the daily treasury tick (same pattern as payroll)."""
    return (
        "import json\n"
        "\n"
        "def async_task():\n"
        "    from core.treasury_allocation import treasury_tick\n"
        "    result = yield from treasury_tick()\n"
        "    return json.dumps(result)\n"
    )


def treasury_tick():
    """Generator: daily sweep, epoch rollover, auto-allocation, and period reports."""
    sweep = yield from sweep_deposits()

    periods = ensure_epoch_periods()
    cfg = get_treasury_config()
    auto_runs = []
    if cfg.auto_allocate == "true":
        # Periods closed by *this* tick get their final allocation exactly
        # once — the close happens on a single status transition.
        for period_id in periods.get("closed", []):
            auto_runs.append(run_allocation(period_id, triggered_by="schedule"))

    from core.financial_reports import issue_period_report

    issued_reports = []
    for period_id in periods.get("closed", []):
        issued_reports.append(
            issue_period_report(period_id, issued_by="system")
        )

    return {
        "sweep": sweep,
        "current_period": periods.get("current"),
        "closed_periods": periods.get("closed", []),
        "auto_allocations": auto_runs,
        "issued_reports": issued_reports,
    }


def treasury_schedule_status() -> dict:
    """Whether the daily treasury tick is active."""
    active = False
    try:
        from ggg import Task

        for task in Task.instances():
            if task.name == TREASURY_TASK_NAME:
                active = any(not s.disabled for s in task.schedules)
                break
    except Exception as e:
        logger.warning(f"treasury_schedule_status failed: {e}")
    cfg = get_treasury_config()
    return {
        "schedule_active": active,
        "auto_allocate": cfg.auto_allocate == "true",
    }


# ---------------------------------------------------------------------------
# Read models (Budget Management extension)
# ---------------------------------------------------------------------------


def _list_periods() -> list:
    from ggg import FiscalPeriod

    periods = sorted(FiscalPeriod.instances(), key=lambda p: str(p.start_date or ""))
    return [
        {
            "id": p.id,
            "name": p.name or p.id,
            "start_date": str(p.start_date or "")[:10],
            "end_date": str(p.end_date or "")[:10],
            "status": p.status,
        }
        for p in periods
    ]


def treasury_overview() -> dict:
    """Everything the extension's header/settings need in one call."""
    from ggg import Fund, Token

    ensure_epoch_periods()
    cfg = get_treasury_config()
    from core.runtime_flags import is_test_mode

    source = _source_fund()
    currency = treasury_currency()
    token = Token[currency]
    rule = active_allocation_rule()

    funds = []
    for fund in Fund.instances():
        dept = None
        try:
            dept = fund.department.name if fund.department else None
        except Exception:
            pass
        funds.append(
            {
                "code": fund.code,
                "name": fund.name or fund.code,
                "fund_type": fund.fund_type or "",
                "department": dept,
            }
        )

    return {
        "currency": currency,
        "decimals": int(token.decimals or 8) if token else 8,
        "source_fund": source.code if source else None,
        "epoch_length": cfg.epoch_length or "monthly",
        "epoch_minutes": int(cfg.epoch_minutes or 0),
        "anchor_month": int(cfg.anchor_month or 1),
        "test_mode": is_test_mode(),
        "current_period": epoch_id_for(_today(), cfg),
        "periods": _list_periods(),
        "rule": (
            {
                "id": rule.id,
                "rules": _parse_rules(rule.rules),
                "description": rule.description or "",
            }
            if rule
            else None
        ),
        "funds": sorted(funds, key=lambda f: f["code"] or ""),
        **treasury_schedule_status(),
    }


def allocation_status(period_id: str = None) -> dict:
    """Pool, allocations and reserve for one epoch."""
    from ggg import Category, EntryType, FiscalPeriod

    ensure_epoch_periods()
    period_id = (period_id or "").strip() or epoch_id_for(_today())
    period = FiscalPeriod[period_id]
    if period is None:
        return {"error": f"Fiscal period '{period_id}' not found"}

    source = _source_fund()
    pool = recognized_revenue(period, source)
    already = allocated_out(period, source)

    # Per-fund inbound allocations in this period.
    per_fund = {}
    entries = _entries_in_period(period, entry_type=EntryType.EQUITY)
    for e in entries:
        if e.category != Category.TRANSFER_IN or not e.credit:
            continue
        code = e.fund.code if e.fund else "?"
        per_fund[code] = per_fund.get(code, 0) + int(e.credit or 0)

    # Revenue mix on the source fund.
    by_category = {}
    for e in _entries_in_period(period, fund=source, entry_type=EntryType.REVENUE):
        net = (e.credit or 0) - (e.debit or 0)
        if net == 0:
            continue
        by_category[e.category or "other"] = (
            by_category.get(e.category or "other", 0) + net
        )

    return {
        "period": {
            "id": period.id,
            "start_date": str(period.start_date or "")[:10],
            "end_date": str(period.end_date or "")[:10],
            "status": period.status,
        },
        "source_fund": source.code if source else None,
        "pool": pool,
        "allocated": already,
        "unallocated": pool - already,
        "revenue_by_category": by_category,
        "allocations_by_fund": per_fund,
    }


def allocation_flows(period_id: str = None) -> dict:
    """Sankey read model: revenue mix → pool → funds → spending.

    Node values and link values are raw currency units; the frontend only
    lays them out — no financial math happens client-side.
    """
    from ggg import Category, EntryType, Fund

    status = allocation_status(period_id)
    if status.get("error"):
        return status

    source_code = status.get("source_fund") or "ROOT"
    pool = int(status.get("pool") or 0)
    nodes = []
    links = []

    # Column 0 — revenue categories on the source fund.
    for category, amount in sorted(status["revenue_by_category"].items()):
        if amount <= 0:
            continue
        node_id = f"rev:{category}"
        nodes.append(
            {
                "id": node_id,
                "label": category.replace("_", " "),
                "column": 0,
                "value": amount,
            }
        )
        links.append({"source": node_id, "target": "pool", "value": amount})

    # Column 1 — the treasury pool.
    nodes.append(
        {"id": "pool", "label": f"{source_code} pool", "column": 1, "value": pool}
    )

    # Column 2 — allocations per fund + the untouched reserve.
    from ggg import FiscalPeriod

    period = FiscalPeriod[status["period"]["id"]]
    for code, amount in sorted(status["allocations_by_fund"].items()):
        fund = Fund[code]
        label = (fund.name or code) if fund else code
        node_id = f"fund:{code}"
        nodes.append({"id": node_id, "label": label, "column": 2, "value": amount})
        links.append({"source": "pool", "target": node_id, "value": amount})
    reserve = int(status.get("unallocated") or 0)
    if reserve > 0:
        nodes.append(
            {
                "id": "reserve",
                "label": "Unallocated reserve",
                "column": 2,
                "value": reserve,
            }
        )
        links.append({"source": "pool", "target": "reserve", "value": reserve})

    # Column 3 — spending out of each allocated fund during the period.
    if period is not None:
        expense_totals = {}
        for code in status["allocations_by_fund"]:
            fund = Fund[code]
            if fund is None:
                continue
            for e in _entries_in_period(
                period, fund=fund, entry_type=EntryType.EXPENSE
            ):
                net = (e.debit or 0) - (e.credit or 0)
                if net <= 0:
                    continue
                category = e.category or "other"
                key = f"exp:{category}"
                expense_totals[key] = expense_totals.get(key, 0) + net
                links.append({"source": f"fund:{code}", "target": key, "value": net})
        for key, total in sorted(expense_totals.items()):
            label = key.split(":", 1)[1].replace("_", " ")
            nodes.append({"id": key, "label": label, "column": 3, "value": total})

    return {
        "period": status["period"],
        "currency": treasury_currency(),
        "nodes": nodes,
        "links": links,
    }


def _add_months_ym(y: int, m: int, delta: int) -> tuple:
    total = y * 12 + (m - 1) + delta
    return total // 12, total % 12 + 1


def _epoch_at_ts(ts: int, cfg=None) -> dict:
    """Epoch containing unix timestamp *ts* under *cfg*."""
    cfg = cfg or get_treasury_config()
    length = (cfg.epoch_length or "monthly").lower()
    ts = int(ts)

    if length == "minutes":
        period_sec = max(int(cfg.epoch_minutes or 0), 1) * 60
        idx = ts // period_sec
        start_ts = idx * period_sec
        end_ts = start_ts + period_sec - 1
        return {
            "id": f"m{idx}",
            "start_date": _ts_to_iso(start_ts),
            "end_date": _ts_to_iso(end_ts),
            "start_ts": start_ts,
            "end_ts": end_ts,
        }

    ymd = civil_from_ts(ts)
    if length == "weekly":
        start = _monday_of_week(ymd)
        start_ts = _ymd_to_ord(*start) * 86_400
        end_ts = start_ts + 7 * 86_400 - 1
        eid = f"W{_iso(start)}"
        start_iso, end_iso = _iso(start), _iso(_ord_to_ymd(start_ts // 86_400 + 6))
    elif length == "biweekly":
        start = _biweekly_start(ymd)
        start_ts = _ymd_to_ord(*start) * 86_400
        end_ts = start_ts + 14 * 86_400 - 1
        eid = f"B{_iso(start)}"
        start_iso = _iso(start)
        end_iso = _iso(_ord_to_ymd(start_ts // 86_400 + 13))
    else:
        eid = epoch_id_for(ymd, cfg)
        start_iso, end_iso = epoch_bounds_for(ymd, cfg)
        start_ts = _parse_iso_ts(start_iso)
        end_ts = _parse_iso_ts(end_iso)

    return {
        "id": eid,
        "start_date": start_iso,
        "end_date": end_iso,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


def _step_epoch_start(start_ts: int, cfg, direction: int) -> int:
    """Start timestamp of the epoch *direction* steps from *start_ts* (-1 prev, +1 next)."""
    length = (cfg.epoch_length or "monthly").lower()
    if length == "minutes":
        period_sec = max(int(cfg.epoch_minutes or 0), 1) * 60
        return int(start_ts) + direction * period_sec
    if length == "weekly":
        return int(start_ts) + direction * 7 * 86_400
    if length == "biweekly":
        return int(start_ts) + direction * 14 * 86_400
    months = EPOCH_MONTHS.get(length, 1)
    y, m, _ = civil_from_ts(int(start_ts))
    ny, nm = _add_months_ym(y, m, direction * months)
    return _ymd_to_ord(ny, nm, 1) * 86_400


def epoch_timeline(
    center_ts: int = None,
    before: int = 20,
    after: int = 20,
) -> dict:
    """Timeline read model: projected epochs with optional stored stats.

    Returns a window of epochs centred on *center_ts* (default: now), each
    with start/end timestamps, lifecycle status, and treasury totals when a
    :class:`ggg.FiscalPeriod` record exists.
    """
    from ggg import FiscalPeriod

    ensure_epoch_periods()
    cfg = get_treasury_config()
    now_ts = _now_ts()
    center_ts = int(center_ts or now_ts)
    before = max(0, min(int(before or 20), 60))
    after = max(0, min(int(after or 20), 60))

    current = _epoch_at_ts(center_ts, cfg)
    start_ts = current["start_ts"]
    for _ in range(before):
        start_ts = _step_epoch_start(start_ts, cfg, -1)

    stored = {p.id: p for p in FiscalPeriod.instances()}
    source = _source_fund()
    epochs = []
    ts = start_ts
    for _ in range(before + after + 1):
        ep = _epoch_at_ts(ts, cfg)
        period = stored.get(ep["id"])
        if period is not None:
            ep["status"] = period.status or "closed"
            ep["stored"] = True
            if source is not None:
                pool = recognized_revenue(period, source)
                allocated = allocated_out(period, source)
                ep["pool"] = pool
                ep["allocated"] = allocated
                ep["unallocated"] = pool - allocated
            else:
                ep["pool"] = ep["allocated"] = ep["unallocated"] = 0
        elif ep["start_ts"] <= now_ts <= ep["end_ts"]:
            ep["status"] = "open"
            ep["stored"] = False
            ep["pool"] = ep["allocated"] = ep["unallocated"] = 0
        elif ep["end_ts"] < now_ts:
            ep["status"] = "closed"
            ep["stored"] = False
            ep["pool"] = ep["allocated"] = ep["unallocated"] = 0
        else:
            ep["status"] = "future"
            ep["stored"] = False
            ep["pool"] = ep["allocated"] = ep["unallocated"] = 0
        ep["is_current"] = ep["start_ts"] <= now_ts <= ep["end_ts"]
        epochs.append(ep)
        ts = _step_epoch_start(ep["start_ts"], cfg, 1)

    current_id = _epoch_at_ts(now_ts, cfg)["id"]
    return {
        "now_ts": now_ts,
        "center_ts": center_ts,
        "current_period": current_id,
        "epoch_length": cfg.epoch_length or "monthly",
        "epoch_minutes": int(cfg.epoch_minutes or 0),
        "currency": treasury_currency(),
        "epochs": epochs,
    }


def budgets_for_period(period_id: str = None) -> dict:
    """Planned vs actual allocation budgets for one epoch."""
    from ggg import Budget, FiscalPeriod

    ensure_epoch_periods()
    period_id = (period_id or "").strip() or epoch_id_for(_today())
    period = FiscalPeriod[period_id]
    if period is None:
        return {"error": f"Fiscal period '{period_id}' not found"}

    rows = []
    for budget in Budget.instances():
        if budget.fiscal_period != period:
            continue
        rows.append(
            {
                "id": budget.id,
                "fund": budget.fund.code if budget.fund else "?",
                "category": budget.category or "",
                "budget_type": budget.budget_type or "",
                "planned": int(budget.planned_amount or 0),
                "actual": int(budget.actual_amount or 0),
                "variance": budget.variance(),
                "status": budget.status or "",
            }
        )
    return {"period": period_id, "budgets": sorted(rows, key=lambda r: r["fund"])}


# ---------------------------------------------------------------------------
# Governance (vote-gated actions — mirrors core.payroll)
# ---------------------------------------------------------------------------


def describe_treasury_action(action: dict) -> str:
    """One-line human summary for proposal titles and audit logs."""
    kind = action.get("kind")
    if kind == "set_rule":
        parts = ", ".join(
            f"{r.get('fund')}: {int(r.get('percent_bp', 0)) / 100:g}%"
            for r in action.get("rules", [])
        )
        return f"Adopt treasury allocation rule ({parts})"
    if kind == "run_allocation":
        return f"Run treasury allocation for epoch {action.get('period', 'current')}"
    if kind == "set_epoch":
        length = action.get("epoch_length", "?")
        if length == "minutes":
            mins = action.get("epoch_minutes", "?")
            return f"Set treasury epoch length to {mins} minutes"
        return f"Set treasury epoch length to {length}"
    if kind == "set_schedule":
        state = "Enable" if action.get("enabled") else "Disable"
        auto = " with automatic allocation" if action.get("auto_allocate") else ""
        return f"{state} the treasury schedule{auto}"
    if kind == "issue_report":
        if action.get("as_of"):
            return f"Issue financial report as of {action.get('as_of')}"
        period = action.get("period") or "current"
        return f"Issue financial report for period {period}"
    return f"Treasury action {kind or '?'}"


def apply_treasury_action(action: dict) -> dict:
    """Execute a treasury action dict (direct path and proposal path)."""
    kind = action.get("kind")
    by = action.get("triggered_by", "proposal")
    if kind == "set_rule":
        return set_allocation_rule(
            action.get("rules") or [],
            description=action.get("description", ""),
            triggered_by=by,
        )
    if kind == "run_allocation":
        return run_allocation(action.get("period"), triggered_by=by)
    if kind == "set_epoch":
        return set_epoch_config(
            action.get("epoch_length", ""),
            anchor_month=action.get("anchor_month"),
            epoch_minutes=action.get("epoch_minutes"),
            triggered_by=by,
        )
    if kind == "set_schedule":
        return set_treasury_schedule(
            bool(action.get("enabled")),
            auto_allocate=action.get("auto_allocate"),
            triggered_by=by,
        )
    if kind == "issue_report":
        from core.financial_reports import issue_as_of, issue_period_report
        from ggg import FinancialReport

        as_of = str(action.get("as_of") or "").strip()
        period = str(action.get("period") or "").strip()
        if as_of:
            return issue_as_of(
                as_of=as_of, issued_by=by, period_id=period or None
            )
        if period:
            base_id = f"FR-{period}"
            restate = FinancialReport[base_id] is not None
            return issue_period_report(period, issued_by=by, restate=restate)
        return {"error": "issue_report requires 'as_of' or 'period'"}
    return {"error": f"Unknown treasury action '{kind}'"}


def build_treasury_proposal_code(action: dict) -> str:
    """Inline proposal code that applies the action after the vote passes."""
    payload = json.dumps(action, sort_keys=True)
    return (
        "import json\n"
        "from core.treasury_allocation import apply_treasury_action\n"
        "\n"
        "def main():\n"
        f"    action = json.loads({payload!r})\n"
        "    result = apply_treasury_action(action)\n"
        '    if result.get("error"):\n'
        "        raise RuntimeError(f\"Treasury action failed: {result.get('error')}\")\n"
        "    return result\n"
    )
