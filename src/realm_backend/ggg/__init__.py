"""
Python implementation of the Generalized Global Governance (GGG) standard

Note: Call and TaskStep are implementation details for task execution,
not part of the core GGG standard.
"""

# System module - user management, permissions, tasks, and core services
from .system import (
    Appointment,
    AppointmentKind,
    AppointmentStatus,
    Call,
    Department,
    DepartmentAuthority,
    Extension,
    MenuCategoryConfig,
    MenuDepartmentVisibility,
    MenuItemConfig,
    Notification,
    Operations,
    Permission,
    Position,
    PositionStatus,
    Profiles,
    RegistrationCode,
    ROOT_ORG_NAME,
    Service,
    Task,
    TaskExecution,
    TaskSchedule,
    TaskStep,
    User,
    UserProfile,
    appoint,
    appointment_kind,
    department_personnel_cost,
    end_acting_appointments,
    position_key,
)

# Justice module - legal system, courts, cases, and verdicts
from .justice import (
    Appeal,
    AppealStatus,
    appeal_file,
    appeal_decide,
    appeal_withdraw,
    Case,
    CaseStatus,
    case_file,
    case_assign_judges,
    case_issue_verdict,
    case_close,
    case_transfer,
    case_begin_executing,
    case_penalties_resolved,
    Court,
    CourtLevel,
    Dispute,
    Judge,
    JusticeSystem,
    JusticeSystemType,
    Penalty,
    PenaltyType,
    penalty_execute,
    penalty_waive,
    seed_justice_template,
    Verdict,
    verdict_prehook,
    verdict_posthook,
)

# Finance module - instruments, balances, budgets, tokens, and treasury
from .finance import (
    AllocationRule,
    AllocationRuleStatus,
    Balance,
    Budget,
    BudgetStatus,
    Category,
    EntryType,
    FinancialReport,
    FinancialReportKind,
    FiscalPeriod,
    FiscalPeriodStatus,
    Fund,
    FundType,
    Instrument,
    Invoice,
    LedgerEntry,
    MarketPlace,
    NFTToken,
    PaymentAccount,
    TaxPolicy,
    TaxPolicyStatus,
    Token,
    Trade,
    Transfer,
    Treasury,
    TreasuryConfig,
)

# Identity module - humans, identities, members, and organizations
from .identity import (
    Human,
    Identity,
    Member,
    Organization,
)

# Governance module - realms, registries, codex, contracts, calendar, and voting
from .governance import (
    Calendar,
    Codex,
    Contract,
    Delegation,
    DelegationRequest,
    EntityMigration,
    FederalVote,
    FederalVoteLeg,
    FederationMessage,
    LEG_STATUS_ARMED,
    LEG_STATUS_EXECUTED,
    LEG_STATUS_EXPIRED,
    LEG_STATUS_FAILED,
    LEG_STATUS_OPEN,
    LEG_STATUS_REPORTED,
    VOTE_STATUS_ADOPTED,
    VOTE_STATUS_EXPIRED,
    VOTE_STATUS_NO_QUORUM,
    VOTE_STATUS_OPEN,
    VOTE_STATUS_REJECTED,
    License,
    LicenseType,
    license_issue,
    license_revoke,
    Mandate,
    Proposal,
    Quarter,
    QuarterResident,
    QuarterStatus,
    Realm,
    RealmStatus,
    Registry,
    Vote,
)

# Land module - land parcels and zones
from .land import (
    Land,
    LandType,
    LandStatus,
    Zone,
)

# Public codex-facing facade (issue #265) - supported helpers a codex may call
# instead of reaching into core.* internals.
from .facade import (
    check_access,
    ensure_root_org,
    extension_call,
    extension_entity_class,
    grant_root_authority_over_local_orgs,
    iter_users,
    user_has_profile,
    user_in_department,
)

