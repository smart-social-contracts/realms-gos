"""Contract amendments and the spread of a contract's value over fiscal periods (ggg I-GOV-16, I-FIN-11): what the terms
may become, what each amendment records, who governs it, and that it is checked again when a proposal carries it out."""

import pytest

import world
from api.proposals import contract_owner
from core.db import Fail
from ggg import Contract, ContractAmendment, Department, Mandate, Proposal
from kit import call, denied, ok
from test_treasury import adopt, allocate, ledger_ok, receive, setup_finance

DAY = 86400
JAN_2026 = 1_767_225_600


def spread(*pairs):
    return [{"period": period, "amount": amount} for period, amount in pairs]


def bike_lane(**extra):
    """50,000 REALM, 30,000 of it in FY2026 and 20,000 in FY2027, for 90 days."""
    args = {"title": "Bike lane works", "parties": ["Organization:buildco"], "value": 50_000, "symbol": "REALM",
            "starts_at": JAN_2026, "ends_at": JAN_2026 + 90 * DAY, "split": spread(("FY2026", 30_000), ("FY2027", 20_000)),
            **extra}
    return ok("contract_create", "ctl", **args)["contract"]


def amend(contract, caller="ctl", **terms):
    return call("contract_amend", caller, contract=contract, **terms)


def test_a_contract_is_made_with_its_value_spread_over_periods():
    world.build()
    c = bike_lane()
    assert Contract[c].split == spread(("FY2026", 30_000), ("FY2027", 20_000))
    assert Contract[ok("contract_create", "ctl", title="Not spread", value=10)["contract"]].split == []


@pytest.mark.parametrize("split, why", [
    (spread(("FY2026", 30_000)), "add up"),
    (spread(("FY2026", 25_000), ("FY2026", 25_000)), "each named once"),
    ([{"period": f"P{i}", "amount": 50_000 if i == 0 else 0} for i in range(65)], "at most 64"),
    (spread(("FY2026", 60_000), ("FY2027", -10_000)), "must be between 0"),
    (["FY2026"], "list of {period, amount}"),
])
def test_a_split_that_breaks_the_rules_is_refused(split, why):
    world.build()
    assert why in call("contract_create", "ctl", title="x", value=50_000, split=split)["error"]
    assert "does not start after it ends" in call("contract_create", "ctl", title="x", starts_at=JAN_2026,
                                                  ends_at=JAN_2026 - DAY)["error"]
    assert Contract.count() == 0


def test_an_amendment_re_spreads_a_contract_and_records_both_sides():
    world.build()
    c = bike_lane()
    out = ok("contract_amend", "ctl", contract=c, split=spread(("FY2026", 10_000), ("FY2027", 40_000)),
             ends_at=JAN_2026 + 180 * DAY)
    assert out["contract"] == c
    k = Contract[c]
    assert (k.value, k.title, k.parties, k.status) == (50_000, "Bike lane works", ["Organization:buildco"], "active")
    assert k.split == spread(("FY2026", 10_000), ("FY2027", 40_000)) and k.ends_at == JAN_2026 + 180 * DAY
    ok("contract_amend", "ctl", contract=c, value=40_000, split=spread(("FY2026", 40_000)))
    ok("contract_amend", "ctl", contract=c, split=[])  # an empty split clears it
    first, second, third = ContractAmendment[f"{c}.1"], ContractAmendment[f"{c}.2"], ContractAmendment[f"{c}.3"]
    assert first.before["split"] == spread(("FY2026", 30_000), ("FY2027", 20_000))
    assert first.after["ends_at"] == JAN_2026 + 180 * DAY and first.proposal == "" and first.at > 0
    assert (second.before["value"], second.after["value"]) == (50_000, 40_000) and third.after["split"] == []
    shown = ok("contract_get", "bob", contract=c)
    assert shown["contract"]["value"] == 40_000
    assert [a["id"] for a in shown["amendments"]] == [first.id, second.id, third.id] and shown["next"] is None
    paged = ok("contract_get", "bob", contract=c, count=2)
    assert [a["id"] for a in paged["amendments"]] == [first.id, second.id] and paged["next"] == 3
    assert [a["id"] for a in ok("contract_get", "bob", contract=c, count=2, **{"from": 3})["amendments"]] == [third.id]
    assert "unknown Contract" in call("contract_get", "bob", contract="C-404")["error"]


