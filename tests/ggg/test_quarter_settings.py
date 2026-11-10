"""The settings a realm keeps in sync for its quarters, in process (realms-gos#528; "What the realm sets for its
quarters" in docs/architecture/quarters-and-federation.md). The multi-canister flows are tests/deployed/test_quarter_settings.py."""

import json

import fake_chain
from api import quarters
from ggg import Department, FederationMessage, Proposal, Realm
from kit import call, fire_timers, ok, rows
from test_quarters import CAPITAL, capital, quarter, reply
import world

SETTINGS = {"name": "Agora", "manifesto": "One realm", "terms": "Be kind", "primary_color": "#1E6B52",
            "accounting_currency": "AGR", "accounting_currency_decimals": 2, "languages": ["ca", "en"], "status": "beta",
            "images": {"logo_url": "", "background_image_url": ""}}


def push(body, sender=CAPITAL):
    return call("federation_message", sender, topic=quarters.SETTINGS_TOPIC, body=body)


def sent(cid, topic):
    return [json.loads(a)["body"] for c, m, a in fake_chain.state["calls"]
            if c == cid and m == "federation_message" and json.loads(a)["topic"] == topic]


# ---- The capital


def test_a_change_of_a_synced_setting_is_pushed_to_every_active_quarter_each_in_its_own_message():
    capital("north", "south")
    fake_chain.state["replies"][("north", "federation_message")] = Exception("north is stopped")
    reply("south", "federation_message", configured=sorted(SETTINGS))
    ok("realm_configure", "ctl", name="Agora", welcome_message="Hi")
    fire_timers()
    (body,) = sent("south", quarters.SETTINGS_TOPIC)
    assert body["name"] == "Agora" and body["status"] == "beta" and "welcome_message" not in body
    assert body["images"] == {"logo_url": "", "background_image_url": ""}
    records = {next(iter(m.response)): m.response for m in rows(FederationMessage) if m.topic == quarters.SETTINGS_TOPIC}
    assert records["south"]["south"]["ok"] and "north is stopped" in records["north"]["north"]["error"]
    assert Realm["1"].name == "Agora"  # the capital's value stands whatever a quarter answers

    fake_chain.state["calls"].clear()
    ok("realm_configure", "ctl", welcome_message="Hello", open_registration=True)  # local settings: nothing to push
    fire_timers()
    assert sent("south", quarters.SETTINGS_TOPIC) == []


def test_an_image_travels_as_its_digest_which_the_capital_takes_once_per_change():
    capital("south")
    logo = "data:image/png;base64," + "A" * 1000
    ok("realm_configure_branding", "ctl", logo_url=logo)
    digest = quarters.synced_values()["images"]["logo_url"]
    assert len(digest) == 64 and Realm["1"].manifest_data[quarters.DIGESTS]["logo_url"] == digest
    ok("realm_configure_branding", "ctl", logo_url=logo + "B")  # the push takes the new one
    assert digest != Realm["1"].manifest_data[quarters.DIGESTS]["logo_url"] == quarters.synced_values()["images"]["logo_url"]


def test_the_quarter_sync_answer_carries_the_settings_and_the_catch_up_interval():
    capital("south")
    out = ok("quarter_sync", "south", population=1)
    assert out["settings"]["status"] == "beta" and out["sync_every"] == quarters.SYNC_EVERY
    for wrong in (quarters.SYNC_MIN - 1, quarters.SYNC_MAX + 1):
        assert "an hour) to" in call("quarter_configure", "ctl", sync_every=wrong)["error"]
    ok("quarter_configure", "ctl", sync_every=6 * 3600)
    assert ok("quarter_sync", "south", population=1)["sync_every"] == 6 * 3600


def test_a_quarters_welcome_message_is_set_for_the_quarter_named():
    capital("north")
    assert "name it with quarter" in call("quarter_configure", "ctl", welcome_message="Hi")["error"]
    reply("north", "federation_message", configured=["welcome_message"])
    ok("quarter_configure", "ctl", quarter="north", welcome_message="Benvinguts")
    assert sent("north", quarters.LOCAL_SETTINGS) == [{"welcome_message": "Benvinguts"}]
    assert Realm["1"].welcome_message == ""


def test_a_federal_action_that_changes_a_synced_setting_is_replayed_by_the_capital_only():
    assert quarters.changes_synced({"op": "realm_configure", "args": {"name": "X", "welcome_message": "Hi"}})
    assert quarters.changes_synced({"op": "setup_launch", "args": {}})
    assert not quarters.changes_synced({"op": "realm_configure", "args": {"welcome_message": "Hi"}})
    assert not quarters.changes_synced({"op": "governance_update", "args": {"voting_window": 0}})


