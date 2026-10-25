"""Job positions and appointments within an organization (issue #241).

A ``Position`` is a *seat definition* on a Department: a titled role with a
capability profile, a headcount, and a salary line that feeds the org's
personnel budget planning. An ``Appointment`` is the *occupancy record* — who
holds a seat and for what period. Keeping the two apart gives vacancy math,
appointment history, and terms for free:

    vacancies = headcount - active appointments

Positions answer "what seat, in which org, at what cost" (org structure +
accounting); ``UserProfile`` answers "what can this person do" (authorization).
The two stay linked but distinct — a profile may be shared by positions in
several organizations.
"""

from ic_python_db import (
    Boolean,
    Entity,
    Integer,
    ManyToOne,
    OneToMany,
    String,
    TimestampedMixin,
)
from ic_python_logging import get_logger

logger = get_logger("entity.position")


class PositionStatus:
    """Position lifecycle states."""

    OPEN = "open"          # accepting appointments
    FROZEN = "frozen"      # temporarily not hiring (existing holders stay)
    CLOSED = "closed"      # abolished


class AppointmentStatus:
    """Appointment lifecycle states."""

    ACTIVE = "active"
    ENDED = "ended"


class AppointmentKind:
    """How the seat is held (issue #301)."""

    SUBSTANTIVE = "substantive"
    ACTING = "acting"


def position_key(department_name: str, title: str) -> str:
    """Canonical unique key for a position: ``<department>/<title>``."""
    return f"{department_name}/{title}"


class Position(Entity, TimestampedMixin):
    """A titled seat on a Department (product name: Organization).

    - ``key`` is the unique alias ``<department>/<title>`` (titles like
      "judge" may exist in several organizations).
    - ``profile`` is the UserProfile granted to holders (capabilities).
    - ``headcount`` is how many people may hold the seat simultaneously.
    - ``salary_amount`` is the per-``salary_period`` cost in the realm's
      accounting currency; ``planned_cost()`` (headcount x salary) is the
      position's line in the org's personnel budget planning.
    """

    __alias__ = "key"

    key = String(max_length=512)
    title = String(max_length=256)
    description = String(max_length=512)
    department = ManyToOne("Department", "positions")
    profile = ManyToOne("UserProfile", "positions")
    headcount = Integer(default=1)
    # Cost per salary_period in accounting-currency units (0 = unpaid/unset).
    salary_amount = Integer(default=0)
    salary_period = String(max_length=16, default="monthly")
    status = String(max_length=16, default=PositionStatus.OPEN)
    # When True, a new quarter copies the capital's current holders as acting
    # officers at birth (issue #301). Codex may set False for seats that must
    # be locally elected from day one.
    inherit_from_capital = Boolean(default=True)
    appointments = OneToMany("Appointment", "position")
    __version__ = 2

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("inherit_from_capital", True)
        return obj

    def __repr__(self):
        return f"Position(key={self.key!r}, headcount={self.headcount!r})"

    def active_appointments(self) -> list["Appointment"]:
        """Current holders (status == active)."""
        result = []
        for a in Appointment.instances():
            if (a.status or AppointmentStatus.ACTIVE) != AppointmentStatus.ACTIVE:
                continue
            pos = a.position
            if pos is not None and getattr(pos, "key", None) == self.key:
                result.append(a)
        return result

    def filled_count(self) -> int:
        return len(self.active_appointments())

    def vacancies(self) -> int:
        return max(0, int(self.headcount or 1) - self.filled_count())

    def planned_cost(self) -> int:
        """Full-headcount cost per salary_period (personnel budget planning)."""
        return int(self.headcount or 1) * int(self.salary_amount or 0)

    @classmethod
    def for_department(cls, department_name: str) -> list["Position"]:
        """All positions belonging to *department_name*."""
        result = []
        for pos in cls.instances():
            dept = pos.department
            if dept is not None and getattr(dept, "name", None) == department_name:
                result.append(pos)
        return result


