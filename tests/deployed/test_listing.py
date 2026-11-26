"""A realm keeps its listing in a realm registry up to date, on the real wasm (realms-gos#736).

The registry is ``mock_registry``: it keeps the last listing it was sent and counts the calls, so each test reads what a
realm published and how often. What matters here is what only the built artifact shows: the timer that publishes
survives an upgrade, and an idle realm calls nobody however long it runs.
"""

import json
from types import SimpleNamespace

import mock_registry
import pytest
from ic.candid import decode
from ic.principal import Principal
from pic import Canister

DAY = 86400
LOOK = 800  # seconds: more than the first look takes after a start (api.listing.FIRST + SPREAD)


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def listed(registry):
    """What the registry has been sent: how many calls, and the last listing."""
    pic, rid = registry.pic, Principal.from_str(registry.id)
    calls = decode(bytes(pic.query_call(rid, "calls", b"DIDL\x00\x00")))[0]["value"]
    if not calls:
        return 0, None
    sent = decode(bytes(pic.query_call(rid, "last_arg", b"DIDL\x00\x00")))[0]["value"]
    return calls, json.loads(sent)


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm of three members, and the registry that lists it, which it has not been told of yet."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_launch", founder)
    for member in ("alice", "bob"):
        ok(realm, "realm_join", people[member])
    realm.registry = SimpleNamespace(pic=realm.pic, id=mock_registry.install(realm.pic))
    return realm


def test_a_launched_realm_told_its_registry_publishes_what_it_holds_at_once(realm, people):
    ok(realm, "host_registry", people["installer"], registry=realm.registry.id)
    realm.wait(1)
    calls, sent = listed(realm.registry)
    assert calls == 1
    assert {k: sent[k] for k in ("name", "users_count", "stage")} == {"name": "Agora", "users_count": 3, "stage": "alpha"}
    assert "logo" not in sent  # the realm has none, or one that is not an https URL


def test_a_realm_that_does_not_change_calls_nobody_for_a_week(realm):
    for _ in range(7):
        realm.wait(DAY)
    assert listed(realm.registry)[0] == 1


def test_a_new_member_reaches_the_registry_at_the_next_daily_look(realm, people):
    ok(realm, "realm_join", people["mallory"])
    assert listed(realm.registry)[0] == 1  # a join publishes nothing
    realm.wait(DAY)
    calls, sent = listed(realm.registry)
    assert (calls, sent["users_count"]) == (2, 4)


def test_the_look_survives_an_upgrade_and_a_realm_upgraded_publishes_within_minutes(realm, people):
    """Timers do not survive an upgrade: the one that publishes is armed again when the realm starts, or listings would
    go on working until the first upgrade and then stop."""
    realm.upgrade()
    ok(realm, "realm_join", people["claude-conn"])
    realm.wait(LOOK)
    calls, sent = listed(realm.registry)
    assert (calls, sent["users_count"]) == (3, 5)
    ok(realm, "realm_info", people["alice"], query=True)
    for _ in range(3):
        realm.wait(DAY)
    assert listed(realm.registry)[0] == 3  # and the look it armed goes on without calling when nothing changed


@pytest.fixture
def no_registry(realm):
    """A canister that is no registry (the realm itself: it has no ``listing_publish``), listed as one for the test and
    forgotten after it, so that no test depends on the one before it."""
    yield realm.id.to_str()
    out = realm.shell("from ggg import Realm, Registry\n"
                      f"Registry[{realm.id.to_str()!r}].delete()\n"
                      "data = Realm['1'].manifest_data\n"
                      f"data['listing'] = {{k: v for k, v in data['listing'].items() if k != {realm.id.to_str()!r}}}\n"
                      "Realm['1'].manifest_data = data\n"
                      "print('forgotten')")
    assert "forgotten" in out, out


def test_a_registry_that_is_not_one_is_survived_and_tried_again(realm, people, no_registry):
    """A canister that has no ``listing_publish``: the call fails, the realm keeps going, the look comes round again."""
    ok(realm, "host_registry", people["installer"], registry=no_registry)
    realm.wait(1)
    told = json.loads(realm.shell("import json\nfrom api import listing\nprint(json.dumps(listing.kept()))").strip())
    assert told[no_registry]["error"] != "" and "sent" not in told[no_registry]
    assert ok(realm, "realm_numbers", people["alice"], query=True)["members"] == 5
    realm.wait(DAY)  # still alive: the next look runs, and the registry that works is not called again
    assert listed(realm.registry)[0] == 3


def test_a_daily_look_with_nothing_to_say_costs_little(realm, costs):
    """What every realm pays every day when nothing changed: its figures (the members of every quarter, the stage, the
    name), which it compares with what each registry took. The call itself is left out: a shell makes none."""
    spent = costs.measure(realm, "listing: the figures of a daily look that makes no call",
                          "from api import listing\nlisting.figures()\nlisting.registries()\nlisting.kept()")
    assert spent < 50_000_000