def test_a_quarter_skips_the_replay_of_a_federal_action_that_changes_a_synced_setting(monkeypatch):
    from api import federation

    quarter(monkeypatch, "south")
    action, rule = {"op": "realm_configure", "args": {"name": "Elsewhere"}, "title": "rename"}, dict(quarters.RULE)
    sealed = federation.freeze(action, rule, 0)
    ok("federation_message", CAPITAL, topic="federal_vote.open", body={
        "vote": "FV-1", "action": action, "rule": rule, "deadline": 0, "hash": sealed, "proposer": "alice",
        "origin": CAPITAL})
    out = ok("federation_message", CAPITAL, topic="federal_vote.result", body={"vote": "FV-1", "status": "passed",
                                                                                "hash": sealed})
    assert out["result"]["skipped"] and Realm["1"].name != "Elsewhere"


def test_the_texts_a_quarter_keeps_in_sync_are_capped():
    world.build()
    for name in ("manifesto", "terms"):
        assert "above 20,000 characters" in call("realm_configure", "ctl", **{name: "x" * 20_001})["error"]
    ok("realm_configure", "ctl", terms="x" * 20_000)


# ---- A quarter


def test_a_quarter_refuses_a_synced_setting_before_any_vote_and_takes_its_local_ones(monkeypatch):
    quarter(monkeypatch, "south")
    for name, args in (("realm_configure", {"name": "Mine", "welcome_message": "Hi"}),
                       ("realm_configure_branding", {"primary_color": "#000000"}),
                       ("realm_configure_tokens", {"accounting_currency": "EUR"})):
        out = call(name, "ctl", **args)
        assert out["kind"] == "forbidden" and f"from the capital {CAPITAL}" in out["error"], (name, out)
    world.policy(Department["Administration"], 2, 3)
    filed = Proposal.count()
    assert "kept in sync" in call("realm_configure", "ctl", manifesto="Ours")["error"]
    assert Proposal.count() == filed
    world.policy(Department["Administration"], 1, 1)
    ok("realm_configure", "ctl", welcome_message="Hi", open_registration=True)
    assert Realm["1"].welcome_message == "Hi"

    Realm["1"].status = "setup"
    assert "kept in sync" in call("setup_step", "ctl", step="realm", name="Mine")["error"]
    ok("setup_step", "ctl", step="realm", open_registration=True)


def test_a_quarter_applies_the_capitals_push_and_no_one_else_s(monkeypatch):
    quarter(monkeypatch, "south", "north")
    out = push(SETTINGS)
    assert out["ok"] and out["configured"] == sorted(SETTINGS)
    r = Realm["1"]
    assert (r.name, r.terms, r.accounting_currency, r.accounting_currency_decimals, r.languages, r.status) == (
        "Agora", "Be kind", "AGR", 2, ["ca", "en"], "beta")
    assert push({**SETTINGS, "name": "Fake"}, sender="north")["kind"] == "forbidden"
    refused = call("federation_message", "ctl", topic=quarters.SETTINGS_TOPIC, body=SETTINGS, to="north")
    assert refused["kind"] == "forbidden" and "never relayed" in refused["error"]
    assert r.name == "Agora"


def test_a_quarter_reads_the_capitals_images_only_when_their_digest_changes(monkeypatch):
    quarter(monkeypatch, "south")
    reply(CAPITAL, "realm_info", realm={"logo_url": "data:logo", "background_image_url": ""})
    with_logo = {**SETTINGS, "images": {"logo_url": "d1", "background_image_url": ""}}
    push(with_logo)
    assert Realm["1"].logo_url == "data:logo"
    fake_chain.state["calls"].clear()
    push(with_logo)
    assert [m for _, m, _ in fake_chain.state["calls"]] == []  # the same digest: nothing to read
    fake_chain.state["replies"][(CAPITAL, "realm_info")] = Exception("capital is stopped")
    out = push({**SETTINGS, "name": "Later", "images": {"logo_url": "d2", "background_image_url": ""}})
    assert not out["ok"] and Realm["1"].name == "Agora"  # nothing half applied


