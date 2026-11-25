"""The two realm wasms on PocketIC: production cannot enable test flags, the test build can only where it should.

Plain Python is not enough here: basilisk stubs typing and struct inside a canister, so the packed wasm is the only
place the built code is known to behave as shipped. See realms-gos#395.
"""

import pytest
from pack_realm_backend import pack, snapshot_version
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


@pytest.mark.parametrize("variant", ["production", "test"])
def test_a_build_without_a_release_tag_reports_the_commit_it_was_packed_from(wasm_of, people, variant):
    """realms-gos#551: the wasm holds its sources as text, so a placeholder left in setup.py would show in it."""
    wasm = wasm_of(variant)
    code = open(wasm, "rb").read()
    assert b"VERSION_PLACEHOLDER" not in code
    assert f'VERSION = "{snapshot_version()}"'.encode() in code
    realm = Canister(wasm, installer=people["installer"])
    assert realm.call("status", query=True)["version"] == snapshot_version()


# ---- test flags: the names the shell reads, and self-registration (realms-gos#737, #738)


def closed_realm(wasm, people, **settings):
    """A launched realm of the test build that needs an invite to join; ``settings`` are set before it launches."""
    realm = install(wasm, people, network="local")
    out = set_flags(realm, people, name="Agora", open_registration=False, **settings)
    assert out["ok"], out
    assert realm.call("setup_launch", sender=people["founder"])["ok"]
    return realm


def test_a_flag_is_kept_under_the_name_the_shell_reads(wasm_of, people):
    """The CLI, the descriptors and the installer name a flag without ``test_mode_``; the shell reads it with, so a flag
    kept as given did nothing: no direct page load signed in, and joining still wanted an invite."""
    realm = install(wasm_of("test"), people, network="local")
    out = set_flags(realm, people, test_mode=True,
                    test_flags={"ii_bypass": True, "test_mode_skip_terms": True, "demo_notice_body": {"en": "Hello"}})
    assert out["ok"], out
    assert realm.call("realm_info", query=True)["realm"]["test_flags"] == {
        "test_mode_ii_bypass": True, "test_mode_skip_terms": True, "demo_notice_body": {"en": "Hello"}}


def test_a_realm_configured_with_the_bare_names_has_them_renamed_when_it_starts(wasm_of, people):
    realm = install(wasm_of("test"), people, network="local")
    realm.shell("from ggg import Realm\nRealm['1'].test_flags = {'ii_bypass': True, 'skip_terms': False}\nprint('set')")
    realm.upgrade()
    assert realm.call("realm_info", query=True)["realm"]["test_flags"] == {
        "test_mode_ii_bypass": True, "test_mode_skip_terms": False}


def test_the_self_registration_flag_lets_anyone_join_without_an_invite_in_test_mode(wasm_of, people):
    realm = closed_realm(wasm_of("test"), people, test_mode=True, test_flags={"user_self_registration": True})
    out = realm.call("realm_join", sender=people["alice"])
    assert out["ok"] and out["status"] == "active", out


@pytest.mark.parametrize("settings", [
    {},
    {"test_mode": True, "test_flags": {"user_self_registration": False}},
    {"test_mode": True, "test_flags": {"ii_bypass": True}},
], ids=["no flag", "flag off", "another flag"])
def test_a_realm_without_the_self_registration_flag_still_needs_an_invite(wasm_of, people, settings):
    realm = closed_realm(wasm_of("test"), people, **settings)
    out = realm.call("realm_join", sender=people["alice"])
    assert not out["ok"] and "invite code" in out["error"], out


def test_the_self_registration_flag_is_not_in_the_production_wasm(wasm_of, people):
    """It cannot be set there (the test above), and the code that would read it is not in the artifact."""
    assert b"test_mode_user_self_registration" not in open(wasm_of("production"), "rb").read()
