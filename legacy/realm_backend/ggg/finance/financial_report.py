"""Issued financial reports — frozen statement snapshots (GGG finance)."""

from ic_python_db import (
    Entity,
    Integer,
    ManyToOne,
    OneToMany,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.financial_report")


class FinancialReportKind:
    PERIOD_CLOSE = "period_close"
    AS_OF = "as_of"
    DRAFT = "draft"


class FinancialReport(Entity, TimestampedMixin):
    """
    Frozen financial statement snapshot issued at a point in time.

    ``statements`` holds compact JSON produced by
    :func:`core.financial_reports.compile_statements`.
    """
    __alias__ = "id"
    id = String(max_length=64)  # FR-{period_id} | FR-asof-{ts} | FR-draft-{ts} | FR-{period}-R{n}
    kind = String(max_length=16)  # period_close | as_of | draft
    as_of = String(max_length=32)
    period = ManyToOne("FiscalPeriod", "financial_reports")
    issued_at = String(max_length=32)
    issued_by = String(max_length=64)
    currency = String(max_length=16)
    statements = String(max_length=8192, default="{}")  # frozen JSON
    entry_count = Integer(default=0)
    source_hash = String(max_length=64)
    supersedes = ManyToOne("FinancialReport", "restatements")
    restatements = OneToMany("FinancialReport", "supersedes")

    def __repr__(self):
        return f"FinancialReport(id={self.id!r}, kind={self.kind!r})"
