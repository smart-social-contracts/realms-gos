"""Every ratio is an integer in hundred-millionths (ggg#9, realms-gos#514): rows stored in whole percents or basis
points keep their meaning when they are read, and a caller still using the old names is refused.

A "version 1" row is how the last release saved it. Reading it again, after the loaded copy is forgotten, runs the
entity's migration, which must leave a row that is already converted alone: ``import_data`` saves records without a
version, so the migration runs on them too.
"""

import ggg_methods as g
import world
from api import proposals, taxes
from core.ratio import BASIS_POINT, ONE, PERCENT
from ggg import AllocationRule, Department, Proposal, Realm, TaxPolicy, Tender
from ic_python_db import Database
from kit import call, denied, ok
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
    rule = AllocationRule(id="AR-1", rules={"ROADS": 30, "WELFARE": 20})
    stored_as_version_1(AllocationRule, rule.id)
    assert AllocationRule["AR-1"].rules == {"ROADS": 30 * PERCENT, "WELFARE": 20 * PERCENT}
    AllocationRule(id="AR-2", rules={"ROADS": 30 * PERCENT, "WELFARE": 20 * PERCENT})
    stored_as_version_1(AllocationRule, "AR-2")
    assert AllocationRule["AR-2"].rules == {"ROADS": 30 * PERCENT, "WELFARE": 20 * PERCENT}
    AllocationRule(id="AR-3", rules={})
    stored_as_version_1(AllocationRule, "AR-3")
    assert AllocationRule["AR-3"].rules == {}


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
        {"code": "residence", "method": "fixed", "amount": 5}]}
    TaxPolicy(id="TAX-POLICY-1", document=old)
    stored_as_version_1(TaxPolicy, "TAX-POLICY-1")
    revenue, property_, residence = TaxPolicy["TAX-POLICY-1"].document["taxes"]
    assert revenue == {"code": "revenue", "method": "proportional", "rate": 200 * BASIS_POINT}
    assert [b["rate"] for b in property_["brackets"]] == [40 * BASIS_POINT, 80 * BASIS_POINT]
    assert residence == {"code": "residence", "method": "fixed", "amount": 5}
    stored_as_version_1(TaxPolicy, "TAX-POLICY-1")
    assert TaxPolicy["TAX-POLICY-1"].document["taxes"][0]["rate"] == 200 * BASIS_POINT  # a second read changes nothing


def test_a_rate_finer_than_a_basis_point():
    tax = {"method": "proportional", "rate": 125_000}  # 0.125 %, 12.5 basis points
    assert taxes.owed(tax, 1_000_000) == 1_250


def test_an_old_name_is_refused_with_the_new_one():
    world.build()
    assert "voting_threshold" in denied("governance_update", "ctl", threshold=60)
    assert "voting_quorum" in denied("governance_update", "ctl", quorum_percent=25)
    assert "quorum" in denied("department_set_policy", "ctl", department="Finance", m=1, n=1, quorum_percent=25)
    document = {"taxes": [{"code": "revenue", "base": "declared", "method": "proportional", "rate_bp": 200}]}
    assert "rate" in denied("tax_policy_draft", "ctl", document=document)
    brackets = [{"up_to": None, "rate_bp": 80}]
    document = {"taxes": [{"code": "property", "base": "flat", "method": "progressive", "brackets": brackets}]}
    assert "rate" in denied("tax_policy_draft", "ctl", document=document)
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
