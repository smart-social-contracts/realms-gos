import json

import fake_chain
from ic_python_db import Database
from api import federation
import world
from core import op
from ggg import Delegation, Department, FederalVote, FederalVoteLeg, FederationMessage, Proposal, Realm, User, UserProfile
from kit import call, denied, later, ok
from test_quarters import CAPITAL, capital, quarter, reply

ANON = fake_chain.ANONYMOUS
QUARTERS = ("north", "south", "harbor")
ACTION = {"op": "governance_update", "args": {"threshold": 60}}


@op.op(None)
def t_whoami(c, args):
    return {"user": c.user, "by": c.by}


def open_fv(*quarters, rule=None):
    capital(*quarters)
    if rule:
        ok("quarter_configure", "ctl", rule=rule)
    for q in quarters:
        reply(q, "federation_message", proposal="P-1")
    return ok("federal_vote_propose", "alice", title="Adopt tax policy 3", action=ACTION)["vote"]


def leg(q, vote, outcome, yes=0, no=0, **extra):
    v = FederalVote[vote]
    data = {"vote": vote, "outcome": outcome, "yes": yes, "no": no, "abstain": 0, "eligible": yes + no, "hash": v.hash}
    return call("quarter_report", q, population=yes + no, leg={**data, **extra})


def local_vote(vote, choice, *voters):
    p = FederalVoteLeg[f"{vote}:{fake_chain.SELF}"].proposal_id
    for v in voters:
        ok("proposal_vote", v, proposal=p, choice=choice)


def test_fv3_passes_with_3_of_4_quarters():
    v = open_fv(*QUARTERS)
    fv = FederalVote[v]
    assert fv.status == "open" and len(list(fv.legs)) == 4 and fv.hash == federation.freeze(fv.action, fv.rule, fv.deadline)
    opened = [json.loads(a) for cid, m, a in fake_chain.state["calls"] if a and "federal_vote.open" in a]
    assert len(opened) == 3 and all(o["body"]["hash"] == fv.hash for o in opened)
    local_vote(v, "yes", "alice", "bob", "dana", "carla")
    local_vote(v, "no", "mallory")
    later(8 * 86400)
    assert "waiting for legs from" in call("federal_vote_finalize", ANON, vote=v)["error"]
    assert leg("north", v, "passed", 812, 301)["ok"] and leg("south", v, "passed", 500, 20)["ok"]
    assert leg("harbor", v, "failed", 40, 90)["ok"]
    out = ok("federal_vote_finalize", ANON, vote=v)
    assert (out["status"], out["yes"], out["no"]) == ("passed", 3, 1)
    assert Realm["1"].manifest_data["governance"]["threshold"] == 60
    sent = [cid for cid, m, a in fake_chain.state["calls"] if a and "federal_vote.result" in a]
    assert sorted(sent) == sorted(QUARTERS)
    assert "already passed" in call("federal_vote_finalize", ANON, vote=v)["error"]


def test_I_FED_2_rule_and_absence_policy_decide():
    v = open_fv(*QUARTERS, rule={"absent": "no"})
    assert FederalVote[v].rule["absent"] == "no"
    local_vote(v, "yes", "alice")
    assert "match" in leg("north", v, "passed", 1, 0, hash="sha256:forged")["error"]
    leg("north", v, "passed", 9, 1)
    assert "waiting" in call("federal_vote_finalize", ANON, vote=v)["error"]
    later(8 * 86400)
    out = ok("federal_vote_finalize", ANON, vote=v)
    assert (out["status"], out["yes"], out["no"], out["absent"]) == ("failed", 2, 2, 2)
    assert Realm["1"].manifest_data.get("governance", {}).get("threshold") is None


def test_I_FED_2_votes_by_members_when_the_rule_says_so():
    v = open_fv("north", rule={"kind": "majority_of_members"})
    local_vote(v, "yes", "alice", "bob")
    later(8 * 86400)
    leg("north", v, "failed", 10, 30)
    out = ok("federal_vote_finalize", ANON, vote=v)
    assert (out["status"], out["yes"], out["no"]) == ("failed", 12, 30)


