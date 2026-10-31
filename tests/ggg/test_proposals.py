from types import SimpleNamespace

import fake_chain
import world
from api import members, proposals
from core import auth, op
from core.db import Forbidden
from ggg import Delegation, Department, Mandate, Position, Proposal, Realm, User, UserProfile, Vote
from kit import call, denied, later, ok


@op.op("proposal.vote", delegable=True, governed=lambda a: Department["Infrastructure"])
def t_governed_whoami(c, args):
    return {"user": c.user, "by": c.by}


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
    assert Proposal[p].status == "accepted"


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
    assert Proposal[p].status == "rejected"


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


def test_a_department_proposal_cannot_carry_an_action_outside_the_department():
    world.build()
    realm_wide = [("mandate_create", {"title": "Road works", "executor": "Infrastructure"}),
                  ("governance_update", {"threshold": 0, "voting_window": 1})]
    for name, args in realm_wide:
        out = call("proposal_create", "carla", title=name, department="Infrastructure", action={"op": name, "args": args})
        assert not out["ok"] and out["kind"] == "forbidden" and "only a realm-wide proposal" in out["error"]
    assert call("proposal_create", "carla", title="x", department="Infrastructure",
                action={"op": "contract_create", "args": {"title": "C"}})["kind"] == "forbidden"
    assert Proposal.count() == 0


def test_a_stored_department_action_outside_the_department_fails_without_running():
    world.build()
    for name, args in (("mandate_create", {"title": "Road works"}), ("governance_update", {"threshold": 0})):
        p = ok("proposal_create", "carla", title=name, department="Infrastructure")["proposal"]
        Proposal[p].action = {"op": name, "args": args}
        out = ok("proposal_vote", "carla", proposal=p, choice="yes")
        assert out["status"] == "failed" and "only a realm-wide proposal" in Proposal[p].result["error"]
    assert Mandate.count() == 0 and "governance" not in (Realm["1"].manifest_data or {})


def test_a_stored_department_action_missing_an_argument_fails_without_trapping():
    world.build()
    p = ok("proposal_create", "carla", title="x", department="Infrastructure")["proposal"]
    Proposal[p].action = {"op": "appoint", "args": {}}
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "failed"
    assert "missing" in Proposal[p].result["error"]


def test_a_department_proposal_carries_actions_its_department_governs():
    world.build()
    Department(id="Roads", parent=Department["Infrastructure"])
    for parent in ("Infrastructure", "Roads"):
        action = {"op": "department_create", "args": {"department": f"{parent} crew", "parent": parent}}
        p = ok("proposal_create", "carla", title="crew", department="Infrastructure", action=action)["proposal"]
        assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "executed"
        assert Department[f"{parent} crew"].parent.id == parent


def pass_department_proposal(op_name, args, department="Infrastructure"):
    p = ok("proposal_create", "carla", title=op_name, department=department,
           action={"op": op_name, "args": args})["proposal"]
    ok("proposal_vote", "carla", proposal=p, choice="yes")
    return Proposal[p]


def test_I_ID_3_a_department_proposal_cannot_open_a_seat_with_permissions_the_department_lacks():
    world.build()
    seat = pass_department_proposal("position_create",
                                    {"department": "Infrastructure", "position": "infra.boss", "profile": "admin"})
    assert seat.status == "failed" and "you do not hold" in seat.result["error"]
    assert Position["infra.boss"] is None


def test_I_ID_3_a_department_proposal_cannot_assign_a_profile_the_department_lacks():
    world.build()
    world.seat("infrastructure.registrar", "carla", Department["Infrastructure"], "role.assign")
    UserProfile(id="auditor", allowed_to=["role.assign"])
    assigned = pass_department_proposal("role_assign", {"department": "Infrastructure", "user": "alice",
                                                        "profile": "auditor"})
    assert assigned.status == "executed" and "auditor" in [x.id for x in User["alice"].profiles]
    escalated = pass_department_proposal("role_assign", {"department": "Infrastructure", "user": "carla",
                                                         "profile": "admin"})
    assert escalated.status == "failed" and "all" not in auth.permissions("carla")


