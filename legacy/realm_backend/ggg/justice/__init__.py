"""Justice module - legal system, courts, cases, and verdicts."""

from .appeal import Appeal, AppealStatus, appeal_file, appeal_decide, appeal_withdraw
from .case import (
    Case,
    CaseStatus,
    case_file,
    case_assign_judges,
    case_issue_verdict,
    case_close,
    case_transfer,
    case_begin_executing,
    case_penalties_resolved,
)
from .court import Court, CourtLevel
from .dispute import Dispute
from .judge import Judge
from .justice_system import JusticeSystem, JusticeSystemType
from .penalty import Penalty, PenaltyType, penalty_execute, penalty_waive
from .seeding import seed_justice_template
from .verdict import Verdict, verdict_prehook, verdict_posthook

__all__ = [
    "Appeal",
    "AppealStatus",
    "appeal_file",
    "appeal_decide",
    "appeal_withdraw",
    "Case",
    "CaseStatus",
    "case_file",
    "case_assign_judges",
    "case_issue_verdict",
    "case_close",
    "case_transfer",
    "case_begin_executing",
    "case_penalties_resolved",
    "Court",
    "CourtLevel",
    "Dispute",
    "Judge",
    "JusticeSystem",
    "JusticeSystemType",
    "Penalty",
    "PenaltyType",
    "penalty_execute",
    "penalty_waive",
    "seed_justice_template",
    "Verdict",
    "verdict_prehook",
    "verdict_posthook",
]
