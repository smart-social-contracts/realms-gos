"""A realm that was upgraded before upgrades to this release (ic-python-db's schema check).

Every upgrade stores the entity schema it ran with, and the next one is rejected if it adds a field without a default
to an entity that has no ``migrate()``. A realm installed from the previous release and upgraded straight to this one
has no stored schema yet, so the other upgrade tests never meet the check; a staging realm, upgraded many times, does.
Hence the previous release is upgraded over itself first, which is what stores its schema.

The previous release's wasm is named by ``REALM_PREVIOUS_WASM`` (skipped when unset).
"""

import os

import pytest
from pic import Canister

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
RATE_LIMIT = 600  # seconds the replica wants between install_code messages as dear as installing this wasm


@pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")
def test_a_realm_upgraded_before_upgrades_to_this_release(wasm_of, people):
    realm = Canister(PREVIOUS, installer=people["installer"])
    before = realm.call("status", query=True)["version"]
    realm.wait(RATE_LIMIT)
    realm.upgrade()  # the previous release over itself: its schema is stored now
    realm.wait(RATE_LIMIT)
    realm.upgrade(wasm_of("production"))  # raises if a field was added without a default or a migrate()
    status = realm.call("status", query=True)
    assert status["ok"] and status["version"] != before, (before, status)