class Appointment(Entity, TimestampedMixin):
    """One user holding one position for a period."""

    position = ManyToOne("Position", "appointments")
    user = ManyToOne("User", "appointments")
    started_at = Integer(default=0)
    ended_at = Integer(default=0)
    status = String(max_length=16, default=AppointmentStatus.ACTIVE)
    # Empty kind on pre-#301 rows is treated as substantive.
    kind = String(max_length=16, default=AppointmentKind.SUBSTANTIVE)
    source_canister_id = String(max_length=64, default="")
    source_position_key = String(max_length=512, default="")
    __version__ = 2

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj.setdefault("kind", AppointmentKind.SUBSTANTIVE)
            obj.setdefault("source_canister_id", "")
            obj.setdefault("source_position_key", "")
        return obj

    def __repr__(self):
        return (
            f"Appointment(position={getattr(self.position, 'key', None)!r}, "
            f"status={self.status!r}, kind={self.kind!r})"
        )

    def is_acting(self) -> bool:
        return (self.kind or AppointmentKind.SUBSTANTIVE) == AppointmentKind.ACTING

    def end(self, ended_at: int = 0):
        self.status = AppointmentStatus.ENDED
        self.ended_at = ended_at or _now_ts()


def _now_ts() -> int:
    """Current Unix timestamp in seconds, canister-safe (time.time() is 0 on IC)."""
    try:
        from kybra import ic as _ic  # noqa: PLC0415

        t = _ic.time()
        if t and t > 0:
            return int(t) // 1_000_000_000
    except Exception:
        pass
    import time

    t = time.time()
    return int(t) if t and t > 1_000_000 else 0


def appointment_kind(appointment) -> str:
    """Normalize empty/legacy kind to substantive."""
    raw = (getattr(appointment, "kind", None) or "").strip()
    if raw == AppointmentKind.ACTING:
        return AppointmentKind.ACTING
    return AppointmentKind.SUBSTANTIVE


def end_acting_appointments(position: Position) -> int:
    """End every active acting appointment on *position*. Returns how many ended."""
    ended = 0
    for a in list(position.active_appointments()):
        if appointment_kind(a) == AppointmentKind.ACTING:
            a.end()
            ended += 1
    return ended


def appoint(
    position: Position,
    user,
    kind: str = AppointmentKind.SUBSTANTIVE,
    source_canister_id: str = "",
    source_position_key: str = "",
) -> "Appointment | None":
    """Appoint *user* to *position* if a seat is free.

    Idempotent: returns the existing appointment when the user already holds
    the seat with the same kind. A **substantive** appoint ends every acting
    term on the seat first (issue #301) so a local Congress vote supersedes
    inherited capital officers.

    Returns ``None`` (and logs) when the position is not open or has no
    vacancy — callers like ``join_realm`` must not fail the whole
    registration over a full roster.
    """
    if (position.status or PositionStatus.OPEN) != PositionStatus.OPEN:
        logger.warning(f"Position '{position.key}' is {position.status}; not appointing")
        return None

    resolved_kind = (
        AppointmentKind.ACTING
        if (kind or "").strip() == AppointmentKind.ACTING
        else AppointmentKind.SUBSTANTIVE
    )

    if resolved_kind == AppointmentKind.SUBSTANTIVE:
        ended = end_acting_appointments(position)
        if ended:
            logger.info(
                f"Ended {ended} acting appointment(s) on '{position.key}' "
                f"for substantive appoint of {getattr(user, 'id', '?')}"
            )

    for a in position.active_appointments():
        holder = a.user
        if holder is not None and getattr(holder, "id", None) == getattr(user, "id", None):
            return a

    if position.vacancies() <= 0:
        logger.warning(
            f"Position '{position.key}' is full "
            f"({position.filled_count()}/{position.headcount}); not appointing"
        )
        return None

    appointment = Appointment(
        position=position,
        user=user,
        started_at=_now_ts(),
        ended_at=0,
        status=AppointmentStatus.ACTIVE,
        kind=resolved_kind,
        source_canister_id=(source_canister_id or "").strip(),
        source_position_key=(source_position_key or "").strip(),
    )
    logger.info(
        f"User {getattr(user, 'id', '?')} appointed to '{position.key}' "
        f"({resolved_kind})"
    )
    return appointment


def department_personnel_cost(department_name: str) -> int:
    """Planned personnel cost per period for one org (sum of open position lines).

    Feeds the ``planned_amount`` of a Budget row with category "personnel" on
    the department's fund — computed on demand so adopted budgets are never
    silently mutated by roster edits.
    """
    total = 0
    for pos in Position.for_department(department_name):
        if (pos.status or PositionStatus.OPEN) == PositionStatus.OPEN:
            total += pos.planned_cost()
    return total
