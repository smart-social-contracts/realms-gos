"""Court hierarchy: justice systems, courts, and the default-court safety net."""

import json
from typing import List, Optional

from ic_python_logging import get_logger

logger = get_logger("core.justice.courts")

DEFAULT_COURT_NAME = "Default Court"
DEFAULT_JUSTICE_SYSTEM_NAME = "Default Justice System"


def valid_levels() -> tuple:
    from ggg import CourtLevel

    return (
        CourtLevel.FIRST_INSTANCE,
        CourtLevel.APPELLATE,
        CourtLevel.SUPREME,
        CourtLevel.SPECIALIZED,
    )


def all_courts() -> List:
    from ggg import Court

    return list(Court.instances())


def all_systems() -> List:
    from ggg import JusticeSystem

    return list(JusticeSystem.instances())


def find_court(court_id):
    from ggg import Court

    try:
        return Court[court_id]
    except Exception:
        return None


def preferred_appellate_court() -> Optional[object]:
    """Supreme, else appellate, on this canister.

    Used when the live case is already on Capital: appeal only changes
    court level (issue #325). No inter-quarter hop.
    """
    from ggg import CourtLevel

    courts = [c for c in all_courts() if getattr(c, "status", None) == "active"]
    hear = [c for c in courts if getattr(c, "can_hear_appeal", lambda: False)()]
    if not hear:
        hear = [
            c for c in courts
            if getattr(c, "level", None) in (CourtLevel.SUPREME, CourtLevel.APPELLATE)
        ]
    supreme = [c for c in hear if getattr(c, "level", None) == CourtLevel.SUPREME]
    appellate = [c for c in hear if getattr(c, "level", None) == CourtLevel.APPELLATE]
    candidates = supreme or appellate
    return candidates[0] if candidates else None


def preferred_court() -> Optional[object]:
    """The court to file in when the caller does not name one.

    An active court of first instance, else any active court, else anything at
    all — filing should not dead-end because a realm's hierarchy is incomplete.
    """
    from ggg import CourtLevel

    courts = all_courts()
    active = [c for c in courts if c.status == "active"]
    first_instance = [c for c in active if c.level == CourtLevel.FIRST_INSTANCE]
    candidates = first_instance or active or courts
    return candidates[0] if candidates else None


def ensure_default_court() -> Optional[object]:
    """Create a minimal active first-instance court when none exists.

    Idempotent, and a safety net rather than the normal path: a realm's codex
    seeds its own hierarchy during bootstrap and always wins, because it runs
    first. Returns the created court, or ``None`` if one was already active.
    """
    from ggg import Court, CourtLevel, JusticeSystem, JusticeSystemType

    for court in all_courts():
        if court.status == "active":
            return None

    systems = all_systems()
    system = systems[0] if systems else JusticeSystem(
        name=DEFAULT_JUSTICE_SYSTEM_NAME,
        description="Automatically created so litigations can be filed.",
        system_type=JusticeSystemType.PUBLIC,
        status="active",
        metadata="",
    )

    court = Court(
        name=DEFAULT_COURT_NAME,
        description="Automatically created court of first instance.",
        jurisdiction="General",
        level=CourtLevel.FIRST_INSTANCE,
        status="active",
        justice_system=system,
        metadata=json.dumps({"seeded_by": "justice_litigation"}),
    )
    logger.info(f"Created default court {court.name!r}")
    return court


def create_court(
    name: str,
    description: str = "",
    jurisdiction: str = "",
    level: str = "",
    justice_system_id=None,
    parent_court_id=None,
):
    """Create an active court. Name uniqueness is enforced: ``Court`` is aliased
    on it, so two courts of the same name would be one court."""
    from ggg import Court, CourtLevel, JusticeSystem

    name = (name or "").strip()
    if len(name) < 2:
        raise ValueError("Court name must be at least 2 characters")

    level = (level or CourtLevel.FIRST_INSTANCE).strip()
    if level not in valid_levels():
        raise ValueError(
            f"Invalid level {level!r}. Valid: {', '.join(valid_levels())}"
        )

    if find_court(name) is not None:
        raise ValueError(f"A court named {name!r} already exists")

    system = None
    if justice_system_id:
        system = JusticeSystem[justice_system_id]
        if system is None:
            raise ValueError(f"Justice system {justice_system_id} not found")
    else:
        systems = all_systems()
        system = systems[0] if systems else None

    parent = None
    if parent_court_id:
        parent = find_court(parent_court_id)
        if parent is None:
            raise ValueError(f"Parent court {parent_court_id} not found")

    court = Court(
        name=name,
        description=str(description or ""),
        jurisdiction=str(jurisdiction or ""),
        level=level,
        status="active",
        metadata="",
    )
    if system is not None:
        court.justice_system = system
    if parent is not None:
        court.parent_court = parent

    logger.info(f"Created court {name!r} ({level})")
    return court