def test_I_FED_4_only_the_capital_opens_votes_on_a_quarter(monkeypatch):
    quarter(monkeypatch, "north", "south")
    body = {"vote": "FV-1", "action": ACTION, "rule": {"kind": "majority_of_quarters"}, "deadline": 1, "proposer": "bob",
            "origin": CAPITAL}
    body["hash"] = federation.freeze(body["action"], body["rule"], body["deadline"])
    assert "needs quarter.sync" in denied("federation_message", "mallory", topic="federal_vote.open", body=body)
    assert "capital" in call("federation_message", "south", topic="federal_vote.open", body=body)["error"]
    assert "hash" in call("federation_message", CAPITAL, topic="federal_vote.open",
                          body={**body, "deadline": 2})["error"]
    assert ok("federation_message", CAPITAL, topic="federal_vote.open", body=body)["proposal"]
    assert FederalVote.count() == 1


def test_a_stored_federal_action_naming_a_member_runs_as_the_realm_itself():
    v = open_fv(*QUARTERS)
    FederalVote[v].action = {"op": "t_whoami", "args": {"on_behalf_of": "alice"}}
    local_vote(v, "yes", "alice", "bob", "dana", "carla")
    later(8 * 86400)
    for q in QUARTERS:
        leg(q, v, "passed", 10, 1)
    assert ok("federal_vote_finalize", ANON, vote=v)["status"] == "passed"
    assert FederalVote[v].tally["result"]["user"] == FederalVote[v].tally["result"]["by"] == fake_chain.SELF


def test_quarter_settles_and_reports_its_leg(monkeypatch):
    quarter(monkeypatch, "north")
    deadline = fake_chain.now() + 7 * 86400
    body = {"vote": "FV-3", "action": ACTION, "rule": {"kind": "majority_of_quarters"}, "deadline": deadline,
            "proposer": "bob", "origin": CAPITAL}
    body["hash"] = federation.freeze(ACTION, body["rule"], deadline)
    p = ok("federation_message", CAPITAL, topic="federal_vote.open", body=body)["proposal"]
    assert Proposal[p].deadline == deadline
    local_vote("FV-3", "yes", "alice", "bob")
    assert "still open" in call("federal_vote_finalize", ANON, vote="FV-3")["error"]
    later(8 * 86400)
    reply(CAPITAL, "quarter_report", drifting=False)
    out = ok("federal_vote_finalize", ANON, vote="FV-3")
    assert out["leg"] == "passed" and out["reported"]
    sent = json.loads(fake_chain.state["calls"][-1][2])
    assert sent["leg"] == {"vote": "FV-3", "outcome": "passed", "yes": 2, "no": 0, "abstain": 0, "eligible": 7,
                           "hash": body["hash"]}
    assert "match" in call("federation_message", CAPITAL, topic="federal_vote.result",
                           body={"vote": "FV-3", "status": "passed", "hash": "x"})["error"]
    ok("federation_message", CAPITAL, topic="federal_vote.result",
       body={"vote": "FV-3", "status": "passed", "hash": body["hash"]})
    assert FederalVote["FV-3"].status == "passed" and Realm["1"].manifest_data["governance"]["threshold"] == 60


def test_propose_from_a_quarter_goes_to_the_capital(monkeypatch):
    quarter(monkeypatch, "north")
    reply(CAPITAL, "federation_message", vote="FV-7")
    assert ok("federal_vote_propose", "bob", title="x", action=ACTION)["vote"] == "FV-7"
    sent = json.loads(fake_chain.state["calls"][-1][2])
    assert sent["topic"] == "federal_vote.propose" and sent["body"]["proposer"] == "bob"
    assert "needs federal_vote.propose" in denied("federal_vote_propose", "eve", title="x", action=ACTION)


