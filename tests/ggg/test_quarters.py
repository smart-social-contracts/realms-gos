import json

from core import auth
import fake_chain
from api import quarters
import world
from ggg import Codex, Department, Forward, GuestUser, Member, Quarter, Realm, User
from kit import call, denied, ok

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