def test_an_amendment_that_breaks_the_rules_or_changes_nothing_is_refused():
    world.build()
    c = bike_lane()
    assert "no term" in amend(c)["error"] and "no term" in amend(c, value=50_000)["error"]
    assert "add up" in amend(c, value=40_000)["error"]  # a spread contract that changes its value needs a new split
    assert "does not start after it ends" in amend(c, ends_at=JAN_2026 - DAY)["error"]
    assert "whole number" in amend(c, value="lots")["error"]
    assert "unknown Contract" in amend("C-404", value=1)["error"]
    assert ContractAmendment.count() == 0 and Contract[c].value == 50_000


def test_a_contract_that_has_ended_is_not_amended():
    world.build()
    c = bike_lane()
    ok("contract_terminate", "ctl", contract=c)
    assert "is terminated" in amend(c, value=40_000, split=[])["error"]
    Contract[c].status = "fulfilled"
    assert "is fulfilled" in amend(c, value=40_000, split=[])["error"]


def test_a_contract_under_a_mandate_stays_within_its_limits_and_is_governed_by_its_executor():
    depts = world.build()
    m = ok("mandate_create", "ctl", title="Road works", scope={"max_value": 60_000}, executor="Infrastructure",
           expires_at=JAN_2026 + 100 * DAY)["mandate"]
    c = Contract(id="C-9", title="Lane", mandate=Mandate[m], value=50_000, status="active", ends_at=JAN_2026 + 90 * DAY).id
    assert contract_owner({"contract": c}) is depts["Infrastructure"]
    assert "exceeds mandate" in amend(c, value=70_000)["error"]
    assert "outlasts mandate" in amend(c, ends_at=JAN_2026 + 101 * DAY)["error"]
    ok("contract_amend", "ctl", contract=c, value=55_000)
    assert Contract[c].value == 55_000


def test_a_contract_from_a_tender_is_governed_by_its_requester_and_any_other_by_the_root():
    depts = world.build()
    from ggg import Tender
    Tender(id="T-5", title="Bike lane", requester=depts["Infrastructure"])
    assert contract_owner({"contract": bike_lane(tender="T-5")}) is depts["Infrastructure"]
    assert contract_owner({"contract": bike_lane()}) is Department["Administration"]


def test_an_amendment_a_proposal_carries_names_it_and_is_checked_again_when_it_runs():
    depts = world.build()
    world.grant(depts["Infrastructure"], "contract.amend")
    world.policy(depts["Infrastructure"], 1, 2)
    m = ok("mandate_create", "ctl", title="Road works", executor="Infrastructure")["mandate"]
    c = Contract(id="C-9", title="Lane", mandate=Mandate[m], value=50_000, status="active").id
    again = Contract(id="C-10", title="Lane 2", mandate=Mandate[m], value=50_000, status="active").id
    filed = ok("contract_amend", "carla", contract=c, value=40_000)
    assert filed["requires_vote"] and Contract[c].value == 50_000
    adopt(filed["proposal"])
    assert Contract[c].value == 40_000 and ContractAmendment[f"{c}.1"].proposal == filed["proposal"]
    # filed while the contract could be amended, closed before the vote ends: nothing is amended
    late = ok("contract_amend", "carla", contract=again, value=30_000)["proposal"]
    ok("contract_terminate", "ctl", contract=again)
    assert ok("proposal_vote", "carla", proposal=late, choice="yes")["status"] == "failed"
    assert Contract[again].value == 50_000 and ContractAmendment.count() == 1


def test_a_value_below_what_has_been_paid_is_refused_when_filed_and_when_it_runs():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "contract.amend")
    c = ok("contract_create", "ctl", title="Lane", value=500, symbol="REALM")["contract"]
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=300, contract=c)
    assert "below the 300 paid" in amend(c, value=299)["error"]
    ok("contract_amend", "ctl", contract=c, value=300)  # equal to what has been paid: allowed
    assert Contract[c].value == 300
