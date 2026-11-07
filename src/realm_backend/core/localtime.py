"""Realm-local time (realms-gos#503): which day it is in the realm's time zone, and where that day starts and ends.

Timestamps stay UTC nanoseconds everywhere. A day is a day number (core.civil), with ``civil_from_days`` giving
its date. The realm keeps the zone file of its own ``Realm.timezone`` and nothing else: ``load`` takes its raw
bytes, parses them once and keeps the result in memory. Where the bytes come from, and what happens when the
time zone changes, is for whoever calls ``load``; without a zone file the realm's time is UTC.

The parsed zone is lost on upgrade (and a query discards what it loads), so whoever wires ``load`` must call it
again in ``post_upgrade``.
"""

from core import tzif

NS = 1_000_000_000
DAY = 86400

_zone = tzif.UTC


def load(zone_file):
    """Use ``zone_file``, the raw bytes of a TZif file, as the realm's time zone; None goes back to UTC."""
    global _zone
    _zone = tzif.parse(zone_file) if zone_file else tzif.UTC


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
