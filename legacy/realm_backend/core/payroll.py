"""Department payroll settlement (issue #260).

Settles salaries on-chain, one department at a time, as a chunked
``TaskManager`` job — a single IC update message cannot loop over thousands
of ICRC-1 transfers, so each tick processes a bounded batch and the task
disables its own schedule when nothing is left to pay.

Idempotency: one ``Transfer`` per (position, holder, period) with the
deterministic id ``SAL-<position key>-<principal>-<YYYY-MM>``. Re-running a
period skips seats whose transfer is already ``completed``, so neither a
double button press nor a crashed run can pay anyone twice. Failed transfers
are reset to pending when a new run is started, which is how retries work.

Two entry paths, one code path (mirrors ``core.position_admin``):

- **Direct** (governing policy 1/1): access_manager calls
  :func:`start_department_payroll` immediately.
- **Governed** (any other policy): access_manager submits an org-scoped
  Proposal whose inline code (:func:`build_payroll_proposal_code`) calls the
  same function after the vote passes.

Successful settlements record balanced double-entry ledger lines against the
department's fund (``personnel`` expense), which is what Financial Reports
and the access_manager Fund tab read.
"""

import json

from ic_python_logging import get_logger

logger = get_logger("core.payroll")

# Seconds between chunk ticks; also the retry backoff for the next chunk.
PAYROLL_TICK_SECONDS = 5
# ICRC-1 transfers settled per tick (bounded by the per-message limits).
DEFAULT_BATCH_SIZE = 50
# Transfer status meaning "awaiting settlement by the payroll task".
PENDING_STATUS = "recorded"
# Standing-schedule cadence: a cheap daily check, not a monthly timer —
# months vary in length and IC timers are lost on upgrade; a daily idempotent
# check self-heals both.
PAYROLL_SCHEDULE_TICK_SECONDS = 86_400
# Paydays are capped at 28 so every month has one.
MAX_PAYDAY = 28


def _task_name(department_name: str) -> str:
    return f"payroll_{department_name}"


def _schedule_task_name(department_name: str) -> str:
    return f"payroll_schedule_{department_name}"


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


def current_period() -> str:
    """Current payroll period as ``YYYY-MM``.

    Uses integer date math (core.treasury_allocation.civil_from_ts) because
    the canister's frozen ``datetime.fromtimestamp`` always returns 1970.
    """
    from core.treasury_allocation import civil_from_ts

    year, month, _ = civil_from_ts(_now_ts())
    return f"{year:04d}-{month:02d}"


def current_day_of_month() -> int:
    from core.treasury_allocation import civil_from_ts

    return civil_from_ts(_now_ts())[2]


def payroll_currency() -> str:
    """The realm's accounting currency, empty until a treasury ledger resolves."""
    from core.realm_currency import realm_currency

    return realm_currency()


def salary_transfer_id(position_key: str, principal: str, period: str) -> str:
    """Deterministic, period-based transfer id — the idempotency key."""
    return f"SAL-{position_key}-{principal}-{period}"


def payment_items(department_name: str) -> list:
    """One payment item per filled salaried seat, in deterministic order.

    Derived from Positions/Appointments on every call so a chunk never needs
    a separate cursor: idempotent transfer ids make skipping settled seats
    cheap (alias lookups).
    """
    from ggg import Position

    items = []
    positions = sorted(
        Position.for_department(department_name), key=lambda p: p.key or ""
    )
    for pos in positions:
        salary = int(pos.salary_amount or 0)
        if salary <= 0:
            continue
        holders = []
        for appointment in pos.active_appointments():
            holder = appointment.user
            if holder is None:
                continue
            principal = str(getattr(holder, "id", "") or "")
            if principal:
                holders.append(principal)
        for principal in sorted(holders):
            items.append(
                {"position": pos.key, "principal": principal, "amount": salary}
            )
    return items


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

