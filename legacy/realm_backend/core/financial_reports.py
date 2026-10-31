"""Compile and issue frozen financial report snapshots."""

import hashlib
import json

from ic_python_logging import get_logger

logger = get_logger("core.financial_reports")


def _now_iso() -> str:
    from core.treasury_allocation import _iso, _today

    return _iso(_today())


def _issued_at_iso() -> str:
    from core.treasury_allocation import _now_ts, _ts_to_iso

    return _ts_to_iso(_now_ts())


def _period_report_id(period_id: str) -> str:
    return f"FR-{period_id}"


def _next_restate_id(period_id: str) -> str:
    prefix = f"FR-{period_id}-R"
    max_n = 0
    from ggg import FinancialReport

    for report in FinancialReport.instances():
        rid = str(report.id or "")
        if rid.startswith(prefix):
            try:
                max_n = max(max_n, int(rid[len(prefix) :]))
            except ValueError:
                pass
    return f"{prefix}{max_n + 1}"


def _superseded_ids() -> set:
    """Report ids pointed at by another report's ``supersedes``."""
    from ggg import FinancialReport

    out = set()
    for report in FinancialReport.instances():
        try:
            prior = report.supersedes
        except Exception:
            prior = None
        if prior is not None:
            out.add(prior.id)
    return out


def latest_report_for_period(period_id: str):
    """Newest non-superseded report for *period_id*, or None."""
    from ggg import FinancialReport, FinancialReportKind

    superseded = _superseded_ids()
    candidates = []
    for report in FinancialReport.instances():
        try:
            period = report.period
        except Exception:
            period = None
        if period is None or period.id != period_id:
            continue
        if report.kind == FinancialReportKind.DRAFT:
            continue
        if report.id in superseded:
            continue
        candidates.append(report)
    if not candidates:
        return None
    return sorted(candidates, key=lambda r: str(r.issued_at or ""))[-1]


def _resolve_period_for_as_of(as_of: str, period_id: str | None = None):
    from ggg import FiscalPeriod

    if period_id:
        return FiscalPeriod[str(period_id).strip()]
    as_of_cmp = str(as_of or "")[:10]
    for period in FiscalPeriod.instances():
        start = str(period.start_date or "")[:10]
        end = str(period.end_date or "")[:10]
        if start and end and start <= as_of_cmp <= end:
            return period
    from core.treasury_allocation import current_epoch_id

    return FiscalPeriod[current_epoch_id()]


def _collect_entries(as_of: str, window_start: str | None) -> list:
    """Ledger rows used by balance sheet (as-of) or income/cash-flow window."""
    from ggg import Category, EntryType, LedgerEntry

    as_of_cmp = str(as_of or "")
    start_cmp = str(window_start or "")[:10] if window_start else None
    used = {}

    def in_window(d: str) -> bool:
        if start_cmp:
            return bool(d) and start_cmp <= d <= as_of_cmp
        return bool(d) and d <= as_of_cmp

    for entry in LedgerEntry.instances():
        d = str(entry.entry_date or "")
        if entry.entry_type in (
            EntryType.ASSET,
            EntryType.LIABILITY,
            EntryType.EQUITY,
        ):
            if d and d <= as_of_cmp:
                used[entry.id] = entry
        if entry.entry_type in (EntryType.REVENUE, EntryType.EXPENSE):
            if in_window(d):
                used[entry.id] = entry
        if entry.category == Category.CASH and in_window(d):
            used[entry.id] = entry
    return list(used.values())


def _source_hash(entries: list) -> str:
    lines = sorted(
        f"{e.id}:{int(e.debit or 0)}:{int(e.credit or 0)}" for e in entries
    )
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compile_statements(
    as_of: str, period=None, window_start: str | None = None
) -> dict:
    """Build statement dict and source hash for a report snapshot."""
    from core.treasury_allocation import treasury_currency
    from ggg import LedgerEntry

    currency = treasury_currency()
    if period is not None and window_start is None:
        window_start = str(period.start_date or "")[:10]

    bs = LedgerEntry.get_balance_sheet(as_of_date=as_of)
    income = LedgerEntry.get_income_statement(
        start_date=window_start, end_date=as_of
    )
    cf_raw = LedgerEntry.get_cash_flow_statement(
        start_date=window_start, end_date=as_of
    )

    entries = _collect_entries(as_of, window_start)
    entry_count = len(entries)
    source_hash = _source_hash(entries)

    statements = {
        "summary": {
            "total_assets": bs["assets"]["total"],
            "total_liabilities": bs["liabilities"]["total"],
            "net_position": bs["net_position"],
            "net_income": income["net_income"],
        },
        "balance_sheet": {
            "assets": bs["assets"],
            "liabilities": bs["liabilities"],
            "equity": bs["fund_balance"],
            "net_position": bs["net_position"],
        },
        "income_statement": {
            "revenues": income["revenues"],
            "expenses": income["expenses"],
            "net_income": income["net_income"],
        },
        "cash_flow": {
            "operating": cf_raw["operating_activities"]["total"],
            "investing": cf_raw["investing_activities"]["total"],
            "financing": cf_raw["financing_activities"]["total"],
            "net_change": cf_raw["net_change_in_cash"],
        },
    }

    return {
        "as_of": as_of,
        "currency": currency,
        "entry_count": entry_count,
        "source_hash": source_hash,
        "statements": statements,
    }


