import json

from core import auth, op
import fake_chain
from api import quarters
import world
from ggg import Codex, Department, Forward, GuestUser, Member, Proposal, Quarter, Realm, User
from kit import call, denied, ok, rows

CAPITAL = fake_chain.SELF


def reply(cid, method, **out):
    fake_chain.state["replies"][(cid, method)] = json.dumps({"ok": True, **out})


def capital(*names):
    """Agora's capital with its quarters registered and reporting."""
    world.build()
    Realm["1"].status = "beta"
    for name in names:
        ok("quarter_register", "ctl", quarter=name, name=name.title())
        ok("quarter_report", name, population=0)


def quarter(monkeypatch, me, *peers):
    """Run as quarter ``me`` of Agora; the capital and ``peers`` are known through sync."""
    monkeypatch.setattr(fake_chain, "SELF", me)
    world.build()
    r = Realm["1"]
    r.status, r.is_quarter, r.is_capital, r.federation_realm_id = "beta", True, False, CAPITAL
    for i, cid in enumerate((CAPITAL,) + peers):
        Quarter(id=cid, name=cid.title(), index=i, status="active")


def test_register_south_story():
    capital("north")
    ok("quarter_report", "north", population=1240, codex_version="")
    out = ok("quarter_register", "ctl", quarter="south", name="South")
    assert out["index"] == 2 and Quarter["south"].status == "setup"
    assert "already" in call("quarter_register", "ctl", quarter="south")["error"]
    synced = ok("quarter_sync", "south", population=0)
    assert Quarter["south"].status == "active" and synced["index"] == 2
    assert {q["id"] for q in synced["directory"]} == {CAPITAL, "north", "south"}
    rows = {q["id"]: q for q in ok("quarter_directory", fake_chain.ANONYMOUS)["quarters"]}
    assert rows["north"]["population"] == 1240 and rows[CAPITAL]["capital"]
    assert ok("join_targets", fake_chain.ANONYMOUS)["default"] == "south"


def test_register_deregister_configure_are_governed_and_need_permission():
    capital("north")
    for name, args in (("quarter_register", {"quarter": "south"}), ("quarter_deregister", {"quarter": "north"}),
                       ("quarter_configure", {"quarter": "north", "status": "suspended"})):
        assert "needs quarter." in denied(name, "alice", **args)
    world.policy(Department["Administration"], 2, 3)
    assert ok("quarter_register", "ctl", quarter="south")["requires_vote"] and Quarter["south"] is None


def test_configure_follows_the_quarter_lifecycle():
    capital("north")
    ok("quarter_configure", "ctl", quarter="north", status="suspended", name="North")
    assert "cannot become splitting" in call("quarter_configure", "ctl", quarter="north", status="splitting")["error"]
    ok("quarter_configure", "ctl", quarter="north", status="active")
    assert "threshold" in call("quarter_configure", "ctl", rule={"threshold": 100})["error"]
    rule = ok("quarter_configure", "ctl", rule={"absent": "no"}, capacity=500)["rule"]
    assert rule["absent"] == "no" and quarters.scale()["capacity"] == 500
    ok("quarter_deregister", "ctl", quarter="north")
    assert Quarter["north"] is None


def test_I_FED_4_only_registered_quarter_canisters_report():
    capital("north")
    assert "needs quarter.sync" in denied("quarter_report", "harbor", population=5)
    assert "needs quarter.sync" in denied("quarter_sync", "alice", population=5)
    assert "not a registered quarter" in denied("quarter_report", "ctl", population=5)
    ok("quarter_report", "north", population=5)
    assert Quarter["north"].population == 5


def test_I_FED_3_quarters_on_another_codex_are_flagged_drifting():
    capital("north", "south")
    Codex(id="agora", version="1.4.2")
    assert not ok("quarter_report", "north", codex_id="agora", codex_version="1.4.2")["drifting"]
    assert ok("quarter_report", "south", codex_id="agora", codex_version="1.4.1")["drifting"]
    rows = {q["id"]: q["drifting"] for q in ok("quarter_directory", fake_chain.ANONYMOUS)["quarters"]}
    assert rows == {CAPITAL: False, "north": False, "south": True}


def test_scale_status_records_intent_only():
    capital("north")
    Realm["1"].network = "test"
    assert not ok("scale_status", fake_chain.ANONYMOUS)["wanted"]
    ok("quarter_report", "north", population=9)
    s = ok("scale_status", fake_chain.ANONYMOUS)
    assert s["wanted"] and s["in_flight"] and s["requested_at"] == fake_chain.now() and s["scale_at"] == 9
    ok("quarter_register", "ctl", quarter="east")
    assert not Realm["1"].scale_in_flight


