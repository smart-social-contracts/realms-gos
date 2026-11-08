"""Every ratio is an integer in hundred-millionths (ggg#9, realms-gos#514): rows stored in whole percents or basis
points keep their meaning when they are read, and a caller still using the old names is refused.

A "version 1" row is how the last release saved it. Reading it again, after the loaded copy is forgotten, runs the
entity's migration. An old row is recognised by its content (an old key, a map where there is a list), so a row that
is already converted, or read a second time, passes unchanged.

The calls the last release left waiting (a proposal's action, a federal vote's, a task's steps) replay with the
arguments they were filed with: ``upgrade_args`` turns them into the current form.
"""

import json

import pytest

import ggg_methods as g
import world
from api import proposals, taxes
from core.ratio import BASIS_POINT, ONE, PERCENT, upgrade_args
from ggg import AllocationRule, Department, FederalVote, Proposal, Realm, TaxPolicy, Tender, TaskStep
from ic_python_db import Database
from kit import call, denied, direct, ok
from test_procurement import RUBRIC


def saved(entity, alias):
    """The row of ``entity`` named ``alias`` as it is in storage."""
    return Database.get_instance().load(entity.__name__, entity[alias]._id)


def stored_as_version_1(entity, alias, drop=(), **fields):
    """Save the row as the last release did, without the fields in ``drop`` and with ``fields`` replacing its own, and
    forget the loaded copy."""
    row = {k: v for k, v in saved(entity, alias).items() if k not in drop}
    Database.get_instance().save(entity.__name__, entity[alias]._id, {**row, "__version__": 1, **fields})
    Database.get_instance().clear_registry()


def test_a_proposal_keeps_its_threshold_and_quorum():
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    stored_as_version_1(Proposal, p, drop=("quorum",), threshold=60, quorum_percent=25)
    proposal = Proposal[p]
    assert (proposal.threshold, proposal.quorum) == (60 * PERCENT, 25 * PERCENT)
    assert "quorum_percent" not in saved(Proposal, p) and saved(Proposal, p)["__version__"] == 2
    Database.get_instance().clear_registry()
    assert (Proposal[p].threshold, Proposal[p].quorum) == (60 * PERCENT, 25 * PERCENT)  # a second read changes nothing


def test_an_old_proposal_is_decided_as_before():
    """Half the members must vote, and more than half of those voting must say yes."""
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    stored_as_version_1(Proposal, p, drop=("quorum",), threshold=50, quorum_percent=50)
    members = proposals.electorate_size(Proposal[p])
    assert members == 7
    tally = Proposal[p].tally
    for yes, no, decided in ((3, 0, "rejected"), (4, 0, "accepted"), (2, 2, "rejected"), (3, 1, "accepted")):
        Proposal[p].tally = {**tally, "yes": yes, "no": no}
        assert proposals.outcome(Proposal[p], True) == decided, (yes, no)


def test_a_department_policy_keeps_its_quorum():
    world.build()
    policy = {"m": 2, "n": 3, "quorum_percent": 50, "veto": ["dana"]}
    stored_as_version_1(Department, "Civil Registry", policy=policy, target_policy=policy)
    assert Department["Civil Registry"].policy == {"m": 2, "n": 3, "quorum": 50 * PERCENT, "veto": ["dana"]}
    assert Department["Civil Registry"].target_policy["quorum"] == 50 * PERCENT
    stored_as_version_1(Department, "Finance", policy={"m": 1, "n": 1, "quorum": 33_333_333, "veto": []})
    assert Department["Finance"].policy["quorum"] == 33_333_333  # converted already: not multiplied again
    assert Department["Infrastructure"].policy == {"m": 1, "n": 1, "quorum": 0, "veto": []}


def test_the_realm_keeps_its_voting_rules():
    world.build()
    manifest = {**Realm["1"].manifest_data, "governance": {"threshold": 60, "quorum_percent": 25}, "scaling": {"x": 1}}
    stored_as_version_1(Realm, "1", manifest_data=manifest)
    assert Realm["1"].manifest_data["governance"] == {"voting_threshold": 60 * PERCENT, "voting_quorum": 25 * PERCENT}
    assert Realm["1"].manifest_data["scaling"] == {"x": 1}
    assert proposals.settings() == {"voting_threshold": 60 * PERCENT, "voting_quorum": 25 * PERCENT}
    manifest = {"governance": {"threshold": 70}}  # only one rule was ever set
    stored_as_version_1(Realm, "1", manifest_data=manifest)
    assert Realm["1"].manifest_data["governance"] == {"voting_threshold": 70 * PERCENT}
    stored_as_version_1(Realm, "1", manifest_data={"governance": {"voting_threshold": 70 * PERCENT}})
    assert Realm["1"].manifest_data["governance"] == {"voting_threshold": 70 * PERCENT}


