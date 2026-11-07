"""Realm-local time in the real wasm (realms-gos#503).

A canister has no ``zoneinfo``, ``bisect``, ``struct`` or ``datetime.timezone``, so the zone file reader and the date
arithmetic are pure Python that only the packed wasm shows working. The realm's zone file is loaded through the
controller's ``__shell__``: how a realm gets its zone file in production is still open.
"""

import json
import os
from datetime import datetime, timezone

import pytest
from pic import Canister

ZONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "tzif")
NS = 10**9
HOUR = 3600

# Instruction ceilings, about three times what PocketIC measures: a parse 1.8M, a conversion 120k, day_bounds 0.8M.
PARSE_BUDGET = 6_000_000
CONVERSION_BUDGET = 400_000
BOUNDS_BUDGET = 2_500_000

# Basilisk compiles a bundled module from its source the first time one of its names is read. That one-off cost
# (FIRST_USE) is kept apart from the parse of a zone file.
FIRST_USE = """
start = ic.performance_counter(0)
from core import civil, localtime
localtime.load(None)
civil.civil_from_days(0)
print(ic.performance_counter(0) - start)
"""

LOAD = """
from core import civil, localtime
import json
data = bytes.fromhex('{hex}')
start = ic.performance_counter(0)
localtime.load(data)
print(json.dumps(ic.performance_counter(0) - start))
"""

CONVERT = """
from core import civil, localtime
import json
out = []
for ns in {instants}:
    start = ic.performance_counter(0)
    day, offset = localtime.local_day(ns)
    cost = ic.performance_counter(0) - start
    out.append([list(civil.civil_from_days(day)), offset // 3600, cost])
print(json.dumps(out))
"""

BOUNDS = """
from core import civil, localtime
import json
start = ic.performance_counter(0)
bounds = localtime.day_bounds(civil.days_from_civil({y}, {m}, {d}))
print(json.dumps([list(bounds), ic.performance_counter(0) - start]))
"""


def utc(*when):
    return int(datetime(*when, tzinfo=timezone.utc).timestamp()) * NS


@pytest.fixture(scope="module")
def clock(wasm_of, people):
    """A canister whose realm-local time is read through its controller's shell."""
    canister = Canister(wasm_of("production"), installer=people["installer"])
    canister.first_use = int(canister.shell(FIRST_USE))

    def run(code):
        out = canister.shell(code)
        assert "Traceback" not in out, out
        return json.loads(out)

    canister.run = run
    return canister


def load(clock, name):
    """Make the zone file the realm's; returns the instructions its parse cost."""
    with open(os.path.join(ZONES, name), "rb") as f:
        return clock.run(LOAD.format(hex=f.read().hex()))


def convert(clock, *instants):
    """``[((year, month, day), offset in hours), ...]`` for UTC instants, and the most instructions one cost."""
    out = clock.run(CONVERT.format(instants=list(instants)))
    return [(tuple(date), hours) for date, hours, _ in out], max(cost for *_, cost in out)


@pytest.mark.parametrize("name", ["Europe_Madrid.fat", "Europe_Madrid.slim"])
def test_madrid_is_right_on_both_sides_of_a_switch_and_a_year_after_2037(clock, name):
    parse = load(clock, name)
    print(f"\nthe three modules compile on first use: {clock.first_use:,} instructions")
    print(f"{name}: parse {parse:,} instructions")
    assert parse < PARSE_BUDGET

    now, cost = convert(clock, utc(2026, 3, 29, 0, 59, 59), utc(2026, 3, 29, 1), utc(2026, 7, 1, 21, 59),
                        utc(2026, 7, 1, 22), utc(2026, 10, 25, 0, 59, 59), utc(2026, 10, 25, 1))
    assert now == [((2026, 3, 29), 1), ((2026, 3, 29), 2), ((2026, 7, 1), 2), ((2026, 7, 2), 2),
                   ((2026, 10, 25), 2), ((2026, 10, 25), 1)]
    print(f"{name}: a conversion in 2026 costs at most {cost:,} instructions")
    assert cost < CONVERSION_BUDGET

    later, cost = convert(clock, utc(2040, 3, 25, 0, 59), utc(2040, 3, 25, 1), utc(2040, 7, 1, 12),
                          utc(2040, 10, 28, 0, 59), utc(2040, 10, 28, 1), utc(2040, 1, 1, 12))
    assert later == [((2040, 3, 25), 1), ((2040, 3, 25), 2), ((2040, 7, 1), 2),
                     ((2040, 10, 28), 2), ((2040, 10, 28), 1), ((2040, 1, 1), 1)]
    print(f"{name}: a conversion in 2040 costs at most {cost:,} instructions")
    assert cost < CONVERSION_BUDGET


def test_a_southern_hemisphere_zone_has_summer_time_over_the_new_year(clock):
    load(clock, "Australia_Sydney.slim")
    seen, _ = convert(clock, utc(2026, 1, 15, 12), utc(2026, 7, 15, 12), utc(2026, 4, 4, 15, 59), utc(2026, 4, 4, 16),
                      utc(2026, 10, 3, 15, 59), utc(2026, 10, 3, 16), utc(2040, 12, 25, 14), utc(2040, 7, 1, 12))
    assert seen == [((2026, 1, 15), 11), ((2026, 7, 15), 10), ((2026, 4, 5), 11), ((2026, 4, 5), 10),
                    ((2026, 10, 4), 10), ((2026, 10, 4), 11), ((2040, 12, 26), 11), ((2040, 7, 1), 10)]


def test_the_zone_is_parsed_once_and_stays_in_memory_between_messages(clock):
    load(clock, "Europe_Madrid.slim")
    # Two later messages, none of them loading: the reader keeps the parsed zone.
    assert convert(clock, utc(2026, 7, 1, 22))[0] == [((2026, 7, 2), 2)]
    assert convert(clock, utc(2026, 1, 1, 23))[0] == [((2026, 1, 2), 1)]


def test_the_day_boundaries_follow_the_clock_changes(clock):
    load(clock, "Europe_Madrid.fat")
    for date, start, hours in [((2026, 3, 28), utc(2026, 3, 27, 23), 24), ((2026, 3, 29), utc(2026, 3, 28, 23), 23),
                               ((2026, 10, 25), utc(2026, 10, 24, 22), 25), ((2040, 3, 25), utc(2040, 3, 24, 23), 23)]:
        (first, end), cost = clock.run(BOUNDS.format(y=date[0], m=date[1], d=date[2]))
        assert (first, (end - first) // NS // HOUR) == (start, hours), date
    print(f"\nday_bounds costs {cost:,} instructions")
    assert cost < BOUNDS_BUDGET
