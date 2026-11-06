"""The realm's calendar: the union of the ``calendar.json`` of the layers it runs (layered-codex.md §3.4).

Days are realm-local days written ``YYYY-MM-DD``: the realm computes the local day of a timestamp from
its own time zone (#503) before it asks the calendar anything. Date arithmetic is written out here
because the canister's ``datetime`` has no ``date``.

A working day is a day that is neither a weekend day nor a holiday (GGG docs/layered-codex.md §7). A
count in working days that reaches a year the calendar has no entries for fails closed: a wrong legal
deadline is worse than a clear error.
"""

from realms.legislation.errors import LegislationError
from realms.legislation.periods import Period
from realms.legislation.sealed import MappingProxyType, Sealed

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_month(year, month):
    return 29 if month == 2 and _leap(year) else _DAYS_IN_MONTH[month - 1]


def parse_day(day):
    """``"YYYY-MM-DD"`` as ``(year, month, day)``, refusing anything else."""
    parts = day.split("-") if isinstance(day, str) else []
    if (
        len(parts) != 3
        or not all(p.isdigit() and p.isascii() for p in parts)
        or len(parts[0]) != 4
    ):
        raise LegislationError(f"a day is written YYYY-MM-DD, not {day!r}")
    year, month, dom = (int(p) for p in parts)
    if not 1 <= month <= 12 or not 1 <= dom <= _days_in_month(year, month):
        raise LegislationError(f"{day} is not a date")
    return year, month, dom


def format_day(year, month, dom):
    return f"{year:04d}-{month:02d}-{dom:02d}"


def _ordinal(year, month, dom):
    """Days since 1970-01-01 (the days-from-civil algorithm)."""
    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + dom - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil(ordinal):
    """The inverse of ``_ordinal``."""
    z = ordinal + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    dom = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    return era * 400 + yoe + (month <= 2), month, dom


def weekday(day):
    """``mon`` to ``sun``."""
    return WEEKDAYS[(_ordinal(*parse_day(day)) + 3) % 7]  # 1970-01-01 was a Thursday


def shift_days(day, count):
    return format_day(*_civil(_ordinal(*parse_day(day)) + count))


def shift_months(day, count):
    """Date to date; when the month reached has no such day, its last day (Ley 39/2015 Art. 30.4)."""
    year, month, dom = parse_day(day)
    index = year * 12 + (month - 1) + count
    year, month = divmod(index, 12)
    month += 1
    return format_day(year, month, min(dom, _days_in_month(year, month)))


class Calendar(metaclass=Sealed):
    """The weekend days and the holidays, by year, of the layers' ``calendar.json`` files, top to bottom.

    A year is known for working-day counting only when **every** layer that has a ``calendar.json``
    declares it: if any such layer lacks the year a count reaches, the count fails closed (a layer that
    ships no calendar at all, such as EMSHI, is not one of these). A declared year may list no
    holidays. Built once from its layers and never changed: ``weekend`` is a frozenset, ``holidays`` a
    read-only map of year to frozenset.
    """

    __slots__ = ("weekend", "holidays", "_layer_years")

    def __init__(self, layers=()):
        weekend, holidays, per_layer = set(), {}, []
        for data in layers:
            per_layer.append(self._layer(data, weekend, holidays))
        object.__setattr__(self, "weekend", frozenset(weekend))
        object.__setattr__(
            self,
            "holidays",
            MappingProxyType({y: frozenset(d) for y, d in holidays.items()}),
        )
        object.__setattr__(self, "_layer_years", tuple(per_layer))

    def __setattr__(self, name, value):
        raise LegislationError(
            "the realm's calendar never changes: it is the union of the layers' calendar.json"
        )

    @staticmethod
    def _layer(data, weekend, holidays):
        """One parsed ``calendar.json``: ``weekend`` (day names) and ``years`` (``{"2027": [{"date": ...}]}``).
        Returns the set of years this layer declares."""
        if not isinstance(data, dict):
            raise LegislationError("a calendar is an object with weekend and years")
        for name in data.get("weekend") or []:
            if name not in WEEKDAYS:
                raise LegislationError(f"{name!r} is not a day of the week {WEEKDAYS}")
            weekend.add(name)
        declared = set()
        for year, entries in (data.get("years") or {}).items():
            if not (isinstance(year, str) and year.isdigit() and len(year) == 4):
                raise LegislationError(
                    f"a calendar year is written as four digits, not {year!r}"
                )
            declared.add(int(year))
            known = holidays.setdefault(int(year), set())
            for entry in entries:
                day = entry.get("date") if isinstance(entry, dict) else None
                if day is None or parse_day(day)[0] != int(year):
                    raise LegislationError(f"holiday {entry!r} is not a date of {year}")
                known.add(day)
        return frozenset(declared)

    def has_year(self, year):
        """Whether every calendar-bearing layer declares ``year`` (and there is at least one)."""
        return bool(self._layer_years) and all(
            year in years for years in self._layer_years
        )

    def is_working_day(self, day):
        """Neither a weekend day nor a holiday. Fails closed unless every layer's calendar declares the year."""
        year = parse_day(day)[0]
        if not self.has_year(year):
            raise LegislationError(
                f"not every layer's calendar has entries for {year}: a working-day count that reaches it fails closed"
            )
        return weekday(day) not in self.weekend and day not in self.holidays.get(
            year, ()
        )

    def add(self, day, period):
        """``day`` plus ``period``: calendar days or months and years date to date; working days
        stepped one by one over the weekend days and holidays of this calendar."""
        if not isinstance(period, Period):
            raise LegislationError(
                f"calendar.add takes a Period, such as working_days(15), not {period!r}"
            )
        parse_day(day)
        if period.unit == "Day":
            return shift_days(day, period.count)
        if period.unit == "Month":
            return shift_months(day, period.count)
        if period.unit == "Year":
            return shift_months(day, 12 * period.count)
        step = 1 if period.count >= 0 else -1
        left = abs(period.count)
        while left:
            day = shift_days(day, step)
            if self.is_working_day(day):
                left -= 1
        return day