__all__ = [
    "AllocationRule",
    "AllocationRuleStatus",
    "Appointment",
    "AppointmentKind",
    "AppointmentStatus",
    "Balance",
    "Calendar",
    "Budget",
    "BudgetStatus",
    "Call",  # Implementation detail, not GGG standard
    "Category",
    "Codex",
    "Contract",
    "Department",
    "DepartmentAuthority",
    "Delegation",
    "DelegationRequest",
    "Dispute",
    "EntityMigration",
    "EntryType",
    "Extension",
    "FederalVote",
    "FederalVoteLeg",
    "FederationMessage",
    "LEG_STATUS_ARMED",
    "LEG_STATUS_EXECUTED",
    "LEG_STATUS_EXPIRED",
    "LEG_STATUS_FAILED",
    "LEG_STATUS_OPEN",
    "LEG_STATUS_REPORTED",
    "VOTE_STATUS_ADOPTED",
    "VOTE_STATUS_EXPIRED",
    "VOTE_STATUS_NO_QUORUM",
    "VOTE_STATUS_OPEN",
    "VOTE_STATUS_REJECTED",
    "FinancialReport",
    "FinancialReportKind",
    "FiscalPeriod",
    "FiscalPeriodStatus",
    "Fund",
    "FundType",
    "Human",
    "Identity",
    "Instrument",
    "Invoice",
    "Land",
    "LandType",
    "LandStatus",
    "LedgerEntry",
    "MarketPlace",
    "License",
    "LicenseType",
    "license_issue",
    "license_revoke",
    "Mandate",
    # Menu configuration
    "MenuCategoryConfig",
    "MenuDepartmentVisibility",
    "MenuItemConfig",
    # Justice System
    "JusticeSystem",
    "JusticeSystemType",
    "Court",
    "CourtLevel",
    "Judge",
    "Case",
    "CaseStatus",
    "case_file",
    "case_assign_judges",
    "case_issue_verdict",
    "case_close",
    "case_transfer",
    "case_begin_executing",
    "case_penalties_resolved",
    "Verdict",
    "verdict_prehook",
    "verdict_posthook",
    "Penalty",
    "PenaltyType",
    "penalty_execute",
    "penalty_waive",
    "seed_justice_template",
    "Appeal",
    "AppealStatus",
    "appeal_file",
    "appeal_decide",
    "appeal_withdraw",
    "Member",
    "Notification",
    "Operations",
    "ROOT_ORG_NAME",
    "Organization",
    "PaymentAccount",
    "Permission",
    "Position",
    "PositionStatus",
    "Profiles",
    "Proposal",
    "Quarter",
    "QuarterResident",
    "QuarterStatus",
    "Realm",
    "RealmStatus",
    "RegistrationCode",
    "Registry",
    "Service",
    "Task",
    "TaskExecution",
    "TaskSchedule",
    "TaskStep",
    "TaxPolicy",
    "TaxPolicyStatus",
    "Token",
    "NFTToken",
    "Trade",
    "Transfer",
    "Treasury",
    "TreasuryConfig",
    "User",
    "UserProfile",
    "Vote",
    "Zone",
    "appoint",
    "appointment_kind",
    "department_personnel_cost",
    "end_acting_appointments",
    "position_key",
    # Codex-facing facade helpers (issue #265)
    "check_access",
    "ensure_root_org",
    "extension_call",
    "extension_entity_class",
    "grant_root_authority_over_local_orgs",
    "iter_users",
    "user_has_profile",
    "user_in_department",
]


def classes() -> list[str]:
    """Return list of GGG entity class names (excludes helper types)."""
    return [name for name in __all__ if name not in (
        'LandType', 'LandStatus', 'RealmStatus', 'Operations', 'Profiles', 'BudgetStatus',
        'FiscalPeriodStatus', 'FundType', 'EntryType', 'Category', 'AllocationRuleStatus',
        'FinancialReportKind', 'TaxPolicyStatus',
        'LicenseType', 'JusticeSystemType', 'CourtLevel', 'CaseStatus', 'QuarterStatus',
        'PenaltyType', 'AppealStatus', 'PositionStatus', 'AppointmentKind', 'AppointmentStatus',
        'VOTE_STATUS_OPEN', 'VOTE_STATUS_ADOPTED', 'VOTE_STATUS_REJECTED',
        'VOTE_STATUS_NO_QUORUM', 'VOTE_STATUS_EXPIRED',
        'LEG_STATUS_OPEN', 'LEG_STATUS_REPORTED', 'LEG_STATUS_ARMED',
        'LEG_STATUS_EXECUTED', 'LEG_STATUS_FAILED', 'LEG_STATUS_EXPIRED',
        'case_file', 'case_assign_judges',
        'case_issue_verdict', 'case_close', 'case_transfer', 'case_begin_executing',
        'case_penalties_resolved',
        'verdict_prehook', 'verdict_posthook',
        'penalty_execute', 'penalty_waive', 'appeal_file', 'appeal_decide',
        'appeal_withdraw',
        'seed_justice_template',
        'license_issue', 'license_revoke', 'appoint', 'appointment_kind',
        'department_personnel_cost', 'end_acting_appointments',
        'position_key',
        # Codex-facing facade helpers (issue #265) - functions, not entities
        'check_access', 'ensure_root_org', 'extension_call', 'extension_entity_class',
        'grant_root_authority_over_local_orgs', 'iter_users', 'user_has_profile',
        'user_in_department',
    )]


# Historical imports (`from ggg.case import CaseStatus`) resolve the justice
# modules as top-level ggg attributes.
import sys as _sys

from .justice import appeal as _justice_appeal
from .justice import case as _justice_case
from .justice import court as _justice_court
from .justice import judge as _justice_judge
from .justice import justice_system as _justice_system
from .justice import penalty as _justice_penalty
from .justice import verdict as _justice_verdict
from .governance import license as _license_mod
from .system import user as _user_mod

_sys.modules[f"{__name__}.appeal"] = _justice_appeal
_sys.modules[f"{__name__}.case"] = _justice_case
_sys.modules[f"{__name__}.court"] = _justice_court
_sys.modules[f"{__name__}.judge"] = _justice_judge
_sys.modules[f"{__name__}.justice_system"] = _justice_system
_sys.modules[f"{__name__}.penalty"] = _justice_penalty
_sys.modules[f"{__name__}.verdict"] = _justice_verdict
_sys.modules[f"{__name__}.license"] = _license_mod
_sys.modules[f"{__name__}.user"] = _user_mod
