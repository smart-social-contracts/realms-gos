import time
from datetime import datetime
from typing import Optional

from ic_python_db import Entity, ManyToOne, OneToMany, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.appeal")


def _now_dt() -> datetime:
    """Current time as a datetime, canister-safe (datetime has no utcnow();
    use time.time() like the DB layer's SystemTime clock)."""
    try:
        return datetime.fromtimestamp(time.time())
    except Exception:
        return datetime.fromtimestamp(0)


class AppealStatus:
    FILED = "filed"
    UNDER_REVIEW = "under_review"
    GRANTED = "granted"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"


class Appeal(Entity, TimestampedMixin):
    """
    An Appeal allows for Case review by a higher Court.
    
    Appeals are filed against a Verdict and are heard by an
    appellate Court. The Appeal decision may uphold, reverse,
    or modify the original Verdict.
    """
    
    __alias__ = "id"
    id = String(max_length=64)
    grounds = String(max_length=4096)  # Legal grounds for appeal
    status = String(max_length=STATUS_MAX_LENGTH)
    filed_date = String(max_length=64)  # ISO format
    decided_date = String(max_length=64)  # ISO format
    decision = String(max_length=256)  # upheld, reversed, modified, remanded
    decision_reasoning = String(max_length=4096)
    original_case = ManyToOne("Case", "appeals")
    original_verdict = OneToOne("Verdict", "appeal")
    appellate_court = ManyToOne("Court", "appeals_received")
    appellant = ManyToOne("User", "appeals_filed")
    new_verdict = OneToOne("Verdict", "appeal_result")
    ledger_entries = OneToMany("LedgerEntry", "appeal")
    metadata = String(max_length=2048)

    def __repr__(self):
        return f"Appeal(id={self.id!r}, status={self.status!r})"

    def is_pending(self) -> bool:
        """Check if this Appeal is still pending decision."""
        return self.status in (AppealStatus.FILED, AppealStatus.UNDER_REVIEW)

    def was_granted(self) -> bool:
        """Check if this Appeal was granted."""
        return self.status == AppealStatus.GRANTED

    @staticmethod
    def appeal_filed_posthook(appeal: "Appeal") -> None:
        """Hook called after Appeal is filed. Override for fee collection, notifications."""
        logger.info(f"Appeal {appeal.id} filed")

    @staticmethod
    def appeal_decided_posthook(appeal: "Appeal") -> None:
        """Hook called after Appeal decision. Override to handle case reopening."""
        logger.info(f"Appeal {appeal.id} decided: {appeal.decision}")


def appeal_file(
    case: "Case",
    appellate_court: Optional["Court"],
    appellant: "User",
    grounds: str,
    appeal_id: Optional[str] = None,
    metadata: Optional[str] = None
) -> "Appeal":
    """
    File an Appeal to a higher Court.

    Args:
        case: The original Case being appealed
        appellate_court: The Court to hear the appeal, or None for a
            courtless appeal (small realms may have no appellate court;
            the core API treats the court as optional)
        appellant: The User filing the appeal (plaintiff or defendant)
        grounds: Legal grounds for the appeal
        appeal_id: Optional custom appeal ID
        metadata: Optional JSON metadata

    Returns:
        The created Appeal
    """
    from .case import CaseStatus

    if not case.can_appeal():
        raise ValueError(f"Case {case.case_number} cannot be appealed (status: {case.status})")

    if appellate_court is not None:
        if not appellate_court.can_hear_appeal():
            raise ValueError(f"Court {appellate_court.name} cannot hear appeals (level: {appellate_court.level})")

        if not appellate_court.is_active():
            raise ValueError(f"Court {appellate_court.name} is not active")
    
    if not appeal_id:
        import uuid
        appeal_id = f"APL-{uuid.uuid4().hex[:12].upper()}"
    
    appeal = Appeal(
        id=appeal_id,
        grounds=grounds,
        status=AppealStatus.FILED,
        filed_date=_now_dt().isoformat(),
        original_case=case,
        original_verdict=case.verdict,
        appellate_court=appellate_court,
        appellant=appellant,
        metadata=metadata or ""
    )
    
    # Update original case status
    case.status = CaseStatus.APPEALED
    
    Appeal.appeal_filed_posthook(appeal)
    logger.info(f"Appeal {appeal_id} filed for case {case.case_number}")
    return appeal


def appeal_decide(
    appeal: "Appeal",
    decision: str,
    reasoning: str,
    new_verdict_data: Optional[dict] = None
) -> "Appeal":
    """
    Decide on an Appeal.
    
    Args:
        appeal: The Appeal to decide
        decision: upheld, reversed, modified, remanded
        reasoning: Legal reasoning for the decision
        new_verdict_data: Optional dict for new Verdict if reversed/modified
        
    Returns:
        The updated Appeal
    """
    from .verdict import Verdict
    
    if not appeal.is_pending():
        raise ValueError(f"Cannot decide appeal in status {appeal.status}")
    
    appeal.decision = decision
    appeal.decision_reasoning = reasoning
    appeal.decided_date = _now_dt().isoformat()
    
    if decision in ("upheld", "denied"):
        appeal.status = AppealStatus.DENIED
        _set_case_after_appeal(appeal, granted=False)
    elif decision == "withdrawn":
        appeal.status = AppealStatus.WITHDRAWN
        _set_case_after_appeal(appeal, granted=False)
    elif decision in ("reversed", "modified", "remanded"):
        appeal.status = AppealStatus.GRANTED
        _set_case_after_appeal(appeal, granted=True)

        # Create new verdict if data provided
        if new_verdict_data:
            new_verdict = Verdict(
                decision=new_verdict_data.get("decision", ""),
                reasoning=new_verdict_data.get("reasoning", ""),
                issued_date=_now_dt().isoformat(),
                case=appeal.original_case
            )
            appeal.new_verdict = new_verdict

    Appeal.appeal_decided_posthook(appeal)
    return appeal


def _set_case_after_appeal(appeal: "Appeal", granted: bool) -> None:
    """Move the original Case after an appeal is no longer pending.

    Granted: proceedings resume (``in_progress``). Denied or withdrawn: the
    verdict is final, so the Case enters ``executing``.
    """
    from .case import CaseStatus

    case = getattr(appeal, "original_case", None)
    if case is None:
        return
    if case.status != CaseStatus.APPEALED:
        return
    case.status = CaseStatus.IN_PROGRESS if granted else CaseStatus.EXECUTING


def appeal_withdraw(appeal: "Appeal") -> "Appeal":
    """Appellant withdraws a pending Appeal; the Case enters executing."""
    if not appeal.is_pending():
        raise ValueError(f"Cannot withdraw appeal in status {appeal.status}")

    appeal.status = AppealStatus.WITHDRAWN
    appeal.decision = "withdrawn"
    appeal.decided_date = _now_dt().isoformat()
    _set_case_after_appeal(appeal, granted=False)

    Appeal.appeal_decided_posthook(appeal)
    logger.info(f"Appeal {appeal.id} withdrawn")
    return appeal
