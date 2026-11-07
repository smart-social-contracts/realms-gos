"""Realm-local time (realms-gos#503): which day it is in the realm's time zone, and where that day starts and ends.

Timestamps stay UTC nanoseconds everywhere. A day is a day number (core.civil), with ``civil_from_days`` giving
its date. The realm keeps the zone of its own ``Realm.timezone`` and nothing else: ``use`` reads its zone file from
the release's tz database (core.zones), parses it once and keeps the result in memory. Without one the realm's time
is UTC.

The parsed zone is lost on upgrade, and a query discards what it loads, so ``use`` runs where the realm starts
(``setup.start``, in ``init`` and ``post_upgrade``) and where the time zone is set, never in a query. A change of
time zone applies from the moment it is made: nothing stored is UTC-dependent, so nothing is rewritten.
"""

from core import tzif, zones

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


def local_day(ns):
    """``(day, offset)``: the realm-local day of UTC timestamp ``ns`` and the UTC offset in seconds at that time."""
    seconds = ns // NS
    offset = _zone.offset(seconds)
    return (seconds + offset) // DAY, offset


def day_bounds(day):
    """``(start, end)`` in UTC ns of realm-local ``day``; the end is the start of the next day.

    A day lasts 23 or 25 hours when the clocks change in it."""
    return _day_start(day) * NS, _day_start(day + 1) * NS


def _day_start(day):
    """The first UTC second of ``day``. When the clocks change at midnight, midnight comes twice (the day starts
    at the first) or not at all (it starts at the change), so both offsets around it are tried."""
    midnight = day * DAY
    before, after = _zone.offset(midnight - DAY), _zone.offset(midnight + DAY)
    starts = [at for at in (midnight - before, midnight - after) if at + _zone.offset(at) == midnight]
    return min(starts) if starts else midnight - before
