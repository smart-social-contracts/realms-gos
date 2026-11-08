"""Realm-local time (realms-gos#503): which day it is in the realm's time zone, and where that day starts and ends.

Timestamps stay UTC nanoseconds everywhere. A day is a day number (core.civil), with ``civil_from_days`` giving
its date. The realm keeps the zone of its own ``Realm.timezone`` and nothing else: ``use`` reads its zone file from
the release's tz database (core.zones), parses it once and keeps the result in memory. Without one the realm's time
is UTC. ``local_day``, ``day_bounds`` and ``add_period`` tell the day on that clock, or on another when given a
``zone`` (a ``tzif.Zone``).

The parsed zone is lost on upgrade, and a query discards what it loads, so ``use`` runs where the realm starts
(``setup.start``, in ``init`` and ``post_upgrade``) and where the time zone is set, never in a query. A change of
time zone applies from the moment it is made: nothing stored is UTC-dependent, so nothing is rewritten.
"""

from core import civil, tzif, zones

NS = 1_000_000_000
DAY = 86400

_zone = tzif.UTC
_name = "UTC"  # the zone in memory, by name; None when it was loaded from bytes


def load(zone_file):
    """Use ``zone_file``, the raw bytes of a TZif file, as the realm's time zone; None goes back to UTC."""
    global _zone, _name
    _zone, _name = tzif.parse(zone_file) if zone_file else tzif.UTC, None


def use(name):
    """Use the bundled zone ``name`` (``UTC`` or an IANA name, see core.zones) as the realm's time zone. The zone
    file is parsed only when ``name`` is not the zone already in memory."""
    global _name
    if name != _name:
        load(zones.zone_file(name) if name != "UTC" else None)
        _name = name


def local_day(ns, zone=None):
    """``(day, offset)``: the local day of UTC timestamp ``ns`` and the UTC offset in seconds at that time."""
    seconds = ns // NS
    offset = _zone_of(zone).offset(seconds)
    return (seconds + offset) // DAY, offset


def day_bounds(day, zone=None):
    """``(start, end)`` in UTC ns of local ``day``; the end is the start of the next day.

    A day lasts 23 or 25 hours when the clocks change in it."""
    zone = _zone_of(zone)
    return _day_start(day, zone) * NS, _day_start(day + 1, zone) * NS


def add_period(ns, period, calendar, zone=None):
    """``(day, start)``: the local day reached by adding ``period`` to the local day of UTC timestamp ``ns``, as
    ``YYYY-MM-DD`` (the form ``Calendar`` writes), and the UTC ns at which that day starts.

    ``calendar`` does the counting, working days included (realms-gos#505), on the clock of ``zone``. Which clock
    counts is layered-codex.md §4.5: the capital's for a realm-wide matter, a quarter's for its own department; each
    is the realm's own zone in the canister that counts. A count into a year the calendar has no entries for raises its
    LegislationError, naming the year: a guard that depends on the date denies.
    """
    # Reading the library's names compiles it (about 40M instructions for the calendar alone, measured on PocketIC):
    # that is paid when a codex first counts, not at every start of the realm, where this module is imported.
    from realms.legislation.calendar import format_day, parse_day

    zone = _zone_of(zone)
    today, _ = local_day(ns, zone)
    reached = calendar.add(format_day(*civil.civil_from_days(today)), period)
    return reached, _day_start(civil.days_from_civil(*parse_day(reached)), zone) * NS


def _zone_of(zone):
    """``zone``, or the realm's own when none is given."""
    return _zone if zone is None else zone


def _day_start(day, zone):
    """The first UTC second of ``day``. When the clocks change at midnight, midnight comes twice (the day starts
    at the first) or not at all (it starts at the change), so both offsets around it are tried."""
    midnight = day * DAY
    before, after = zone.offset(midnight - DAY), zone.offset(midnight + DAY)
    starts = [at for at in (midnight - before, midnight - after) if at + zone.offset(at) == midnight]
    return min(starts) if starts else midnight - before