def test_new_proposals_take_the_migrated_rules():
    world.build()
    stored_as_version_1(Realm, "1", manifest_data={"governance": {"threshold": 60, "quorum_percent": 25}})
    p = ok("proposal_create", "alice", title="x")["proposal"]
    assert (Proposal[p].threshold, Proposal[p].quorum) == (60 * PERCENT, 25 * PERCENT)


def test_an_allocation_rule_keeps_its_shares():
    world.build()
    shares = [{"fund": "ROADS", "share": 30 * PERCENT}, {"fund": "WELFARE", "share": 20 * PERCENT}]
    AllocationRule(id="AR-1", rules={"ROADS": 30, "WELFARE": 20})
    stored_as_version_1(AllocationRule, "AR-1")
    assert AllocationRule["AR-1"].rules == shares
    AllocationRule(id="AR-2", rules=shares)
    stored_as_version_1(AllocationRule, "AR-2")
    assert AllocationRule["AR-2"].rules == shares  # already a list: unchanged
    AllocationRule(id="AR-3", rules={})
    stored_as_version_1(AllocationRule, "AR-3")
    assert AllocationRule["AR-3"].rules == []


def test_a_tender_keeps_its_weights():
    world.build()
    Tender(id="T-1", rubric={"price": 60, "quality": 40})
    stored_as_version_1(Tender, "T-1")
    assert Tender["T-1"].rubric == RUBRIC
    stored_as_version_1(Tender, "T-1", rubric=RUBRIC)
    assert Tender["T-1"].rubric == RUBRIC  # converted already: not multiplied again


def test_a_tax_policy_keeps_its_rates():
    world.build()
    old = {"symbol": "REALM", "taxes": [
        {"code": "revenue", "method": "proportional", "rate_bp": 200},
        {"code": "property", "method": "progressive", "brackets": [{"up_to": 5, "rate_bp": 40}, {"up_to": None, "rate_bp": 80}]},
        {"code": "residence", "method": "fixed", "amount": 5, "brackets": None}]}
    TaxPolicy(id="TAX-POLICY-1", document=old)
    stored_as_version_1(TaxPolicy, "TAX-POLICY-1")
    revenue, property_, residence = TaxPolicy["TAX-POLICY-1"].taxes
    assert revenue["rate"] == 200 * BASIS_POINT
    assert [b["rate"] for b in property_["facts"]["brackets"]] == [40 * BASIS_POINT, 80 * BASIS_POINT]
    assert residence["amount"] == 5
    stored_as_version_1(TaxPolicy, "TAX-POLICY-1")
    assert TaxPolicy["TAX-POLICY-1"].taxes[0]["rate"] == 200 * BASIS_POINT  # a second read changes nothing


def test_a_rate_finer_than_a_basis_point():
    tax = {"code": "r", "kind": "tax", "subject": "User", "rate": 125_000, "facts": {}}  # 0.125 %, 12.5 basis points
    assert taxes.owed(tax, 1_000_000, 2) == 1_250


def test_an_old_name_is_refused_with_the_new_one():
    world.build()
    assert "voting_threshold" in denied("governance_update", "ctl", threshold=60)
    assert "voting_quorum" in denied("governance_update", "ctl", quorum_percent=25)
    assert "quorum" in denied("department_set_policy", "ctl", department="Finance", m=1, n=1, quorum_percent=25)
    document = {"taxes": [{"code": "revenue", "base": "declared", "method": "proportional", "rate_bp": 200}]}
    assert "taxes" in denied("tax_policy_draft", "ctl", document=document)
    brackets = [{"up_to": None, "rate_bp": 80}]
    entry = {"code": "property", "kind": "tax", "subject": "User", "facts": {"brackets": brackets}}
    assert "rate" in denied("tax_policy_draft", "ctl", document={}, taxes=[entry])
    assert Realm["1"].manifest_data.get("governance") is None and TaxPolicy.count() == 0


