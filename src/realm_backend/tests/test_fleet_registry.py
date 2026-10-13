"""Fleet discovery: Casals well-known file, then the conductor's resolve."""

import json

import pytest

from core.fleet_registry import casals_url, parse_resolve_reply, parse_well_known


def test_casals_url_per_environment():
    assert casals_url("production") == "https://casals.realmsgos.org/.well-known/casals.json"
    assert casals_url(" Staging ") == "https://casals.staging.realmsgos.org/.well-known/casals.json"
    for bad in ("", "  ", "evil.com/x", "a.b"):
        with pytest.raises(ValueError):
            casals_url(bad)


def test_well_known_gives_the_conductor():
    text = json.dumps({"orchestra": "realms-product", "conductor": "kfamy-tiaaa-aaaao-bbmoq-cai"})
    assert parse_well_known(text) == "kfamy-tiaaa-aaaao-bbmoq-cai"
    for bad in ("", "[]", "{}", json.dumps({"conductor": "not a principal"})):
        with pytest.raises(ValueError):
            parse_well_known(bad)


def test_resolve_reply():
    ok = json.dumps({"ok": True, "canister_id": "aaaaa-aa", "name": "fleet-file-registry"})
    assert parse_resolve_reply(ok, "fleet-file-registry") == "aaaaa-aa"
    with pytest.raises(ValueError, match="no resolvable"):
        parse_resolve_reply(json.dumps({"ok": False, "error": "no resolvable canister 'x'"}), "x")
    with pytest.raises(ValueError):
        parse_resolve_reply("nope", "x")