def pending_in_deleted_department(perm, action):
    """A proposal filed in a sub-department that is then deleted from under it, as before deletion was refused."""
    world.build()
    sub = Department(id="Sub", parent=Department["Infrastructure"])
    world.seat("sub.officer", "carla", sub, perm)
    p = ok("proposal_create", "carla", title="x", department="Sub", action=action)["proposal"]
    sub.delete()
    assert Proposal[p].department is None
    return p


def assert_never_runs(p, voter):
    assert "not eligible" in call("proposal_vote", voter, proposal=p, choice="yes")["error"]
    Proposal[p].status = "accepted"
    assert ok("proposal_execute", "ctl", proposal=p)["status"] == "failed"
    assert "no longer exists" in Proposal[p].result["error"]
    assert call("proposal_vote", "carla", proposal=p, choice="yes")["ok"] is False


def test_a_deleted_departments_proposal_does_not_turn_realm_wide_to_assign_admin():
    p = pending_in_deleted_department(
        "role.assign", {"op": "role_assign", "args": {"department": "Sub", "user": "carla", "profile": "admin"}})
    assert_never_runs(p, "carla")
    assert "all" not in auth.permissions("carla")


def test_a_deleted_departments_proposal_does_not_turn_realm_wide_to_make_every_member_admin():
    p = pending_in_deleted_department(
        "permission.grant", {"op": "profile_edit", "args": {"department": "Sub", "profile": "member",
                                                            "allowed_to": ["all"]}})
    assert_never_runs(p, "mallory")
    assert "all" not in auth.permissions("mallory")


def test_a_replay_of_a_proposal_whose_department_is_gone_holds_no_permission():
    p = pending_in_deleted_department(
        "role.assign", {"op": "role_assign", "args": {"department": "Sub", "user": "carla", "profile": "admin"}})
    replay = SimpleNamespace(origin="replay", proposal=p, user="carla")
    try:
        members.within(replay, {"role.assign"})
    except Forbidden as e:
        assert "no longer exists" in str(e)
    else:
        raise AssertionError("a replay without a department was let through")


def test_a_department_with_a_proposal_to_vote_on_or_run_cannot_be_deleted():
    world.build()
    Department(id="Sub", parent=Department["Infrastructure"])
    for status in ("draft", "voting", "accepted"):
        p = ok("proposal_create", "carla", title="x", department="Sub")["proposal"]
        Proposal[p].status = status
        out = call("department_delete", "ctl", department="Sub")
        assert out["kind"] == "conflict" and "proposals" in out["error"], (status, out)
        Proposal[p].status = "rejected"
    ok("department_delete", "ctl", department="Sub")
    assert Department["Sub"] is None


def test_a_realm_wide_proposal_carries_any_action():
    world.build()
    action = {"op": "mandate_create", "args": {"title": "Road works"}}
    p = ok("proposal_create", "alice", title="Road works", action=action)["proposal"]
    vote_all(p, "yes", *world.PEOPLE)
    later(8 * 86400)
    ok("proposal_finalize", "ctl", proposal=p)
    assert ok("proposal_execute", "ctl", proposal=p)["status"] == "executed" and Mandate.count() == 1


def test_a_governed_call_under_power_of_attorney_replays_as_the_grantor():
    world.build()
    world.policy(Department["Infrastructure"], 2, 3)
    Department["Infrastructure"].head = "bob"
    Delegation(id="d1", grantor="carla", delegate="claude-conn", status="active",
               scope={"operations": ["proposal.vote"]}, expires_at=fake_chain.now() + 86400)
    p = ok("t_governed_whoami", "claude-conn", on_behalf_of="carla")["proposal"]
    assert (Proposal[p].proposer, Proposal[p].on_behalf_of) == ("carla", "carla")
    vote_all(p, "yes", "carla", "bob")
    assert Proposal[p].status == "executed"
    assert Proposal[p].result["user"] == Proposal[p].result["by"] == "carla"