def start_department_payroll(
    department_name: str,
    period: str = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    triggered_by: str = "",
) -> dict:
    """Start (or resume) an on-chain payroll run for one department.

    Creates one pending ``Transfer`` per unsettled seat (resetting previously
    failed ones so they are retried) and seeds the recurring chunk task that
    settles them. Safe to call repeatedly for the same period.
    """
    from ggg import Department, Transfer

    dept = Department[department_name]
    if not dept:
        return {"error": f"Department '{department_name}' not found"}

    fund = dept.fund
    if fund is None:
        return {
            "error": (
                f"Department '{department_name}' has no linked fund — "
                "set a fund code in Policy & budget first"
            )
        }

    period = (period or current_period()).strip()
    items = payment_items(department_name)
    if not items:
        return {"error": f"No filled salaried positions in '{department_name}'"}

    currency = payroll_currency()
    if not currency:
        from core.realm_currency import no_treasury_token_error

        return no_treasury_token_error()
    fund_code = fund.code or "treasury"
    scheduled = 0
    already_settled = 0
    retried = 0
    for item in items:
        transfer_id = salary_transfer_id(item["position"], item["principal"], period)
        transfer = Transfer[transfer_id]
        if transfer is None:
            Transfer(
                id=transfer_id,
                principal_from=fund_code,
                principal_to=item["principal"],
                instrument=currency,
                amount=item["amount"],
                timestamp="",
                tags=f"salary,payroll:{department_name}:{period}",
                status=PENDING_STATUS,
            )
            scheduled += 1
        elif transfer.status == "completed":
            already_settled += 1
        else:
            # failed or stale (e.g. trapped mid-execution) — retry this run
            if transfer.status not in (PENDING_STATUS,):
                retried += 1
            transfer.status = PENDING_STATUS
            scheduled += 1

    result = {
        "success": True,
        "department": department_name,
        "period": period,
        "total_items": len(items),
        "scheduled": scheduled,
        "already_settled": already_settled,
        "retried": retried,
        "task": _task_name(department_name),
        "triggered_by": triggered_by,
    }

    if scheduled == 0:
        result["message"] = "All salaries for this period are already settled"
        return result

    from core.quarter_bootstrap import seed_recurring_codex_task

    seed_recurring_codex_task(
        _task_name(department_name),
        _chunk_step_code(department_name, period, batch_size),
        PAYROLL_TICK_SECONDS,
    )
    logger.info(
        f"Payroll run started for '{department_name}' period {period}: "
        f"{scheduled} payment(s) scheduled, {already_settled} already settled "
        f"(triggered by {triggered_by or 'unknown'})"
    )
    return result


def _chunk_step_code(department_name: str, period: str, batch_size: int) -> str:
    """Tiny stable codex shim run by the recurring TaskManager step.

    All real work is the native :func:`process_payroll_chunk` generator; the
    shim only forwards the run parameters (same pattern as quarter bootstrap).
    """
    payload = json.dumps(
        {
            "department": department_name,
            "period": period,
            "batch_size": int(batch_size),
        },
        sort_keys=True,
    )
    return (
        "import json\n"
        "\n"
        "def async_task():\n"
        f"    args = json.loads({payload!r})\n"
        "    from core.payroll import process_payroll_chunk\n"
        "    result = yield from process_payroll_chunk(\n"
        '        args["department"], args["period"], args["batch_size"]\n'
        "    )\n"
        "    return json.dumps(result)\n"
    )


def process_payroll_chunk(
    department_name: str, period: str, batch_size: int = DEFAULT_BATCH_SIZE
):
    """Generator: settle up to *batch_size* pending salary transfers.

    Driven by the TaskManager timer callback (``yield from``). When nothing
    pending remains the task's schedule is disabled, ending the run.
    """
    from ggg import Department, Transfer

    dept = Department[department_name]
    fund = dept.fund if dept else None

    processed = 0
    completed = 0
    failed = 0
    remaining = 0
    for item in payment_items(department_name):
        transfer_id = salary_transfer_id(item["position"], item["principal"], period)
        transfer = Transfer[transfer_id]
        if transfer is None or transfer.status != PENDING_STATUS:
            continue
        if processed >= batch_size:
            remaining += 1
            continue
        processed += 1
        result = yield from _settle_one(transfer, fund, item["position"])
        if isinstance(result, dict) and "ok" in result:
            completed += 1
        else:
            failed += 1

    if remaining == 0:
        _finish_task(_task_name(department_name))

    summary = {
        "department": department_name,
        "period": period,
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "remaining": remaining,
        "done": remaining == 0,
    }
    logger.info(f"Payroll chunk: {summary}")
    return summary


