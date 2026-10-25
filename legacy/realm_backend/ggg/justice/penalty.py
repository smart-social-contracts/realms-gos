import time
from datetime import datetime
from typing import Optional

from ic_python_db import Entity, Float, ManyToOne, OneToMany, String, TimestampedMixin
from ic_python_logging import get_logger

from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.penalty")


def _now_dt() -> datetime:
    """Current time as a datetime, canister-safe (datetime has no utcnow();
    use time.time() like the DB layer's SystemTime clock)."""
    try:
        return datetime.fromtimestamp(time.time())
    except Exception:
        return datetime.fromtimestamp(0)


class PenaltyType:
    FINE = "fine"
    RESTITUTION = "restitution"
    COMMUNITY_SERVICE = "community_service"
    SUSPENSION = "suspension"
    REVOCATION = "revocation"
    INJUNCTION = "injunction"
    OTHER = "other"


class Penalty(Entity, TimestampedMixin):
    """
    A sanction or remedy resulting from a Verdict.
    
    Penalties can be financial (fines, restitution) or non-financial
    (community service, license revocation, injunctions).
    
    Penalties have a lifecycle: pending → executed (or waived).
    """
    
    __alias__ = "id"
    id = String(max_length=64)
    penalty_type = String(max_length=32)  # fine, restitution, community_service, etc.
    amount = Float()  # For financial penalties (in native currency/token)
    currency = String(max_length=16, default="")
    description = String(max_length=1024)
    status = String(max_length=STATUS_MAX_LENGTH)  # pending, executed, waived, appealed
    due_date = String(max_length=64)  # ISO format
    executed_date = String(max_length=64)  # ISO format
    verdict = ManyToOne("Verdict", "penalties")
    target_user = ManyToOne("User", "penalties_received")
    ledger_entries = OneToMany("LedgerEntry", "penalty")
    metadata = String(max_length=1024)

    def __repr__(self):
        return f"Penalty(id={self.id!r}, type={self.penalty_type!r}, status={self.status!r})"

    def is_financial(self) -> bool:
        """Check if this is a financial penalty."""
        return self.penalty_type in (PenaltyType.FINE, PenaltyType.RESTITUTION)

    def is_pending(self) -> bool:
        """Check if this Penalty is still pending execution."""
        return self.status == "pending"

    def record_accounting(
        self,
        fund: Optional["Fund"] = None,
        fiscal_period: Optional["FiscalPeriod"] = None,
        description: Optional[str] = None
    ) -> list:
        """
        Record accounting entries for financial penalties.
        
        Creates balanced double-entry LedgerEntry records.
        Override via accounting_hook for custom logic.
        """
        if not self.is_financial():
            return []
        
        custom_entries = Penalty.accounting_hook(
            penalty=self,
            event="executed",
            fund=fund,
            fiscal_period=fiscal_period,
            description=description
        )
        if custom_entries is not None:
            return custom_entries
        
        # Default: no automatic ledger entries (override via Codex)
        logger.info(f"Penalty {self.id} executed - no default accounting entries")
        return []

    @staticmethod
    def accounting_hook(
        penalty: "Penalty" = None,
        event: str = None,
        fund=None,
        fiscal_period=None,
        description=None
    ):
        """
        Hook for custom accounting logic. Override via Codex.
        
        Return None for default logic, or list of LedgerEntry for custom.
        """
        return None

    @staticmethod
    def penalty_execution_prehook(penalty: "Penalty") -> bool:
        """
        Called before Penalty execution.
        Override via Codex to validate or block.
        
        Returns:
            True to proceed, False to block.
        """
        return True

    @staticmethod
    def penalty_executed_posthook(penalty: "Penalty") -> None:
        """
        Called after Penalty execution.
        Override via Codex for ledger entries, notifications.
        """
        logger.info(f"Penalty {penalty.id} executed")


def _case_of_penalty(penalty: "Penalty"):
    verdict = getattr(penalty, "verdict", None)
    return getattr(verdict, "case", None) if verdict is not None else None


def _require_executing_case(penalty: "Penalty", action: str):
    """Penalties run only while the Case is executing.

    ``verdict_issued`` still allows an appeal, so execute/waive is refused
    there. A penalty with no Case is refused rather than treated as an
    exception — production penalties are always attached to a verdict.
    """
    from .case import CaseStatus

    case = _case_of_penalty(penalty)
    if case is None:
        raise ValueError(
            f"Cannot {action} a penalty that is not attached to an executing Case"
        )
    if case.status != CaseStatus.EXECUTING:
        raise ValueError(
            f"Cannot {action} penalty while case is {case.status}; "
            "the case must be executing (verdict is final; appeal is no longer possible)"
        )
    return case


def _maybe_close_case(case) -> None:
    from .case import CaseStatus, case_close, case_penalties_resolved

    if case is None or case.status != CaseStatus.EXECUTING:
        return
    if case_penalties_resolved(case):
        case_close(case)


def penalty_execute(penalty: "Penalty") -> "Penalty":
    """
    Execute a Penalty.
    
    Args:
        penalty: The Penalty to execute
        
    Returns:
        The updated Penalty
    """
    if penalty.status != "pending":
        raise ValueError(f"Cannot execute penalty in status {penalty.status}")

    case = _require_executing_case(penalty, "execute")
    
    if not Penalty.penalty_execution_prehook(penalty):
        raise ValueError("Penalty execution blocked by prehook")
    
    penalty.status = "executed"
    penalty.executed_date = _now_dt().isoformat()
    
    # Record accounting if financial
    if penalty.is_financial():
        penalty.record_accounting()
    
    Penalty.penalty_executed_posthook(penalty)
    _maybe_close_case(case)
    return penalty


def penalty_waive(penalty: "Penalty", reason: str = "") -> "Penalty":
    """
    Waive a Penalty.
    
    Args:
        penalty: The Penalty to waive
        reason: Reason for waiving
        
    Returns:
        The updated Penalty
    """
    if penalty.status != "pending":
        raise ValueError(f"Cannot waive penalty in status {penalty.status}")

    case = _require_executing_case(penalty, "waive")
    
    penalty.status = "waived"
    if reason:
        penalty.metadata = f'{{"waive_reason": "{reason}"}}'
    
    logger.info(f"Penalty {penalty.id} waived: {reason}")
    _maybe_close_case(case)
    return penalty
