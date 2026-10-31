from ic_python_db import Entity, ManyToMany, ManyToOne, OneToMany, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.court")


class CourtLevel:
    FIRST_INSTANCE = "first_instance"
    APPELLATE = "appellate"
    SUPREME = "supreme"
    SPECIALIZED = "specialized"


class Court(Entity, TimestampedMixin):
    """
    A Court handles Cases within a JusticeSystem.
    
    Courts operate under a Codex (legal framework) and may require
    a License to operate. They have Judges assigned to them and
    handle Cases from filing through Verdict.
    
    Courts can be hierarchical (first instance → appellate → supreme)
    for the Appeal process.
    """
    
    __alias__ = "name"
    name = String(min_length=2, max_length=256)
    description = String(max_length=1024)
    jurisdiction = String(max_length=256)  # Geographic or subject matter jurisdiction
    level = String(max_length=32)  # first_instance, appellate, supreme, specialized
    status = String(max_length=STATUS_MAX_LENGTH)  # active, suspended, dissolved
    justice_system = ManyToOne("JusticeSystem", "courts")
    codex = ManyToOne("Codex", "courts")
    license = OneToOne("License", "court")
    judges = OneToMany("Judge", "court")
    cases = OneToMany("Case", "court")
    appeals_received = OneToMany("Appeal", "appellate_court")
    parent_court = ManyToOne("Court", "child_courts")
    child_courts = OneToMany("Court", "parent_court")
    metadata = String(max_length=1024)

    def __repr__(self):
        return f"Court(name={self.name!r}, level={self.level!r})"

    def is_active(self) -> bool:
        """Check if this Court is currently active."""
        return self.status == "active"

    def is_licensed(self) -> bool:
        """Check if this Court has a valid License."""
        if not self.license:
            return False
        return self.license.status == "active"

    def get_judges(self) -> list:
        """Get all Judges assigned to this Court."""
        return list(self.judges) if self.judges else []

    def get_active_judges(self) -> list:
        """Get only active Judges."""
        return [j for j in self.get_judges() if j.status == "active"]

    def get_cases(self) -> list:
        """Get all Cases in this Court."""
        return list(self.cases) if self.cases else []

    def get_pending_cases(self) -> list:
        """Get Cases that are still pending verdict."""
        return [c for c in self.get_cases() if c.status in ("filed", "in_progress", "assigned")]

    def can_hear_appeal(self) -> bool:
        """Check if this Court can hear appeals (appellate or supreme level)."""
        return self.level in (CourtLevel.APPELLATE, CourtLevel.SUPREME)

    @staticmethod
    def court_registered_posthook(court: "Court") -> None:
        """Hook called after Court registration. Override via Codex."""
        logger.info(f"Court {court.name} registered")

    @staticmethod
    def court_suspended_posthook(court: "Court") -> None:
        """Hook called after Court suspension. Override via Codex."""
        logger.info(f"Court {court.name} suspended")
