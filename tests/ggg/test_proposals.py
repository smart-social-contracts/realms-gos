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
    world.build()
    m = ok("mandate_create", "ctl", title="Road works", scope={"max_value": 200000},
           executor="Infrastructure", expires_at=fake_chain.now() + 1000)["mandate"]
    assert "exceeds" in call("contract_create", "ctl", title="C", mandate=m, value=300000)["error"]
    assert "outlasts" in call("contract_create", "ctl", title="C", mandate=m, ends_at=fake_chain.now() + 2000)["error"]
    k = ok("contract_create", "ctl", title="Bike lane works", mandate=m, value=50000,
           parties=["Organization:buildco", "Department:Infrastructure"])["contract"]
    ok("contract_terminate", "ctl", contract=k)


def test_withdraw_only_own():
    world.build()
    p = ok("proposal_create", "bob", title="x")["proposal"]
    assert "yours" in call("proposal_withdraw", "alice", proposal=p)["error"]
    ok("proposal_withdraw", "bob", proposal=p)
