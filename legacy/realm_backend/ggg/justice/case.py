import time
from datetime import datetime
from typing import Optional

from ic_python_db import Entity, ManyToMany, ManyToOne, OneToMany, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.case")


def _now_dt() -> datetime:
    """Current time as a datetime, canister-safe.

    The on-chain Python runtime's ``datetime`` has no ``utcnow()``. The DB
    layer's ``SystemTime`` clock relies on ``time.time()`` here, so we do the
    same and build the datetime via ``fromtimestamp()`` (also used on-chain in
    ``SystemTime.format_timestamp``).
    """
    try:
        return datetime.fromtimestamp(time.time())
    except Exception:
        return datetime.fromtimestamp(0)


class CaseStatus:
    FILED = "filed"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    VERDICT_ISSUED = "verdict_issued"
    APPEALED = "appealed"
    EXECUTING = "executing"
    TRANSFERRED = "transferred"
    CLOSED = "closed"
    DISMISSED = "dismissed"


class Case(Entity, TimestampedMixin):
    """
    A legal Case filed at a Court.
    
    Cases involve a plaintiff and defendant, are assigned to Judges,
    and result in a Verdict. Cases can be appealed to higher Courts.
    
    Lifecycle:
    1. filed → Case created, fees collected
    2. assigned → Judges assigned to case
    3. in_progress → Proceedings underway
    4. verdict_issued → Verdict rendered (appeal still possible)
    5. appealed → An appeal is pending
    6. executing → Verdict is final; penalties may run
    7. closed → Penalties resolved (or dismissed)

    A judge may freeze a pre-verdict docket as transferred (dest pointer in
    ``metadata`` only — Case is not an EntityMigration subject). Transferred
    is not open: no verdicts, appeals, or penalties on this record.
    """
    
    __alias__ = "case_number"
    case_number = String(max_length=64)
    title = String(max_length=256)
    description = String(max_length=4096)
    status = String(max_length=STATUS_MAX_LENGTH)
    filed_date = String(max_length=64)  # ISO format
    closed_date = String(max_length=64)  # ISO format
    court = ManyToOne("Court", "cases")
    plaintiff = ManyToOne("User", "cases_as_plaintiff")
    defendant = ManyToOne("User", "cases_as_defendant")
    judges = ManyToMany(["Judge"], "cases_assigned")
    verdict = OneToOne("Verdict", "case")
    appeals = OneToMany("Appeal", "original_case")
    ledger_entries = OneToMany("LedgerEntry", "case")
    metadata = String(max_length=2048)

    def __repr__(self):
        return f"Case(case_number={self.case_number!r}, status={self.status!r})"

    def is_open(self) -> bool:
        """Check if this Case is still open.

        ``executing`` stays open (penalties may still run). ``transferred``
        is a frozen origin docket and is not open.
        """
        return self.status not in (
            CaseStatus.CLOSED,
            CaseStatus.DISMISSED,
            CaseStatus.TRANSFERRED,
        )

    def get_judges(self) -> list:
        """Get all Judges assigned to this Case."""
        return list(self.judges) if self.judges else []

    def has_verdict(self) -> bool:
        """Check if this Case has a Verdict."""
        return self.verdict is not None

    def can_appeal(self) -> bool:
        """Check if this Case can be appealed."""
        return self.status == CaseStatus.VERDICT_ISSUED

    def record_accounting(
        self,
        fund: Optional["Fund"] = None,
        fiscal_period: Optional["FiscalPeriod"] = None,
        description: Optional[str] = None
    ) -> list:
        """
        Record accounting entries for case filing fees.
        
        Creates balanced double-entry LedgerEntry records.
        Override via accounting_hook for custom logic.
        """
        custom_entries = Case.accounting_hook(
            case=self,
            event="filed",
            fund=fund,
            fiscal_period=fiscal_period,
            description=description
        )
        if custom_entries is not None:
            return custom_entries
        
        # Default: no automatic ledger entries (override via Codex)
        logger.info(f"Case {self.case_number} filed - no default accounting entries")
        return []

    @staticmethod
    def accounting_hook(
        case: "Case" = None,
        event: str = None,
        fund=None,
        fiscal_period=None,
        description=None
    ):
        """
        Hook for custom accounting logic. Override via Codex.
        
        Args:
            case: The Case instance
            event: 'filed', 'closed', 'verdict_issued'
            
        Return None for default logic, or list of LedgerEntry for custom.
        """
        return None

    @staticmethod
    def case_filed_posthook(case: "Case") -> None:
        """Hook called after Case is filed. Override for notifications, fee collection."""
        logger.info(f"Case {case.case_number} filed: {case.title}")

    @staticmethod
    def case_assigned_posthook(case: "Case", judges: list) -> None:
        """Hook called after Judges are assigned. Override for conflict checks."""
        logger.info(f"Case {case.case_number} assigned to {len(judges)} judge(s)")

    @staticmethod
    def case_closed_posthook(case: "Case") -> None:
        """Hook called after Case is closed. Override for cleanup."""
        logger.info(f"Case {case.case_number} closed")


