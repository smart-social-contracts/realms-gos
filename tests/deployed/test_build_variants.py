"""The two realm wasms on PocketIC: production cannot enable test flags, the test build can only where it should.

Plain Python is not enough here: basilisk stubs typing and struct inside a canister, so the packed wasm is the only
place the built code is known to behave as shipped. See realms-gos#395.
"""

import pytest
from pack_realm_backend import pack
from pic import Canister

# Every switch a test or demo realm sets through ``test_flags``.
TEST_FLAGS = ("test_mode_ii_bypass", "test_mode_skip_terms", "test_mode_user_self_registration",
              "test_mode_demo_data", "test_mode_skip_passport_zkproof", "test_mode_disable_monetary_tokens",
              "test_mode_demo_notice")


def install(wasm, people, **facts):
    """A realm whose installer has run the install step with ``facts``; returns it and the founder."""
    realm = Canister(wasm, installer=people["installer"])
    out = realm.call("setup_step", {"step": "install", "founder": people["founder"], **facts}, sender=people["installer"])
    assert out["ok"], out
    return realm


def set_flags(realm, people, **settings):
    return realm.call("setup_step", {"step": "realm", **settings}, sender=people["founder"])


def test_the_production_wasm_refuses_every_test_flag_whatever_the_install_asked(wasm_of, people):
    wasm = wasm_of("production")
    assert b"REALMS_TEST_BUILD_AUTH_BYPASS" not in open(wasm, "rb").read()
    realm = install(wasm, people, network="local", can_test_mode=True)
    assert realm.call("status", query=True)["build_variant"] == "production"
    for flag in TEST_FLAGS:
        out = set_flags(realm, people, test_flags={flag: True})
        assert not out["ok"] and "test flags" in out["error"], (flag, out)
    out = set_flags(realm, people, test_mode=True)
    assert not out["ok"] and "test flags" in out["error"], out
    info = realm.call("realm_info", query=True)["realm"]
    assert info["test_mode"] is False and info["test_flags"] == {}


@pytest.mark.parametrize("facts, accepted", [
    ({}, False),
    ({"network": "ic"}, False),
    ({"network": "local"}, True),
    ({"network": "ic", "can_test_mode": True}, True),
], ids=["network unset", "network ic", "network local", "network ic, allowed at install"])
def test_the_test_wasm_accepts_test_flags_only_off_production_or_when_allowed_at_install(wasm_of, people, facts, accepted):
    realm = install(wasm_of("test"), people, **facts)
    assert realm.call("status", query=True)["build_variant"] == "test"
    out = set_flags(realm, people, test_mode=True, test_flags={flag: True for flag in TEST_FLAGS})
    assert out["ok"] is accepted, out
    if accepted:
        assert realm.call("realm_info", query=True)["realm"]["test_flags"]["test_mode_ii_bypass"] is True
    else:
        assert "test flags" in out["error"]


def test_a_release_build_reports_its_version(people):
    """The release workflow stamps the tag through the packer; a placeholder left in would ship as the version."""
    realm = Canister(pack(version="v9.9.9"), installer=people["installer"])
    assert realm.call("status", query=True)["version"] == "v9.9.9"