def test_capital_opens_a_vote_proposed_by_a_quarter():
    capital("north")
    reply("north", "federation_message", proposal="P-2")
    out = ok("federation_message", "north", topic="federal_vote.propose", body={"action": ACTION, "proposer": "bob"})
    assert FederalVote[out["vote"]].origin_quarter == "north"
    assert "unknown operation" in call("federal_vote_propose", "alice", title="x", action={"op": "nope"})["error"]


def test_a_federal_vote_action_cannot_act_on_behalf_of_a_member():
    capital()
    action = {"op": "proposal_vote", "args": {"proposal": "P-1", "choice": "no", "on_behalf_of": "bob"}}
    assert "on_behalf_of" in denied("federal_vote_propose", "alice", title="x", action=action)
    assert FederalVote.count() == 0


def test_claude_proposes_for_alice():
    capital()
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["federal_vote.propose"]}, expires_at=fake_chain.now() + 60)
    v = ok("federal_vote_propose", "claude-conn", title="x", action=ACTION, on_behalf_of="alice")["vote"]
    assert Proposal[FederalVoteLeg[f"{v}:{CAPITAL}"].proposal_id].proposer == "alice"


def test_cancel_is_governed_and_withdraws_every_leg():
    v = open_fv("north")
    assert "needs federal_vote.manage" in denied("federal_vote_cancel", "alice", vote=v)
    world.policy(Department["Administration"], 2, 3)
    assert ok("federal_vote_cancel", "ctl", vote=v)["requires_vote"]
    world.policy(Department["Administration"], 1, 1)
    ok("federal_vote_cancel", "ctl", vote=v)
    assert FederalVote[v].status == "cancelled"
    assert Proposal[FederalVoteLeg[f"{v}:{CAPITAL}"].proposal_id].status == "withdrawn"
    assert "federal_vote.cancel" in fake_chain.state["calls"][-1][2]
    assert "cancelled" in call("federal_vote_finalize", ANON, vote=v)["error"]


def test_votes_and_messages_go_to_active_quarters_only():
    capital(*QUARTERS)
    ok("quarter_configure", "ctl", quarter="harbor", status="suspended")
    for q in QUARTERS:
        reply(q, "federation_message", proposal="P-1")
    v = ok("federal_vote_propose", "alice", title="x", action=ACTION)["vote"]
    assert {leg.quarter for leg in FederalVote[v].legs} == {CAPITAL, "north", "south"}
    out = ok("federation_message", "ctl", topic="codex", body={"id": "agora", "version": "1.5.0"})
    assert sorted(out["replies"]) == ["north", "south"]


def test_capital_announces_codex_to_its_quarters(monkeypatch):
    capital("north", "south")
    assert "needs quarter.sync" in denied("federation_message", "alice", topic="codex")
    reply("north", "federation_message", drifting=False)
    out = ok("federation_message", "ctl", topic="codex", body={"id": "agora", "version": "1.5.0"})
    assert out["replies"]["north"]["ok"] and not out["replies"]["south"]["ok"]
    assert FederationMessage[out["message"]].source == CAPITAL
    Database.get_instance().clear()
    fake_chain.reset()
    quarter(monkeypatch, "north")
    assert ok("federation_message", CAPITAL, topic="codex", body={"id": "agora", "version": "1.5.0"})["drifting"]
    assert Realm["1"].sync_state["codex"]["version"] == "1.5.0"


def test_a_passed_federal_vote_can_assign_a_role():
    v = open_fv(*QUARTERS)
    UserProfile(id="clerk", allowed_to=["user.view"])
    FederalVote[v].action = {"op": "role_assign", "args": {"user": "carla", "profile": "clerk"}}
    local_vote(v, "yes", "alice", "bob", "dana", "carla")
    later(8 * 86400)
    for q in QUARTERS:
        leg(q, v, "passed", 10, 1)
    assert ok("federal_vote_finalize", ANON, vote=v)["status"] == "passed"
    assert "clerk" in [p.id for p in User["carla"].profiles], FederalVote[v].tally
