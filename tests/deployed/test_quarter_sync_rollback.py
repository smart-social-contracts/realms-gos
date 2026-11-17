"""A capital rolled back to the previous release and upgraded again keeps its quarters right (core.sync_log.restart), on the
previous release's own wasm named by ``REALM_PREVIOUS_WASM`` (skipped when unset).

The previous release has no log of changes: rolled back to it, a capital changes what it serves and notes nothing. Upgraded
again, its log reads as it did when a quarter last synced, so a cursor from before the rollback must not be trusted: the
quarter that presents it is answered with everything.
"""

import os

import pytest
from network import Network
from test_quarter_sync_cost import CATCH_UP, heap
from test_quarter_sync_deltas import ZONE, directory

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
RATE_LIMIT = 600  # seconds the replica wants between install_code messages as dear as installing this wasm
pytestmark = pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")


def catch_up(net, quarter):
    net.pic.advance_time(2 * 10**9)
    heap(quarter, CATCH_UP)
    for _ in range(6):
        net.tick()


def upgrade(net, realm, wasm):
    net.wait(RATE_LIMIT)
    realm.upgrade(wasm)


def test_a_quarter_whose_capital_was_rolled_back_and_upgraded_again_catches_up_in_one_sync(wasm_of, people):
    net = Network(PREVIOUS, people)  # every realm starts on the previous release
    capital, north = net.realm("capital"), net.realm("north", members=())
    net.register_quarters(capital, north)
    for realm in (capital, north):
        upgrade(net, realm, wasm_of("production"))
    catch_up(net, north)
    catch_up(net, north)  # north holds a cursor of this release's log
    upgrade(net, capital, PREVIOUS)  # the rollback
    capital.ok("zone_define", "ctl", zone="riverside", name="Riverside", kind="district")
    capital.ok("quarter_configure", "ctl", quarter=north.cid, name="North Bank")
    upgrade(net, capital, wasm_of("production"))
    catch_up(net, north)
    assert heap(north, ZONE % {"zone": "riverside"}) == ["Riverside", "district"]
    assert directory(north)[north.cid]["name"] == "North Bank"