def test_I_FED_1_bob_moves_to_south(monkeypatch):
    quarter(monkeypatch, "north", "south")
    reply("south", "federation_message", member="bob")
    out = ok("self_change_quarter", "bob", quarter="south")
    cid, method, arg = fake_chain.state["calls"][-1]
    body = json.loads(arg)["body"]
    assert (cid, method, json.loads(arg)["topic"]) == ("south", "federation_message", "user.arrive")
    f = Forward[out["forward"]]
    assert (f.subject, f.origin, f.destination, f.signature) == ("User:bob", "north", "south", body["signature"])
    assert Member["bob"] is None and GuestUser["bob"].home_quarter == "south" and User["bob"].home_quarter == "south"
    assert "proposal.vote" not in auth.permissions("bob")
    assert "needs self.change_quarter" in denied("self_change_quarter", "bob", quarter="south")


def test_bob_arrives_in_south(monkeypatch):
    quarter(monkeypatch, "south", "north")
    Member["bob"].delete()
    GuestUser(id="bob", home_quarter="north")
    moved = {"user": "bob", "from": "north", "moved_at": fake_chain.now()}
    sig = quarters.digest({"subject": "User:bob", "from": "north", "to": "south", "moved_at": moved["moved_at"]})
    assert "signature" in call("federation_message", "north", topic="user.arrive", body={**moved, "signature": "x"})["error"]
    ok("federation_message", "north", topic="user.arrive", body={**moved, "signature": sig})
    assert Member["bob"].status == "active" and User["bob"].home_quarter == "south" and GuestUser["bob"] is None
    assert "needs quarter.sync" in denied("federation_message", "harbor", topic="user.arrive", body=moved)


def test_change_quarter_rules_and_delegation(monkeypatch):
    quarter(monkeypatch, "north", "south")
    reply("south", "federation_message")
    assert "not an active quarter" in call("self_change_quarter", "alice", quarter="west")["error"]
    Member["mallory"].status = "suspended"
    assert "only an active member" in call("self_change_quarter", "mallory", quarter="south")["error"]
    assert "needs self.change_quarter" in denied("self_change_quarter", "eve", quarter="south")
    assert "needs self.change_quarter" in denied("self_change_quarter", "claude-conn", quarter="south")
    from ggg import Delegation
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["self.change_quarter"]}, expires_at=fake_chain.now() + 60)
    ok("self_change_quarter", "claude-conn", quarter="south", on_behalf_of="alice")
    assert Member["alice"] is None


def test_quarter_syncs_with_its_capital(monkeypatch):
    quarter(monkeypatch, "north", "gone")
    directory = [{"id": i, "name": i, "index": n, "status": "active", "population": 3, "codex_id": "agora",
                  "codex_version": "1.4.2", "last_sync": 0} for n, i in enumerate((CAPITAL, "north", "south"))]
    reply(CAPITAL, "quarter_sync", directory=directory, codex={"id": "agora", "version": "1.4.2"}, index=1)
    out = ok("quarter_sync", "ctl")
    assert out["drifting"] and Quarter["gone"] is None and Quarter["south"].population == 3
    assert Realm["1"].sync_state["index"] == 1 and Quarter["north"] is None
    assert json.loads(fake_chain.state["calls"][-1][2])["population"] == len(world.PEOPLE)


def test_south_secedes(monkeypatch):
    quarter(monkeypatch, "south", "north")
    assert "needs quarter.secede" in denied("quarter_secede", "alice")
    ok("quarter_secede", "ctl")
    r = Realm["1"]
    assert (r.is_quarter, r.is_capital, r.federation_realm_id) == (False, True, "") and Quarter.count() == 0
    assert fake_chain.state["calls"][-1][:2] == (CAPITAL, "federation_message")
    assert "only a quarter" in call("quarter_secede", "ctl")["error"]


def test_capital_forgets_a_seceded_quarter():
    capital("south")
    ok("federation_message", "south", topic="quarter.secede")
    assert Quarter["south"] is None


def test_harbor_joins_agora():
    world.build()
    assert "needs quarter.join_federation" in denied("quarter_join_federation", "alice", capital="agora")
    ok("quarter_join_federation", "ctl", capital="agora")
    r = Realm["1"]
    assert r.is_quarter and not r.is_capital and Quarter["agora"].index == 0 and quarters.capital() == "agora"
    assert "already belongs" in call("quarter_join_federation", "ctl", capital="other")["error"]


def test_population_counts_active_members_only():
    capital()
    assert ok("quarter_directory", fake_chain.ANONYMOUS)["quarters"][0]["population"] == len(world.PEOPLE)
    ok("user_set_status", "ctl", user="mallory", status="suspended")
    ok("user_remove", "ctl", user="alice")
    assert ok("scale_status", fake_chain.ANONYMOUS)["populations"][CAPITAL] == len(world.PEOPLE) - 2
    assert quarters.report()["population"] == Member.count_by("status", "active") == len(world.PEOPLE) - 2


CONDUCTOR = "conductor"


def fire_timers():
    timers, fake_chain.state["timers"] = fake_chain.state["timers"], []
    for _, cb in timers:
        out = cb()
        if out is not None:
            op.run(out)