def test_a_quarter_in_setup_keeps_its_stage_and_launches_on_its_capitals(monkeypatch):
    quarter(monkeypatch, "south")
    r = Realm["1"]
    r.status, r.name = "setup", "South"
    r.sync_state = {}
    assert "run quarter_sync first" in call("setup_launch", "ctl")["error"]
    push({**SETTINGS, "status": "setup"})
    assert r.status == "setup" and r.name == "Agora"
    assert "still in setup" in call("setup_launch", "ctl")["error"]
    push(SETTINGS)
    assert r.status == "setup"  # the push does not end a quarter's setup
    assert ok("setup_launch", "ctl")["status"] == "beta"
    push({**SETTINGS, "status": "production"})
    assert r.status == "production"


def test_a_quarter_learns_its_label_and_the_catch_up_interval_at_each_sync(monkeypatch):
    quarter(monkeypatch, "south")
    directory = [{"id": i, "name": n, "index": x, "status": "active", "population": 3, "codex_id": "", "codex_version": "",
                  "last_sync": 0} for x, (i, n) in enumerate(((CAPITAL, "Agora"), ("south", "South Bank")))]
    reply(CAPITAL, "quarter_sync", directory=directory, codex={"id": "", "version": ""}, index=1, settings=SETTINGS,
          sync_every=7200)
    ok("quarter_sync", "ctl")
    me = next(q for q in ok("quarter_directory", fake_chain.ANONYMOUS)["quarters"] if q["id"] == "south")
    assert me["name"] == "South Bank" and Realm["1"].name == "Agora"
    assert Realm["1"].sync_state["sync_every"] == 7200


def test_a_quarter_catches_up_on_a_timer_from_when_it_starts_and_one_that_seceded_stops(monkeypatch):
    quarter(monkeypatch, "south")
    quarters._catch_up["armed"] = False
    fake_chain.state["timers"].clear()
    quarters.resume()
    quarters.resume()  # armed once, however often the realm starts
    assert [delay for delay, _ in fake_chain.state["timers"]] == [0]
    fake_chain.state["replies"][(CAPITAL, "quarter_sync")] = Exception("capital is upgrading")
    Realm["1"].sync_state = {**Realm["1"].sync_state, "sync_every": 7200}
    fire_timers()  # the sync fails quietly, and the next is armed
    assert [delay for delay, _ in fake_chain.state["timers"]] == [7200]
    Realm["1"].is_quarter = False
    fire_timers()
    assert fake_chain.state["timers"] == []


def test_a_quarter_that_secedes_keeps_its_last_values_and_can_change_them(monkeypatch):
    quarter(monkeypatch, "south")
    push(SETTINGS)
    ok("quarter_secede", "ctl")
    assert Realm["1"].name == "Agora"
    ok("realm_configure", "ctl", name="South Bank")
    assert Realm["1"].name == "South Bank"


# ---- Joining


def test_a_realm_with_another_currency_cannot_join_and_a_blank_one_takes_the_capitals():
    capital_info = {"timezone": "UTC", "welcome_message": "Welcome", "accounting_currency": "AGR",
                    "accounting_currency_decimals": 2}
    world.build()
    reply("agora", "realm_info", realm=capital_info)
    for code, decimals in (("EUR", 2), ("AGR", 8)):
        ok("realm_configure_tokens", "ctl", accounting_currency=code, accounting_currency_decimals=decimals)
        out = call("quarter_join_federation", "ctl", capital="agora")
        assert out["kind"] == "conflict" and "change this realm's accounting currency first" in out["error"], out
    ok("realm_configure_tokens", "ctl", accounting_currency="", accounting_currency_decimals=8)
    ok("quarter_join_federation", "ctl", capital="agora")
    r = Realm["1"]
    assert (r.accounting_currency, r.accounting_currency_decimals, r.welcome_message) == ("AGR", 2, "Welcome")


def test_a_realm_with_a_welcome_message_keeps_it_when_it_joins():
    world.build()
    reply("agora", "realm_info", realm={"timezone": "UTC", "welcome_message": "Welcome", "accounting_currency": "",
                                        "accounting_currency_decimals": 8})
    ok("realm_configure", "ctl", welcome_message="Ours")
    ok("quarter_join_federation", "ctl", capital="agora")
    assert Realm["1"].welcome_message == "Ours"


def test_the_join_arms_the_catch_up():
    world.build()
    quarters._catch_up["armed"] = False
    reply("agora", "realm_info", realm={"timezone": "UTC"})
    fake_chain.state["timers"].clear()
    ok("quarter_join_federation", "ctl", capital="agora")
    assert [delay for delay, _ in fake_chain.state["timers"]] == [quarters.SYNC_EVERY]