def _settle_one(transfer, fund, position_key: str):
    """Generator: execute one ICRC-1 salary transfer and book it."""
    try:
        from _cdk import ic

        transfer.timestamp = str(int(ic.time()) // 1_000_000_000)
    except Exception:
        pass

    try:
        result = yield from transfer.execute()
    except Exception as e:
        transfer.status = "failed"
        logger.error(f"Payroll settlement failed for {transfer.id}: {e}")
        return {"err": str(e)}

    if isinstance(result, dict) and "ok" in result:
        try:
            # A codex may have pre-recorded this salary at the beta baseline
            # (same deterministic id) — never book the expense twice.
            if not list(transfer.ledger_entries):
                transfer.record_accounting(
                    fund=fund,
                    expense_category="personnel",
                    description=f"Salary — {position_key}",
                )
        except Exception as e:
            logger.error(f"Payroll accounting failed for {transfer.id}: {e}")
    return result


def _finish_task(task_name: str):
    """Disable the payroll task's schedule — the run is complete."""
    try:
        from ggg import Task

        for task in Task.instances():
            if task.name == task_name:
                for schedule in task.schedules:
                    schedule.disabled = True
                logger.info(f"Payroll task '{task_name}' finished; schedule disabled")
    except Exception as e:
        logger.warning(f"Could not disable payroll task '{task_name}': {e}")


# ---------------------------------------------------------------------------
# Standing schedule (automatic payroll)
# ---------------------------------------------------------------------------

def set_payroll_schedule(
    department_name: str,
    enabled: bool,
    payday: int = 1,
    triggered_by: str = "",
) -> dict:
    """Enable or disable automatic monthly payroll for one department.

    Enabling seeds a small recurring task that ticks daily and, once the
    configured payday is reached, starts the (idempotent) payroll run for the
    current period. Disabling just switches the task's schedule off.
    """
    from ggg import Department

    dept = Department[department_name]
    if not dept:
        return {"error": f"Department '{department_name}' not found"}

    if not enabled:
        _finish_task(_schedule_task_name(department_name))
        logger.info(
            f"Automatic payroll disabled for '{department_name}' "
            f"(triggered by {triggered_by or 'unknown'})"
        )
        return {
            "success": True,
            "department": department_name,
            "schedule_active": False,
        }

    if dept.fund is None:
        return {
            "error": (
                f"Department '{department_name}' has no linked fund — "
                "set a fund code in Policy & budget first"
            )
        }
    payday = min(max(int(payday or 1), 1), MAX_PAYDAY)

    from core.quarter_bootstrap import seed_recurring_codex_task

    seed_recurring_codex_task(
        _schedule_task_name(department_name),
        _schedule_step_code(department_name, payday),
        PAYROLL_SCHEDULE_TICK_SECONDS,
    )
    logger.info(
        f"Automatic payroll enabled for '{department_name}' on day {payday} "
        f"(triggered by {triggered_by or 'unknown'})"
    )
    return {
        "success": True,
        "department": department_name,
        "schedule_active": True,
        "payday": payday,
    }


def _schedule_step_code(department_name: str, payday: int) -> str:
    """Codex shim for the daily automatic-payroll check."""
    payload = json.dumps(
        {"department": department_name, "payday": int(payday)}, sort_keys=True
    )
    return (
        "import json\n"
        "\n"
        "def async_task():\n"
        f"    args = json.loads({payload!r})\n"
        "    from core.payroll import payroll_schedule_tick\n"
        '    result = payroll_schedule_tick(args["department"], args["payday"])\n'
        "    return json.dumps(result)\n"
    )


def payroll_schedule_tick(department_name: str, payday: int) -> dict:
    """Daily check: start this period's payroll once the payday is reached.

    A run is started only when at least one seat has no transfer record for
    the current period, so each seat is paid at most once per month (settled
    seats are skipped by the run itself). Seats whose transfer already exists
    — including failed ones — are left for a manual "Run payroll", so a
    persistently failing transfer is not retried day after day.
    """
    from ggg import Transfer

    period = current_period()
    day = current_day_of_month()
    if day < int(payday):
        return {"period": period, "day": day, "skipped": f"before payday {payday}"}

    items = payment_items(department_name)
    if not items:
        return {"period": period, "skipped": "no filled salaried positions"}

    unstarted = sum(
        1
        for item in items
        if Transfer[salary_transfer_id(item["position"], item["principal"], period)]
        is None
    )
    if unstarted == 0:
        return {"period": period, "skipped": "run already started for this period"}

    result = start_department_payroll(
        department_name, period=period, triggered_by="schedule"
    )
    logger.info(
        f"Automatic payroll tick started run for '{department_name}' "
        f"period {period}: {result}"
    )
    return {"period": period, "started": True, "run": result}


def payroll_schedule_status(department_name: str) -> dict:
    """Whether automatic payroll is on for a department, and its payday."""
    import re

    from ggg import Task

    active = False
    payday = None
    try:
        for task in Task.instances():
            if task.name != _schedule_task_name(department_name):
                continue
            active = any(not s.disabled for s in task.schedules)
            for step in task.steps:
                code = ""
                try:
                    code = step.call.codex.code or ""
                except Exception:
                    pass
                match = re.search(r'"payday": (\d+)', code)
                if match:
                    payday = int(match.group(1))
                    break
            break
    except Exception as e:
        logger.warning(f"payroll_schedule_status({department_name}) failed: {e}")
    return {"schedule_active": active, "schedule_payday": payday}


# ---------------------------------------------------------------------------
# Status (read model for the access_manager Fund tab)
# ---------------------------------------------------------------------------

# Cap the per-seat payment list in status responses; counts stay exact.
STATUS_PAYMENTS_LIMIT = 100


def payroll_status(department_name: str, period: str = None) -> dict:
    """Current payroll picture for one department and period."""
    from ggg import Department, Task, Transfer

    dept = Department[department_name]
    if not dept:
        return {"error": f"Department '{department_name}' not found"}

    period = (period or current_period()).strip()
    items = payment_items(department_name)

    counts = {"not_started": 0, "pending": 0, "completed": 0, "failed": 0}
    settled_total = 0
    unsettled_total = 0
    payments = []
    for item in items:
        transfer_id = salary_transfer_id(item["position"], item["principal"], period)
        transfer = Transfer[transfer_id]
        raw_status = transfer.status if transfer else "not_started"
        if raw_status == "completed":
            status = "completed"
            settled_total += item["amount"]
        elif raw_status == "failed":
            status = "failed"
            unsettled_total += item["amount"]
        elif transfer is None:
            status = "not_started"
            unsettled_total += item["amount"]
        else:  # recorded / executing / aborted → awaiting (re)settlement
            status = "pending"
            unsettled_total += item["amount"]
        counts[status] += 1
        if len(payments) < STATUS_PAYMENTS_LIMIT:
            payments.append(
                {
                    "position": item["position"],
                    "principal": item["principal"],
                    "amount": item["amount"],
                    "status": status,
                }
            )

    task_status = ""
    task_active = False
    try:
        for task in Task.instances():
            if task.name == _task_name(department_name):
                task_status = str(task.status or "")
                task_active = any(not s.disabled for s in task.schedules)
                break
    except Exception:
        pass

    status = {
        "department": department_name,
        "period": period,
        "fund_code": (dept.fund.code if dept.fund else "") or "",
        "currency": payroll_currency(),
        "total_seats": len(items),
        "total_amount": settled_total + unsettled_total,
        "settled_amount": settled_total,
        "unsettled_amount": unsettled_total,
        "counts": counts,
        "run_active": task_active,
        "task_status": task_status,
        "payments": payments,
        "payments_truncated": len(items) > len(payments),
    }
    status.update(payroll_schedule_status(department_name))
    return status


# ---------------------------------------------------------------------------
# Governance (vote-gated trigger — mirrors core.position_admin)
# ---------------------------------------------------------------------------

def describe_payroll_action(action: dict) -> str:
    """One-line human summary for proposal titles and audit logs."""
    if action.get("kind") == "schedule":
        state = "Enable" if action.get("enabled") else "Disable"
        suffix = (
            f" (monthly on day {action.get('payday', 1)})"
            if action.get("enabled")
            else ""
        )
        return (
            f"{state} automatic payroll for department "
            f"'{action.get('department', '?')}'{suffix}"
        )
    return (
        f"Run payroll for department '{action.get('department', '?')}' "
        f"(period {action.get('period', '?')})"
    )


def build_payroll_proposal_code(action: dict) -> str:
    """Inline proposal code that starts the payroll run after the vote passes.

    Executed by the voting extension on acceptance; raising on failure marks
    the proposal 'failed' instead of silently succeeding.
    """
    payload = json.dumps(action, sort_keys=True)
    return (
        "import json\n"
        "from core.payroll import start_department_payroll\n"
        "\n"
        "def main():\n"
        f"    action = json.loads({payload!r})\n"
        "    result = start_department_payroll(\n"
        '        action["department"],\n'
        '        period=action.get("period"),\n'
        '        triggered_by=action.get("triggered_by", "proposal"),\n'
        "    )\n"
        '    if result.get("error"):\n'
        "        raise RuntimeError(f\"Payroll start failed: {result.get('error')}\")\n"
        "    return result\n"
    )


def build_schedule_proposal_code(action: dict) -> str:
    """Inline proposal code that flips automatic payroll after the vote."""
    payload = json.dumps(action, sort_keys=True)
    return (
        "import json\n"
        "from core.payroll import set_payroll_schedule\n"
        "\n"
        "def main():\n"
        f"    action = json.loads({payload!r})\n"
        "    result = set_payroll_schedule(\n"
        '        action["department"],\n'
        '        bool(action.get("enabled")),\n'
        '        payday=int(action.get("payday", 1)),\n'
        '        triggered_by=action.get("triggered_by", "proposal"),\n'
        "    )\n"
        '    if result.get("error"):\n'
        "        raise RuntimeError(\n"
        "            f\"Payroll schedule change failed: {result.get('error')}\"\n"
        "        )\n"
        "    return result\n"
    )