def test_a_ratio_is_at_most_a_hundred_percent():
    world.build()
    for args in ({"voting_threshold": ONE + 1}, {"voting_quorum": -1}):
        assert not call("governance_update", "ctl", **args)["ok"]
    assert not call("department_set_policy", "ctl", department="Finance", m=1, n=1, quorum=ONE + 1)["ok"]
    ok("governance_update", "ctl", voting_threshold=ONE, voting_quorum=0)
    ok("department_set_policy", "ctl", department="Finance", m=1, n=2, quorum=ONE)


def test_ggg_shows_the_stored_ratios_unconverted():
    world.build()
    ok("department_set_policy", "ctl", department="Finance", m=1, n=2, quorum=12_345_678)
    ok("governance_update", "ctl", voting_threshold=66_666_667, voting_quorum=1)
    assert g.ggg_department_get({"id": "Finance"})["Ok"]["department"]["policy"]["quorum"] == 12_345_678
    assert g.ggg_realm_info()["voting_threshold"] == 66_666_667 and g.ggg_realm_info()["voting_quorum"] == 1


OLD_CALLS = [
    ("governance_update", {"threshold": 60, "quorum_percent": 25, "voting_window": 0},
     {"voting_threshold": 60 * PERCENT, "voting_quorum": 25 * PERCENT, "voting_window": 0}),
    ("department_set_policy", {"department": "Finance", "m": 2, "n": 3, "quorum_percent": 50},
     {"department": "Finance", "m": 2, "n": 3, "quorum": 50 * PERCENT}),
    ("tax_policy_draft", {"document": {"taxes": [{"code": "r", "method": "proportional", "rate_bp": 200}]}},
     {"document": {}, "taxes": [{"code": "r", "kind": "tax", "subject": "User", "rate": 200 * BASIS_POINT, "facts": {}}]}),
    ("allocation_rule_draft", {"source_fund": "ROOT", "rules": {"ROADS": 30}},
     {"source_fund": "ROOT", "rules": [{"fund": "ROADS", "share": 30 * PERCENT}]}),
    ("tender_create", {"rubric": {"price": 60, "quality": 40}}, {"rubric": RUBRIC}),
    ("quarter_configure", {"rule": {"kind": "majority_of_members", "threshold": 60}},
     {"rule": {"kind": "majority_of_members", "threshold": 60 * PERCENT}}),
]


@pytest.mark.parametrize("name, old, current", OLD_CALLS)
def test_the_arguments_of_a_call_left_waiting_are_upgraded_and_only_once(name, old, current):
    assert upgrade_args(name, old) == current
    assert upgrade_args(name, current) == current


def test_a_call_inside_a_call_is_upgraded_too():
    inner = {"op": "governance_update", "args": {"threshold": 60}}
    new = {"op": "governance_update", "args": {"voting_threshold": 60 * PERCENT}}
    assert upgrade_args("federal_vote_propose", {"title": "x", "action": inner}) == {"title": "x", "action": new}
    assert upgrade_args("task_create", {"name": "t", "steps": [inner, {"op": "status"}]})["steps"] == [new, {"op": "status"}]


def pending_allocation(old_action):
    """A proposal filed by the last release: voting, and carrying ``old_action``."""
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    world.policy(Department["Administration"], 2, 3)
    p = ok("proposal_create", "bob", title="Share the roads", action={"op": "allocation_rule_draft", "args": {
        "source_fund": "ROOT", "rules": [{"fund": "ROADS", "share": ONE}]}})["proposal"]
    stored_as_version_1(Proposal, p, drop=("quorum",), threshold=50, quorum_percent=0, action=old_action)
    return p


def test_a_proposal_filed_before_the_upgrade_runs_its_action_with_the_shares_it_was_filed_with():
    old = {"op": "allocation_rule_draft", "args": {"source_fund": "ROOT", "rules": {"ROADS": 30}}}
    p = pending_allocation(old)
    assert Proposal[p].action["args"]["rules"] == [{"fund": "ROADS", "share": 30 * PERCENT}]
    for voter in world.PEOPLE:
        if Proposal[p].status != "voting":
            break
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Proposal[p].status == "executed", Proposal[p].result
    assert AllocationRule["AR-1"].rules == [{"fund": "ROADS", "share": 30 * PERCENT}]


