"""The test world itself (``world.py``): Agora as every other test builds it."""

import world
from ggg import Appointment, Department, Fund, Organization, Position, Token


def census():
    rows = {kind.__name__: kind.count() for kind in (Department, Position, Appointment, Organization, Fund, Token)}
    return rows, {p.id: p.headcount for p in Position.instances()}


def test_building_agora_twice_leaves_one_of_each():
    """An autouse fixture and a test's own helper may both build it. On ic-python-db 0.14.0 a second row with the same
    id silently took the id over, so about 130 tests ran with two of every department and seat (realms-gos#770)."""
    world.build()
    once = census()
    world.build()
    assert census() == once
