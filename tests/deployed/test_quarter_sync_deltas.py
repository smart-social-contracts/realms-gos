"""A quarter's catch-up takes what its capital changed, on real wasms (api.quarters.sync, core.sync_log; "What a sync
carries" in docs/architecture/quarters-and-federation.md): a capital and two quarters on one PocketIC instance. Each
quarter's catch-up is run the way its timer runs it, and the capital notes what each asked for.

A change on the capital reaches every quarter at its next sync, a quarter that missed more syncs than the capital's log
keeps catches up in full, the first sync after an upgrade is a full one, a capital that answers as the previous release does
is taken in full, and the reports that change nothing leave the audit log as it was. The realms are built once for the
module and the tests below use them in order.
"""

import pytest
from conftest import principal
from network import Network
from test_quarter_sync_cost import CATCH_UP, STATIC_QUARTERS, heap

WEEK = 7 * 24 * 3600
SPY = '''
from api import quarters
if not hasattr(quarters, "asked"):
    quarters.asked, quarters.current = [], quarters.answer
    def answer(q, have):
        quarters.asked.append([q.id, have is not None])
        return quarters.current(q, have)
    quarters.answer = answer
print("{}")
'''
ASKED = 'import json\nfrom api import quarters\nprint(json.dumps(quarters.asked))'
PREVIOUS_RELEASE = '''
from api import quarters
quarters.answer = lambda q, have: {k: v for k, v in quarters.current(q, None).items() if k != "rev"}
print("{}")
'''
THIS_RELEASE = 'from api import quarters\nquarters.answer = lambda q, have: quarters.current(q, have)\nprint("{}")'
SMALL_LOG = 'from core import sync_log\nsync_log.KEPT = 4\nprint("{}")'
EVENTS = 'import json\nfrom ggg import Event\nprint(json.dumps(Event.max_id()))'
ZONE = 'import json\nfrom ggg import Zone\nz = Zone[%(zone)r]\nprint(json.dumps(z and [z.name, z.kind]))'
ALL_ZONES = 'import json\nfrom api import assets\nprint(json.dumps(sorted(z.id for z in assets.defined_zones())))'


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def federation(net):
    capital = net.realm("capital")
    north, south = net.realm("north", members=()), net.realm("south", members=())
    net.register_quarters(capital, north, south)
    heap(capital, SPY)
    return capital, north, south


def catch_up(net, quarter):
    """The quarter's catch-up, as its timer runs it, after the clock moved on."""
    net.pic.advance_time(2 * 10**9)
    heap(quarter, CATCH_UP)
    for _ in range(6):
        net.tick()


def asked(capital, quarter=None):
    """What the capital was asked, as ``[quarter, whether it presented a cursor]``, those of ``quarter`` only if it is given."""
    return [call for call in heap(capital, ASKED) if quarter is None or call[0] == quarter.cid]


def directory(realm):
    return {r["id"]: r for r in realm.ok("quarter_directory", None, query=True)["quarters"]}


def test_a_change_on_the_capital_reaches_every_quarter_at_its_next_sync(net, federation):
    capital, north, south = federation
    for quarter in (north, south):
        catch_up(net, quarter)
        catch_up(net, quarter)
    capital.ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    capital.ok("realm_configure", "ctl", manifesto="One realm")
    heap(capital, STATIC_QUARTERS % {"first": 1, "last": 1})
    before = len(asked(capital))
    for quarter in (north, south):
        catch_up(net, quarter)
        assert heap(quarter, ZONE % {"zone": "riverside"}) == ["Riverside", "district"]
        assert quarter.ok("realm_info", None, query=True)["realm"]["manifesto"] == "One realm"
        assert "quarter-1" in directory(quarter)
    assert [have for _, have in asked(capital)[before:]] == [True, True]  # both held a cursor, and took what came after
    capital.ok("quarter_deregister", "ctl", quarter="quarter-1")
    for quarter in (north, south):
        catch_up(net, quarter)
        assert "quarter-1" not in directory(quarter)


def test_a_sync_where_nothing_changed_leaves_the_audit_log_as_it_was(net, federation):
    capital, north, south = federation
    for quarter in (north, south):
        catch_up(net, quarter)
    before = heap(capital, EVENTS)
    for _ in range(3):
        for quarter in (north, south):
            catch_up(net, quarter)
    assert heap(capital, EVENTS) == before
    quarter_id = north.cid
    assert abs(directory(capital)[quarter_id]["last_sync"] - north.now()) <= 2  # and the capital still knows when it heard
    north.ok("realm_join", principal("one more"), nickname="one more")
    catch_up(net, north)
    assert heap(capital, EVENTS) > before  # a sync that told the capital something is kept


def test_a_quarter_that_missed_more_syncs_than_the_log_keeps_catches_up_in_full(net, federation):
    capital, north, south = federation
    heap(capital, SMALL_LOG)
    catch_up(net, north)
    north.stop()
    for n in range(6):
        capital.ok("zone_define", "ctl", zone=f"zone-{n}", name=f"Zone {n}", kind="barri")
    net.pic.advance_time(WEEK * 10**9)  # a week of hourly syncs the quarter was not there for
    north.start()
    catch_up(net, north)
    assert [north.cid, True] in asked(capital, north)  # it asked from where it was, which the log could not serve
    assert heap(north, ALL_ZONES) == heap(capital, ALL_ZONES)
    assert len(heap(north, ALL_ZONES)) >= 6


def test_the_first_sync_after_an_upgrade_is_a_full_one(net, federation):
    capital, north, south = federation
    catch_up(net, south)
    catch_up(net, south)
    assert asked(capital, south)[-1] == [south.cid, True]
    south.upgrade()
    net.wait(300)  # its first sync is due soon after post_upgrade
    assert asked(capital, south)[-1] == [south.cid, False]
    catch_up(net, south)
    assert asked(capital, south)[-1] == [south.cid, True]


def test_a_capital_that_answers_as_the_previous_release_does_is_taken_in_full(net, federation):
    capital, north, south = federation
    heap(capital, PREVIOUS_RELEASE)
    try:
        capital.ok("zone_define", "ctl", zone="old-town", name="Old Town", kind="district")
        capital.ok("realm_configure", "ctl", manifesto="Still one realm")
        for quarter in (north, south):
            catch_up(net, quarter)
            assert heap(quarter, ZONE % {"zone": "old-town"}) == ["Old Town", "district"]
            assert quarter.ok("realm_info", None, query=True)["realm"]["manifesto"] == "Still one realm"
            assert heap(quarter, ALL_ZONES) == heap(capital, ALL_ZONES)
    finally:
        heap(capital, THIS_RELEASE)
    capital.ok("zone_define", "ctl", zone="old-town", name="Old Quarter", kind="district")
    catch_up(net, north)
    assert heap(north, ZONE % {"zone": "old-town"}) == ["Old Quarter", "district"]