def test_a_federal_vote_keeps_its_hash_and_replays_with_the_upgraded_arguments():
    action = {"op": "governance_update", "args": {"voting_threshold": 60 * PERCENT}}
    world.build()
    FederalVote(id="FV-1", action=action, rule={"kind": "majority_of_quarters", "threshold": 50}, hash="sha256:published")
    stored_as_version_1(FederalVote, "FV-1", action={"op": "governance_update", "args": {"threshold": 60}})
    vote = FederalVote["FV-1"]
    assert vote.action == action and vote.rule == {"kind": "majority_of_quarters", "threshold": 50 * PERCENT}
    assert vote.hash == "sha256:published"  # the quarters answer with the hash they were sent


def test_the_realms_federal_rule_keeps_its_threshold():
    world.build()
    stored_as_version_1(Realm, "1", manifest_data={"federation": {"rule": {"kind": "majority_of_quarters", "threshold": 60,
                                                                           "absent": "wait"}}})
    assert Realm["1"].manifest_data["federation"]["rule"]["threshold"] == 60 * PERCENT


def test_a_task_step_left_by_the_last_release_runs_with_the_ratios_it_was_given():
    world.build()
    world.profile("task-runner", "task.create", "task.run", "task.view", "governance.update")
    world.person("carla", "task-runner")
    ok("task_create", "carla", name="rules", steps=[{"op": "governance_update", "args": {"voting_quorum": 0}}])
    step = TaskStep.load_some(1, 1)[0]
    step.action = json.dumps({"op": "governance_update", "args": {"quorum_percent": 25}})
    assert ok("task_run", "carla", task="rules")["status"] == "completed"
    assert Realm["1"].manifest_data["governance"] == {"voting_quorum": 25 * PERCENT}


def test_a_governed_call_with_an_old_name_is_refused_and_files_nothing():
    world.build()
    world.policy(Department["Finance"], 2, 3)
    world.policy(Department["Administration"], 2, 3)
    calls = [("department_set_policy", {"department": "Finance", "m": 1, "n": 1, "quorum_percent": 50}),
             ("governance_update", {"threshold": 60}),
             ("tax_policy_draft", {"document": {"taxes": [{"code": "r", "method": "proportional", "rate_bp": 200}]}}),
             ("allocation_rule_draft", {"source_fund": "ROOT", "rules": {"ROADS": 30}})]
    for name, args in calls:
        out = direct(name, "ctl", **args)  # an old name is not in the Candid types: only the operation can see it
        assert not out["ok"] and out["kind"] == "invalid_argument", (name, out)
    assert Proposal.count() == 0


def test_the_shares_of_a_rule_are_a_list_of_funds_and_shares():
    world.build()
    ok("fund_create", "ctl", fund="WELFARE")
    assert "list of {fund, share}" in direct("allocation_rule_draft", "ctl", source_fund="ROOT", rules={"ROADS": 30})["error"]
    share = lambda fund, amount: {"fund": fund, "share": amount}  # noqa: E731
    for rules in ([share("ROADS", 30), share("ROADS", 20)], [share("ROADS", -1)], [share("ROADS", ONE), share("WELFARE", 1)]):
        assert not call("allocation_rule_draft", "ctl", source_fund="ROOT", rules=rules)["ok"], rules
    for rules in ([share("ROADS", 0.5)], [share("ROADS", True)], [{"fund": "ROADS"}], ["ROADS"]):  # not Candid either
        assert not direct("allocation_rule_draft", "ctl", source_fund="ROOT", rules=rules)["ok"], rules
    assert AllocationRule.count() == 0
    ok("allocation_rule_draft", "ctl", source_fund="ROOT", rules=[share("ROADS", 30 * PERCENT), share("WELFARE", 70 * PERCENT)])


def test_the_federal_rule_is_on_the_new_scale():
    world.build()
    out = ok("quarter_configure", "ctl", rule={"threshold": 60 * PERCENT})
    assert out["rule"]["threshold"] == 60 * PERCENT
    assert not call("quarter_configure", "ctl", rule={"threshold": ONE})["ok"]


def test_an_old_federal_threshold_is_refused_even_when_the_call_is_governed():
    world.build()
    world.policy(Department["Administration"], 2, 3)
    for threshold in (1, 60, 99):
        out = direct("quarter_configure", "ctl", rule={"threshold": threshold})
        assert not out["ok"] and out["kind"] == "invalid_argument" and "hundred-millionths" in out["error"], out
    assert Proposal.count() == 0
    assert "proposal" in direct("quarter_configure", "ctl", rule={"threshold": 60 * PERCENT})  # filed: it is a vote
    assert "proposal" in direct("quarter_configure", "ctl", rule={"threshold": 0})