def case_file(
    court: "Court",
    plaintiff: "User",
    defendant: "User",
    title: str,
    description: str,
    case_number: Optional[str] = None,
    metadata: Optional[str] = None
) -> "Case":
    """
    File a new Case at a Court.
    
    Args:
        court: The Court to file at
        plaintiff: The User filing the case
        defendant: The User being sued
        title: Case title
        description: Case description
        case_number: Optional custom case number (auto-generated if not provided)
        metadata: Optional JSON metadata
        
    Returns:
        The created Case
    """
    if not court.is_active():
        raise ValueError(f"Court {court.name} is not active")
    
    if not case_number:
        # Generate case number: COURT-YYYY-NNNN
        import uuid
        year = _now_dt().year
        case_number = f"{court.name[:3].upper()}-{year}-{uuid.uuid4().hex[:8].upper()}"
    
    case = Case(
        case_number=case_number,
        title=title,
        description=description,
        status=CaseStatus.FILED,
        filed_date=_now_dt().isoformat(),
        court=court,
        plaintiff=plaintiff,
        defendant=defendant,
        metadata=metadata or ""
    )
    
    Case.case_filed_posthook(case)
    logger.info(f"Case {case_number} filed at {court.name}")
    return case


def case_assign_judges(case: "Case", judges: list) -> "Case":
    """
    Assign Judges to a Case.
    
    Args:
        case: The Case to assign judges to
        judges: List of Judge instances
        
    Returns:
        The updated Case
    """
    from .judge import Judge
    
    if case.status not in (CaseStatus.FILED, CaseStatus.ASSIGNED):
        raise ValueError(f"Cannot assign judges to case in status {case.status}")
    
    for judge in judges:
        # Check for conflicts of interest
        if not Judge.judge_conflict_check_hook(judge, case):
            raise ValueError(f"Judge {judge.id} has conflict of interest")
        if not judge.is_active():
            raise ValueError(f"Judge {judge.id} is not active")
        case.judges.add(judge)
    
    case.status = CaseStatus.ASSIGNED
    Case.case_assigned_posthook(case, judges)
    return case


def case_issue_verdict(
    case: "Case",
    decision: str,
    reasoning: str,
    penalties: Optional[list] = None
) -> "Verdict":
    """
    Issue a Verdict for a Case.
    
    Args:
        case: The Case to issue verdict for
        decision: The verdict decision
        reasoning: Legal reasoning for the decision
        penalties: Optional list of Penalty dicts
        
    Returns:
        The created Verdict
    """
    from .verdict import Verdict, verdict_prehook, verdict_posthook
    from .penalty import Penalty
    
    if case.status not in (CaseStatus.ASSIGNED, CaseStatus.IN_PROGRESS):
        raise ValueError(f"Cannot issue verdict for case in status {case.status}")
    
    # Pre-hook check
    if not verdict_prehook(case, decision, penalties or []):
        raise ValueError("Verdict blocked by prehook")
    
    verdict = Verdict(
        decision=decision,
        reasoning=reasoning,
        issued_date=_now_dt().isoformat(),
        case=case,
        issued_by=case.judges[0] if case.judges else None  # Primary judge
    )
    
    # Create penalties if provided
    if penalties:
        for p_data in penalties:
            penalty = Penalty(
                penalty_type=p_data.get("type", "fine"),
                amount=p_data.get("amount", 0),
                description=p_data.get("description", ""),
                status="pending",
                verdict=verdict,
                target_user=case.defendant
            )
    
    case.status = CaseStatus.VERDICT_ISSUED
    case.verdict = verdict
    
    verdict_posthook(verdict)
    logger.info(f"Verdict issued for case {case.case_number}")
    return verdict


