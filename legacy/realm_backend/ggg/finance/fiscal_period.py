from ic_python_db import (
    Entity,
    OneToMany,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.fiscal_period")


class FiscalPeriodStatus:
    """Fiscal period lifecycle states."""
    OPEN = "open"
    CLOSED = "closed"
    ARCHIVED = "archived"


class FiscalPeriod(Entity, TimestampedMixin):
    """
    Fiscal Period - GGG Government Accounting Standard.
    
    Defines accounting period boundaries for reporting and budgeting.
    Typically annual (FY2025) but can be quarterly (2025-Q1) or monthly.
    
    Status:
    - open: Active period, entries can be added
    - closed: Period ended, no new entries
    - archived: Historical, read-only
    """
    __alias__ = "id"
    id = String(max_length=16)  # e.g., "FY2025", "2025-Q1"
    name = String(max_length=64)
    start_date = String(max_length=32)  # ISO format: "2025-01-01"
    end_date = String(max_length=32)    # ISO format: "2025-12-31"
    status = String(max_length=16, default=FiscalPeriodStatus.OPEN)
    ledger_entries = OneToMany("LedgerEntry", "fiscal_period")
    budgets = OneToMany("Budget", "fiscal_period")
    financial_reports = OneToMany("FinancialReport", "period")

    def __repr__(self):
        return f"FiscalPeriod(id={self.id!r}, status={self.status!r})"

    def is_open(self) -> bool:
        """Check if period accepts new entries."""
        return self.status == FiscalPeriodStatus.OPEN

    def close(self) -> None:
        """Close the fiscal period."""
        self.status = FiscalPeriodStatus.CLOSED
        logger.info(f"Fiscal period {self.id} closed")
