"""Periods: a count and a GGG unit, written with the unit constructors GGG defines and no others.

``PeriodUnit = variant { Day; WorkingDay; Month; Year }`` (GGG, docs/layered-codex.md §3.3 and §7). A
Period compares only with a Period of the same unit; comparing two units fails closed, with no
conversion through the calendar, so ``days(45) >= working_days(30)`` raises.
"""

from realms.legislation.errors import LegislationError, UnitMismatch
from realms.legislation.sealed import Sealed

UNITS = ("Day", "WorkingDay", "Month", "Year")
_WRITTEN = {
    "Day": "days",
    "WorkingDay": "working_days",
    "Month": "months",
    "Year": "years",
}


class Period(metaclass=Sealed):
    """``count`` of ``unit``, as GGG stores a Period. ``to_json()`` is the form a setting's value takes.
    A Period never changes once written."""

    __slots__ = ("count", "unit")

    def __init__(self, count, unit):
        if type(count) is not int:
            raise LegislationError(f"a Period's count is an integer, not {count!r}")
        if type(unit) is not str or unit not in UNITS:
            raise LegislationError(f"{unit!r} is not a GGG Period unit {UNITS}")
        object.__setattr__(self, "count", count)
        object.__setattr__(self, "unit", unit)

    def __setattr__(self, name, value):
        raise LegislationError("a Period never changes: write a new one")

    def to_json(self):
        return {"count": self.count, "unit": self.unit}

    def _other(self, other):
        if not isinstance(other, Period):
            return NotImplemented
        if other.unit != self.unit:
            raise UnitMismatch(
                f"{self!r} and {other!r} are not of one unit: Periods compare only within one unit"
            )
        return other.count

    def __eq__(self, other):
        count = self._other(other)
        return count if count is NotImplemented else self.count == count

    def __lt__(self, other):
        count = self._other(other)
        return count if count is NotImplemented else self.count < count

    def __le__(self, other):
        count = self._other(other)
        return count if count is NotImplemented else self.count <= count

    def __gt__(self, other):
        count = self._other(other)
        return count if count is NotImplemented else self.count > count

    def __ge__(self, other):
        count = self._other(other)
        return count if count is NotImplemented else self.count >= count

    def __hash__(self):
        return hash((self.count, self.unit))

    def __repr__(self):
        return f"{_WRITTEN[self.unit]}({self.count})"


def days(count):
    """A Period in calendar days."""
    return Period(count, "Day")


def working_days(count):
    """A Period in working days: days that are neither weekend days nor holidays of the realm's calendar."""
    return Period(count, "WorkingDay")


def months(count):
    """A Period in months, date to date."""
    return Period(count, "Month")


def years(count):
    """A Period in years, date to date."""
    return Period(count, "Year")
