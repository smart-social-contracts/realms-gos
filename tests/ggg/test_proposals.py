import fake_chain
import world
from ggg import Delegation, Department, Mandate, Proposal, Vote
from kit import call, denied, later, ok


def vote_all(proposal, choice, *voters):
    for v in voters:
        ok("proposal_vote", v, proposal=proposal, choice=choice)


def test_realm_wide_vote_passes_after_deadline():
    world.build()
    p = ok("proposal_create", "alice", title="Bike lane on Main St")["proposal"]
    vote_all(p, "yes", "alice", "bob", "dana", "carla")
    vote_all(p, "no", "mallory")
    assert "still open" in call("proposal_finalize", "ctl", proposal=p)["error"]
    later(8 * 86400)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "accepted"


def test_vote_is_replaced_until_deadline_then_closed():
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    ok("proposal_vote", "bob", proposal=p, choice="yes")
    ok("proposal_vote", "bob", proposal=p, choice="no")
    assert Vote[f"{p}:bob"].choice == "no"
    later(8 * 86400)
    assert "not open" in call("proposal_vote", "bob", proposal=p, choice="yes")["error"]


def test_claude_votes_for_alice():
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    Delegation(id="d1", grantor="alice", delegate="claude-conn", status="active",
               scope={"operations": ["proposal.vote"]}, expires_at=fake_chain.now() + 86400)
    ok("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="alice")
    v = Vote[f"{p}:alice"]
    assert (v.voter, v.cast_by) == ("alice", "claude-conn")
    denied("proposal_create", "claude-conn", title="y", on_behalf_of="alice")


def test_I_GOV_2_only_eligible_voters_count():
    depts = world.build()
    world.policy(depts["Civil Registry"], 2, 3)
    p = ok("proposal_create", "dana", title="Close the office on Fridays", department="Civil Registry")["proposal"]
    assert "not eligible" in call("proposal_vote", "alice", proposal=p, choice="yes")["error"]
    vote_all(p, "yes", "dana", "bob")
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "accepted"


def test_I_GOV_3_quorum_and_threshold():
    world.build()
    ok("governance_update", "ctl", quorum_percent=50)
    p = ok("proposal_create", "alice", title="x")["proposal"]
    vote_all(p, "yes", "alice", "bob")
    later(8 * 86400)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "rejected"


def test_veto():
    depts = world.build()
    world.policy(depts["Civil Registry"], 1, 3, veto=["dana"])
    p = ok("proposal_create", "bob", title="x", department="Civil Registry")["proposal"]
    ok("proposal_vote", "dana", proposal=p, choice="no")
    later(8 * 86400)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "rejected"


def test_I_GOV_1_governed_call_becomes_a_proposal_and_replays_once():
    world.build()
    world.policy(Department["Administration"], 2, 3)
    out = ok("mandate_create", "ctl", title="Road works", scope={"max_value": 200000}, executor="Infrastructure")
    assert out["requires_vote"] and Mandate.count() == 0
    p = out["proposal"]
    vote_all(p, "yes", *world.PEOPLE)
    later(8 * 86400)
    ok("proposal_finalize", "ctl", proposal=p)
    assert ok("proposal_execute", "ctl", proposal=p)["status"] == "executed"
    assert Mandate.count() == 1 and Mandate["M-1"].executor.id == "Infrastructure"
    assert "no accepted action" in call("proposal_execute", "ctl", proposal=p)["error"]
    assert Proposal[p].status == "executed"


def test_I_GOV_4_contract_stays_within_mandate():
    depts = world.build()
    world.grant(depts["Infrastructure"], "contract.create_under_mandate")
    m = ok("mandate_create", "ctl", title="Road works", scope={"max_value": 200000},
           executor="Infrastructure", expires_at=fake_chain.now() + 1000)["mandate"]
    assert "exceeds" in call("contract_create", "carla", title="C", mandate=m, value=300000)["error"]
    assert "outlasts" in call("contract_create", "carla", title="C", mandate=m, ends_at=fake_chain.now() + 2000)["error"]
    k = ok("contract_create", "carla", title="Bike lane works", mandate=m, value=50000,
           parties=["Organization:buildco", "Department:Infrastructure"])["contract"]
    ok("contract_terminate", "ctl", contract=k)


def test_only_the_executor_department_contracts_under_a_mandate():
    depts = world.build()
    world.grant(depts["Infrastructure"], "contract.create_under_mandate")
    world.profile("contractor", "contract.create_under_mandate")
    world.person("bob", "contractor")
    m = ok("mandate_create", "ctl", title="Road works", executor="Infrastructure")["mandate"]
    assert "executor department" in call("contract_create", "bob", title="C", mandate=m)["error"]
    assert "executor department" in call("contract_create", "ctl", title="C", mandate=m)["error"]
    ok("contract_create", "bob", title="Office chairs")
    ok("contract_create", "carla", title="Bike lane works", mandate=m)
    depts["Infrastructure"].head = "bob"
    ok("contract_create", "bob", title="Resurfacing", mandate=m)
    loose = ok("mandate_create", "ctl", title="Unassigned")["mandate"]
    assert "executor department" in call("contract_create", "carla", title="C", mandate=loose)["error"]


def test_tally_is_kept_as_votes_are_cast_and_replaced():
    depts = world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    vote_all(p, "yes", "alice", "bob")
    ok("proposal_vote", "bob", proposal=p, choice="no")
    out = ok("proposal_get", "alice", proposal=p)
    assert out["tally"] == {"yes": 1, "no": 1, "abstain": 0} and out["eligible"] == len(world.PEOPLE)
    ok("user_set_status", "ctl", user="mallory", status="suspended")
    assert "not eligible" in call("proposal_vote", "mallory", proposal=p, choice="yes")["error"]
    assert ok("proposal_get", "alice", proposal=p)["eligible"] == len(world.PEOPLE) - 1
    world.policy(depts["Civil Registry"], 1, 2)
    d = ok("proposal_create", "dana", title="y", department="Civil Registry")["proposal"]
    assert ok("proposal_get", "dana", proposal=d)["eligible"] == 2


def test_proposals_and_votes_are_paginated():
    world.build()
    ids = [ok("proposal_create", "alice", title=f"p{i}")["proposal"] for i in range(3)]
    ok("proposal_withdraw", "alice", proposal=ids[1])
    first = ok("proposal_list", "bob", count=2)
    rest = ok("proposal_list", "bob", count=2, **{"from": first["next"]})
    assert [x["id"] for x in first["proposals"] + rest["proposals"]] == ids and rest["next"] is None
    voting = ok("proposal_list", "bob", status="voting", count=1)
    more = ok("proposal_list", "bob", status="voting", count=1, **{"from": voting["next"]})
    assert [x["id"] for x in voting["proposals"] + more["proposals"]] == [ids[0], ids[2]] and more["next"] is None
    vote_all(ids[0], "yes", "alice", "bob", "dana")
    page = ok("proposal_get", "bob", proposal=ids[0], count=2)
    tail = ok("proposal_get", "bob", proposal=ids[0], count=2, **{"from": page["next"]})
    assert [v["voter"] for v in page["votes"] + tail["votes"]] == ["alice", "bob", "dana"] and tail["next"] is None


def test_withdraw_only_own():
    world.build()
    p = ok("proposal_create", "bob", title="x")["proposal"]
    assert "not your proposal" in call("proposal_withdraw", "alice", proposal=p)["error"]
    ok("proposal_withdraw", "bob", proposal=p)