def on_casals(*names):
    capital(*names)
    r = Realm["1"]
    r.manifest_data = {**r.manifest_data, "casals": {"conductor_canister_id": CONDUCTOR, "casals_stand": "agora"}}


def built(member, cid, status="ready"):
    fake_chain.state["replies"][(CONDUCTOR, "orchestra_get")] = json.dumps(
        {"ok": True, "canisters": [{"name": member, "canister_id": cid, "status": status}]})


def test_quarter_add_asks_casals_and_adopts_the_quarter_it_builds():
    on_casals("north")
    reply(CONDUCTOR, "stand_grow", name="agora", members=["agora-quarter-2"])
    out = ok("quarter_add", "ctl", name="South")
    assert out["member"] == "agora-quarter-2"
    assert fake_chain.state["calls"][-1] == (CONDUCTOR, "stand_grow",
                                             json.dumps({"stand": "agora", "members": ["agora-quarter-2"]}))
    s = ok("scale_status", fake_chain.ANONYMOUS)
    assert s["in_flight"] and s["pending"]["member"] == "agora-quarter-2"
    assert "still being built" in call("quarter_add", "ctl")["error"]
    built("agora-quarter-2", "", status="created")
    fire_timers()
    assert Quarter.count() == 1 and len(fake_chain.state["timers"]) == 1
    built("agora-quarter-2", "south-id")
    reply("south-id", "quarter_join_federation", capital=CAPITAL)
    fire_timers()
    assert (fake_chain.state["calls"][-1][:2] == ("south-id", "quarter_join_federation")
            and json.loads(fake_chain.state["calls"][-1][2]) == {"capital": CAPITAL})
    south = Quarter["south-id"]
    assert (south.name, south.index, south.status) == ("South", 2, "setup")
    s = ok("scale_status", fake_chain.ANONYMOUS)
    assert not s["in_flight"] and s["pending"] is None and fake_chain.state["timers"] == []


def test_quarter_add_refusals_and_a_failed_join_is_retried():
    capital()
    assert "conductor and stand" in call("quarter_add", "ctl")["error"]
    on_casals()
    fake_chain.state["replies"][(CONDUCTOR, "stand_grow")] = json.dumps({"ok": False, "error": "no such row"})
    assert "no such row" in call("quarter_add", "ctl")["error"]
    denied("quarter_add", "alice")
    reply(CONDUCTOR, "stand_grow")
    ok("quarter_add", "ctl")
    built("agora-quarter-1", "east-id")
    fake_chain.state["replies"][("east-id", "quarter_join_federation")] = json.dumps({"ok": False, "error": "trap"})
    fire_timers()
    assert "trap" in ok("scale_status", fake_chain.ANONYMOUS)["pending"]["error"] and Quarter.count() == 0
    reply("east-id", "quarter_join_federation")
    fire_timers()
    assert Quarter["east-id"].index == 1


def test_a_quarter_still_being_built_is_looked_for_after_an_upgrade():
    on_casals()
    reply(CONDUCTOR, "stand_grow")
    ok("quarter_add", "ctl")
    fake_chain.state["timers"].clear()
    quarters.resume()
    assert len(fake_chain.state["timers"]) == 1


def test_the_realm_proposes_its_next_quarter_when_one_is_wanted(monkeypatch):
    on_casals("north")
    r = Realm["1"]
    r.network, r.auto_scale_enabled = "test", True
    ok("quarter_report", "north", population=9)
    filed = [p for p in rows(Proposal) if (p.action or {}).get("op") == "quarter_add"]
    assert len(filed) == 1 and filed[0].proposer == CAPITAL and filed[0].status == "voting"
    ok("quarter_report", "north", population=10)
    assert len([p for p in rows(Proposal) if (p.action or {}).get("op") == "quarter_add"]) == 1


def test_a_quarter_cannot_add_quarters(monkeypatch):
    quarter(monkeypatch, "south", "north")
    assert "only the capital" in call("quarter_add", "ctl")["error"]


def test_the_members_vote_the_realms_own_proposal_and_casals_is_asked():
    on_casals("north")
    r = Realm["1"]
    r.network, r.auto_scale_enabled = "test", True
    ok("governance_update", "ctl", voting_window=0)
    ok("quarter_report", "north", population=9)
    p = next(p for p in rows(Proposal) if (p.action or {}).get("op") == "quarter_add")
    reply(CONDUCTOR, "stand_grow")
    for voter in ("alice", "bob", "dana"):
        ok("proposal_vote", voter, proposal=p.id, choice="yes")
    assert ok("proposal_vote", "carla", proposal=p.id, choice="yes")["status"] == "executed"
    assert fake_chain.state["calls"][-1][:2] == (CONDUCTOR, "stand_grow")
    assert ok("scale_status", fake_chain.ANONYMOUS)["pending"]["member"] == "agora-quarter-2"
