"""
GGG Status Enums

OS-level statuses (TaskStatus, TaskExecutionStatus) are re-exported from
ic-basilisk-toolkit.  Application-level statuses remain defined here.

See: https://github.com/smart-social-contracts/realms-gos/issues/153
"""

from enum import Enum

# --- OS-level statuses (canonical source: ic_basilisk_toolkit/status.py) ---
from ic_basilisk_toolkit.status import TaskStatus, TaskExecutionStatus  # noqa: F401

# --- Application-level statuses (realms-specific) ---


class TradeStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DisputeStatus(Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ProposalStatus(Enum):
    PENDING_REVIEW = "pending_review"
    PENDING_VOTE = "pending_vote"
    VOTING = "voting"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MandateStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    TERMINATED = "terminated"


class ContractStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    TERMINATED = "terminated"


class InstrumentStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class UserStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    BANNED = "banned"


class VoteStatus(Enum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"


class OrganizationStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    BANNED = "banned"