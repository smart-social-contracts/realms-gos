from ic_python_db import (
    Entity,
    Integer,
    ManyToOne,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.budget")


class BudgetStatus:
    """Budget lifecycle states."""
    DRAFT = "draft"
    PROPOSED = "proposed"
    ADOPTED = "adopted"
    AMENDED = "amended"
    CLOSED = "closed"


class Budget(Entity, TimestampedMixin):
    """
    Budget - GGG Government Accounting Standard.
    
    Tracks planned vs actual revenues and expenditures for a fund
    within a fiscal period. Categories allow granular tracking
    (e.g., tax revenue, personnel expenses, supplies).
    
    Links to Proposal for governance approval workflow.
    """
    __alias__ = "id"
    id = String(max_length=64)
    name = String(max_length=256)
    fund = ManyToOne("Fund", "budgets")
    fiscal_period = ManyToOne("FiscalPeriod", "budgets")
    category = String(max_length=64)  # e.g., "tax_revenue", "personnel"
    budget_type = String(max_length=16)  # "revenue" or "expense"
    planned_amount = Integer(default=0)
    actual_amount = Integer(default=0)
    status = String(max_length=16, default=BudgetStatus.DRAFT)
    proposal = ManyToOne("Proposal", "budgets")
    description = String(max_length=512)

    def __repr__(self):
        return f"Budget(id={self.id!r}, category={self.category!r})"

    def variance(self) -> int:
        """Calculate budget variance (actual - planned)."""
        return (self.actual_amount or 0) - (self.planned_amount or 0)

    def variance_percent(self) -> float:
        """Calculate variance as percentage of planned."""
        if not self.planned_amount:
            return 0.0
        return (self.variance() / self.planned_amount) * 100

    def update_actual(self, amount: int) -> None:
        """Add to actual amount (called when ledger entries are created)."""
        self.actual_amount = (self.actual_amount or 0) + amount
        logger.info(f"Budget {self.id} actual updated to {self.actual_amount}")