def test_an_action_cannot_act_on_behalf_of_a_member():
    world.build()
    target = ok("proposal_create", "alice", title="Bike lane on Main St")["proposal"]
    vote = {"op": "proposal_vote", "args": {"proposal": target, "choice": "no"}}
    out = call("proposal_create", "mallory", title="Vote as alice", action={**vote, "args": {**vote["args"], "on_behalf_of": "alice"}})
    assert not out["ok"] and "on_behalf_of" in out["error"]
    assert Proposal.count() == 1


def test_a_stored_action_naming_another_member_runs_as_its_proposer_not_that_member():
    world.build()
    vote = {"op": "proposal_vote", "args": {"proposal": "P-2", "choice": "no"}}
    p = ok("proposal_create", "mallory", title="Vote no", action=vote)["proposal"]
    # A proposal filed before the rule, with the impersonation already in its stored action, both ways.
    Proposal[p].action = {**vote, "args": {**vote["args"], "on_behalf_of": "alice"}, "initiator": "alice"}
    vote_all(p, "yes", *world.PEOPLE)
    later(8 * 86400)
    ok("proposal_finalize", "ctl", proposal=p)
    target = ok("proposal_create", "alice", title="Bike lane on Main St")["proposal"]
    assert target == "P-2"
    out = ok("proposal_execute", "ctl", proposal=p)
    assert out["status"] == "executed", out
    assert Vote[f"{target}:alice"] is None
    assert Vote[f"{target}:mallory"].choice == "no"


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


def fire_timers():
    timers, fake_chain.state["timers"] = fake_chain.state["timers"], []
    for _, cb in timers:
        out = cb()
        if out is not None:
            op.run(out)


def test_a_zero_window_closes_a_vote_once_its_outcome_is_certain_and_runs_its_action():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    action = {"op": "governance_update", "args": {"threshold": 60}}
    p = ok("proposal_create", "alice", title="Raise the threshold", action=action)["proposal"]
    assert Proposal[p].deadline == 0 and fake_chain.state["timers"] == []
    vote_all(p, "yes", "alice", "bob", "dana")
    assert Proposal[p].status == "voting"
    later(30 * 86400)
    out = ok("proposal_vote", "carla", proposal=p, choice="yes")
    assert out["status"] == "executed" and Proposal[p].tally["eligible"] == 7
    assert "not open for voting" in call("proposal_vote", "elena", proposal=p, choice="no")["error"]
    q = ok("proposal_create", "alice", title="Close the park")["proposal"]
    vote_all(q, "no", "bob", "dana", "carla")
    assert Proposal[q].status == "rejected"


def test_everyone_voting_decides_an_open_vote():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="x")["proposal"]
    vote_all(p, "abstain", *world.PEOPLE[:-1])
    assert Proposal[p].status == "voting"
    assert ok("proposal_vote", "mallory", proposal=p, choice="yes")["status"] == "accepted"


def test_a_timed_vote_settles_itself_at_its_deadline():
    world.build()
    action = {"op": "governance_update", "args": {"threshold": 60}}
    p = ok("proposal_create", "alice", title="Raise the threshold", action=action)["proposal"]
    vote_all(p, "yes", "alice", "bob")
    assert fake_chain.state["timers"][-1][0] == 7 * 86400 + 1
    later(7 * 86400 + 1)
    fire_timers()
    assert Proposal[p].status == "executed"


def test_deadline_timers_are_set_again_after_an_upgrade():
    world.build()
    for title in ("a", "b"):
        ok("proposal_create", "alice", title=title)
    fake_chain.state["timers"].clear()
    proposals.resume()
    assert len(fake_chain.state["timers"]) == 2
