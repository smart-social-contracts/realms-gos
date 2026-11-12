"""The vetKD key a realm asks for, in the packed wasm and on a PocketIC instance that holds threshold keys.

A PocketIC instance has threshold keys only when it has a fiduciary subnet; the realm stays on the application subnet
and its management call is routed to the key's, as on the IC. Which key a network gets is tested in
tests/ggg/test_crypto.py. Here the wasm names it, and the IC answers to the production name. The servers differ in
the keys they hold: the one in dfx 0.31 has the test keys too, the one in dfx 0.32 only ``key_1``. So the test keys'
names are checked in the wasm and not asked of the server.
"""

import pytest
from pic import Canister, Wasm, new_pocket_ic
from pocket_ic import SubnetConfig

G2_PUBLIC_KEY_BYTES = 96  # a vetKD public key is a point of BLS12-381 G2, compressed


@pytest.fixture(scope="module")
def keyed(wasm_of):
    """One PocketIC instance with a fiduciary subnet, and the realm wasm uploaded to it."""
    pic = new_pocket_ic(SubnetConfig(application=1, fiduciary=True))
    return pic, Wasm(pic, wasm_of("production"))


def realm_on(keyed, people, network):
    pic, wasm = keyed
    realm = Canister(None, installer=people["installer"], pic=pic, wasm=wasm)
    installed = realm.call("setup_step", {"step": "install", "founder": people["founder"], "network": network},
                           sender=people["installer"])
    assert installed["ok"], installed
    return realm


@pytest.mark.parametrize("network, key", [("local", "dfx_test_key"), ("staging", "test_key_1"), ("ic", "key_1")])
def test_the_packed_wasm_names_the_ic_key_of_the_realms_network(keyed, people, network, key):
    realm = realm_on(keyed, people, network)
    assert realm.shell("from core import ic_keys; print(ic_keys.key_name())").strip() == key


def test_vetkd_public_key_works_with_the_production_key(keyed, people):
    out = realm_on(keyed, people, "ic").call("crypto_public_key", {}, sender=people["founder"])
    assert out["ok"], out
    assert len(bytes.fromhex(out["public_key"])) == G2_PUBLIC_KEY_BYTES
