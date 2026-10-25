"""Treasury allocation entities — GGG treasury standard (issue #261).

``AllocationRule`` is the voted percentage split that routes recognized
revenue from a source fund (usually ROOT) into target funds at each epoch
close. ``TreasuryConfig`` is the singleton holding the calendar-aligned
epoch settings and the automatic-allocation switch.
"""

from ic_python_db import (
    Entity,
    Integer,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.allocation_rule")


class AllocationRuleStatus:
    """Allocation rule lifecycle states."""
    DRAFT = "draft"
    ADOPTED = "adopted"
    SUPERSEDED = "superseded"


class AllocationRule(Entity, TimestampedMixin):
    """
    Allocation Rule - GGG treasury standard (issue #261).

    A voted percentage split of recognized revenue into funds. Percentages
    are stored in basis points (10000 = 100%); any remainder stays in the
    source fund as reserve. An adopted rule stays in force across epochs
    until superseded by a newer adopted rule.
    """
    __alias__ = "id"
    id = String(max_length=64)               # e.g. "ALLOC-RULE-3"
    source_fund_code = String(max_length=16, default="ROOT")
    # JSON: [{"fund": "DEPT-SOC", "percent_bp": 3000}, ...]
    rules = String(max_length=1024)
    status = String(max_length=16, default=AllocationRuleStatus.DRAFT)
    proposal_id = String(max_length=64)      # governance trail (optional)
    description = String(max_length=512)

    def __repr__(self):
        return f"AllocationRule(id={self.id!r}, status={self.status!r})"


class TreasuryConfig(Entity, TimestampedMixin):
    """
    Treasury configuration singleton (id="1") — issue #261.

    Epoch settings are calendar-aligned: epochs always start on calendar
    boundaries (1st of month / quarter / half-year / anchor month). Length
    changes are policy-gated and take effect from the next boundary.
    """
    __alias__ = "id"
    id = String(max_length=8)                 # always "1"
    # monthly | quarterly | semiannual | annual | weekly | biweekly | minutes
    epoch_length = String(max_length=16, default="monthly")
    epoch_minutes = Integer(default=0)         # test-mode only, when epoch_length=minutes
    anchor_month = Integer(default=1)          # fiscal-year start month (annual)
    source_fund_code = String(max_length=16, default="ROOT")
    auto_allocate = String(max_length=8, default="false")  # "true"/"false"

    def __repr__(self):
        return (
            f"TreasuryConfig(epoch={self.epoch_length!r}, "
            f"auto={self.auto_allocate!r})"
        )