def _parse_metadata(case: "Case") -> dict:
    raw = getattr(case, "metadata", None) or ""
    if not raw:
        return {}
    try:
        import json
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": raw}
    except (TypeError, ValueError):
        return {"_raw": raw}


def _write_metadata(case: "Case", data: dict) -> None:
    import json
    case.metadata = json.dumps(data)


def case_penalties(case: "Case") -> list:
    """Penalties hanging off this Case's current Verdict."""
    verdict = getattr(case, "verdict", None)
    if verdict is None:
        return []
    try:
        return list(getattr(verdict, "penalties", None) or [])
    except Exception:
        return []


def case_penalties_resolved(case: "Case") -> bool:
    """True when every penalty is executed or waived (or there are none)."""
    penalties = case_penalties(case)
    if not penalties:
        return True
    return all(getattr(p, "status", None) in ("executed", "waived") for p in penalties)


def case_transfer(case: "Case", dest=None) -> "Case":
    """Freeze the origin docket as transferred.

    Dest lives in ``Case.metadata`` (``transfer_dest``). Case is immutable
    across canisters — do not create an EntityMigration for it. This function
    does not send a federation message or open a remote case.
    """
    if case.status not in (
        CaseStatus.FILED,
        CaseStatus.ASSIGNED,
        CaseStatus.IN_PROGRESS,
    ):
        raise ValueError(f"Cannot transfer case in status {case.status}")

    if getattr(case, "verdict", None) is not None:
        raise ValueError("Cannot transfer a case that already has a verdict")

    pointer = dest if isinstance(dest, dict) else ({"id": dest} if dest else {})
    data = _parse_metadata(case)
    data["transferred"] = True
    data["transfer_dest"] = pointer
    data["transferred_at"] = _now_dt().isoformat()
    _write_metadata(case, data)

    case.status = CaseStatus.TRANSFERRED
    logger.info(f"Case {case.case_number} marked transferred (dest={pointer})")
    return case


def case_begin_executing(case: "Case") -> "Case":
    """Mark a verdict final and open the penalty window.

    Explicit and testable: there is no appeal-window clock. Call this when
    the verdict cannot be appealed, nobody appealed, or the window is closed.
    """
    if case.status != CaseStatus.VERDICT_ISSUED:
        raise ValueError(
            f"Cannot begin executing case in status {case.status}; "
            "only verdict_issued may become executing this way"
        )
    case.status = CaseStatus.EXECUTING
    logger.info(f"Case {case.case_number} entered executing (verdict is final)")
    return case


def case_close(case: "Case") -> "Case":
    """Close a Case after the verdict is final and penalties are resolved.

    Only ``executing`` may close. Pending penalties block close; call
    ``penalty_execute`` / ``penalty_waive`` first (those auto-close when
    the last pending penalty is resolved).
    """
    if case.status != CaseStatus.EXECUTING:
        raise ValueError(f"Cannot close case in status {case.status}")

    if not case_penalties_resolved(case):
        raise ValueError(
            f"Cannot close case {case.case_number} while penalties are pending"
        )

    case.status = CaseStatus.CLOSED
    case.closed_date = _now_dt().isoformat()

    Case.case_closed_posthook(case)
    return case