def issue_period_report(
    period_id: str, issued_by: str = "system", restate: bool = False
) -> dict:
    """Issue (or restate) a period-close report for one fiscal period."""
    from ggg import FinancialReport, FinancialReportKind, FiscalPeriod

    period_id = str(period_id or "").strip()
    period = FiscalPeriod[period_id]
    if period is None:
        return {"error": f"Fiscal period '{period_id}' not found"}

    base_id = _period_report_id(period_id)
    existing = FinancialReport[base_id]
    latest = latest_report_for_period(period_id)

    if existing is not None and not restate:
        return {"success": True, "skipped": True, "id": base_id}

    if restate and latest is not None:
        report_id = _next_restate_id(period_id)
        supersedes = latest
    else:
        report_id = base_id
        supersedes = None

    as_of = str(period.end_date or "")[:10]
    compiled = compile_statements(
        as_of, period=period, window_start=str(period.start_date or "")[:10]
    )

    FinancialReport(
        id=report_id,
        kind=FinancialReportKind.PERIOD_CLOSE,
        as_of=as_of,
        period=period,
        issued_at=_issued_at_iso(),
        issued_by=issued_by,
        currency=compiled["currency"],
        statements=json.dumps(compiled["statements"], separators=(",", ":")),
        entry_count=compiled["entry_count"],
        source_hash=compiled["source_hash"],
        supersedes=supersedes,
    )
    logger.info(f"Issued period report {report_id} for {period_id}")
    return {"success": True, "id": report_id, "skipped": False}


def _as_of_report_id(as_of: str) -> str:
    sanitized = str(as_of or "").replace(":", "")
    base = f"FR-asof-{sanitized}"
    from ggg import FinancialReport

    if FinancialReport[base] is None:
        return base
    n = 2
    while FinancialReport[f"{base}-{n}"] is not None:
        n += 1
    return f"{base}-{n}"


def issue_as_of(
    as_of: str | None = None,
    issued_by: str = "system",
    period_id: str | None = None,
) -> dict:
    """Issue a point-in-time report (not tied to period close)."""
    from ggg import FinancialReport, FinancialReportKind

    period = _resolve_period_for_as_of(as_of or "", period_id)
    if as_of:
        as_of_val = str(as_of)
    elif period is not None:
        as_of_val = str(period.end_date or "")[:10]
    else:
        as_of_val = _now_iso()

    if period is None and not period_id:
        period = _resolve_period_for_as_of(as_of_val, None)

    window_start = str(period.start_date or "")[:10] if period else None
    compiled = compile_statements(as_of_val, period=period, window_start=window_start)
    report_id = _as_of_report_id(as_of_val)

    FinancialReport(
        id=report_id,
        kind=FinancialReportKind.AS_OF,
        as_of=as_of_val,
        period=period,
        issued_at=_issued_at_iso(),
        issued_by=issued_by,
        currency=compiled["currency"],
        statements=json.dumps(compiled["statements"], separators=(",", ":")),
        entry_count=compiled["entry_count"],
        source_hash=compiled["source_hash"],
    )
    logger.info(f"Issued as-of report {report_id} at {as_of_val}")
    return {"success": True, "id": report_id, "skipped": False}


def _draft_report_id(issued_at: str) -> str:
    sanitized = str(issued_at or "").replace(":", "")
    base = f"FR-draft-{sanitized}"
    from ggg import FinancialReport

    if FinancialReport[base] is None:
        return base
    n = 2
    while FinancialReport[f"{base}-{n}"] is not None:
        n += 1
    return f"{base}-{n}"


def issue_draft(issued_by: str = "system") -> dict:
    """Freeze current working books as an unofficial draft snapshot."""
    from ggg import FinancialReport, FinancialReportKind

    as_of_val = _now_iso()
    issued_at = _issued_at_iso()
    period = _resolve_period_for_as_of(as_of_val, None)
    window_start = str(period.start_date or "")[:10] if period else None
    compiled = compile_statements(as_of_val, period=period, window_start=window_start)
    report_id = _draft_report_id(issued_at)

    FinancialReport(
        id=report_id,
        kind=FinancialReportKind.DRAFT,
        as_of=as_of_val,
        period=period,
        issued_at=issued_at,
        issued_by=issued_by,
        currency=compiled["currency"],
        statements=json.dumps(compiled["statements"], separators=(",", ":")),
        entry_count=compiled["entry_count"],
        source_hash=compiled["source_hash"],
    )
    logger.info(f"Issued draft report {report_id} at {as_of_val}")
    return {"success": True, "id": report_id, "skipped": False}
