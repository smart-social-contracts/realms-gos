"""Every number an operation takes lies in the range GGG gives it (facts.json), or the call is refused with
InvalidArgument before anything is written (realms-gos#547).

ggg_ranges is generated from facts.json (scripts/ggg_candid.py); these tests read the bounds the kinds there promise.
"""

import json
import pathlib

import pytest

import world
from core import ranges
from core.db import Fail
from core.ratio import ONE
from ggg import Fund, Invoice, Proposal, Realm
from ggg_ranges import RANGES
from kit import call, ok

FACTS = pathlib.Path(__file__).resolve().parents[2] / "ggg" / "facts.json"
LONG = 2**63 - 1
MONEY = 2**53 - 1


def test_the_bounds_are_those_the_fact_schema_gives_each_kind():
    kinds = json.loads(FACTS.read_text())["kinds"]
    assert "0 to 100 000 000" in kinds["ratio"]["range"] and "0 to 10 000 000 000" in kinds["ratio"]["range"]
    assert RANGES["governance_update"]["voting_threshold"] == (0, ONE)  # Ratio
    assert RANGES["loan_create"]["rate"] == (0, 100 * ONE)  # Rate
    assert RANGES["transfer_create"]["amount"] == (0, MONEY)  # Money
    assert RANGES["invoice_create"]["items"] == [{"amount": (-MONEY, MONEY), "quantity": (0, LONG)}]  # SignedMoney
    assert RANGES["land_assess"]["area"] == (0, LONG)  # Area
    assert RANGES["token_register"]["fee"] == (0, LONG)  # nat
    assert RANGES["loan_create"]["term"] == {"count": (0, 1_000_000)}  # Period
    assert RANGES["department_set_policy"] == {"m": (0, LONG), "n": (0, LONG), "quorum": (0, ONE)}  # its fields are flat


def test_a_time_is_bounded_in_seconds_because_the_operations_take_seconds():
    assert RANGES["proposal_create"]["effective_at"] == (0, LONG // 10**9)
    assert RANGES["governance_update"]["voting_window"] == (0, LONG // 10**9)


def test_a_number_inside_a_variant_is_left_to_the_operation():
    assert "setup_step" not in RANGES and "outcome" not in RANGES.get("dispatch_confirm", {})


def test_a_derived_fact_is_no_argument():
    assert RANGES["budget_propose"] == {"years": (0, LONG)}


@pytest.mark.parametrize("name, args", [
    ("governance_update", {"voting_threshold": ONE + 1}),
    ("governance_update", {"voting_quorum": -1}),
    ("proposal_create", {"title": "x", "threshold": 2 * ONE}),
    ("transfer_create", {"fund": "ROOT", "to": "bob", "amount": MONEY + 1}),
    ("department_set_policy", {"department": "Finance", "m": 1, "n": 1, "quorum": ONE + 1}),
    ("allocation_rule_draft", {"source_fund": "ROOT", "rules": [{"fund": "ROADS", "share": ONE + 1}]}),
    ("invoice_create", {"payer": "User:bob", "amount": 5, "items": [{"concept": "x", "amount": MONEY + 1}]}),
    ("invoice_create", {"payer": "User:bob", "amount": MONEY + 1}),
    ("token_register", {"token": "T", "decimals": 256}),
])
def test_a_number_out_of_range_is_refused_whatever_the_operation_and_nothing_is_written(name, args):
    world.build()
    funds, proposals = Fund.count(), Proposal.count()
    out = call(name, "ctl", **args)
    assert not out["ok"] and out["kind"] == "invalid_argument" and "must be between" in out["error"], out
    assert (Fund.count(), Proposal.count(), Invoice.count()) == (funds, proposals, 0)


def test_a_governed_call_out_of_range_files_no_proposal():
    world.build()
    world.policy(world.Department["Administration"], 2, 3)
    out = call("governance_update", "ctl", voting_threshold=ONE + 1)
    assert out["kind"] == "invalid_argument" and Proposal.count() == 0


def test_a_proposal_cannot_carry_an_action_out_of_range():
    world.build()
    out = call("proposal_create", "alice", title="x",
               action={"op": "governance_update", "args": {"voting_threshold": ONE + 1}})
    assert out["kind"] == "invalid_argument" and "voting_threshold" in out["error"] and Proposal.count() == 0


def test_the_bounds_themselves_are_in_range():
    world.build()
    ok("governance_update", "ctl", voting_threshold=ONE, voting_quorum=0)
    assert Realm["1"].manifest_data["governance"]["voting_threshold"] == ONE


@pytest.mark.parametrize("value", [1_000_001, -1, 10**30, "1000001", 1e9, float("nan")])
def test_a_periods_count_is_refused_beyond_a_million(value):
    with pytest.raises(Fail, match="term.count"):
        ranges.check("loan_create", {"term": {"count": value, "unit": "day"}, "amount": 1})


def test_a_periods_count_up_to_a_million_passes():
    ranges.check("loan_create", {"term": {"count": 1_000_000, "unit": "working_day"}, "amount": 1, "rate": 100 * ONE})


@pytest.mark.parametrize("args", [{"rate": 100 * ONE + 1}, {"amount": MONEY + 1}, {"term": {"count": 5, "unit": "day"},
                                                                                   "amount": "9007199254740992"}])
def test_money_and_rates_are_checked_where_they_are_nested_or_written_as_text(args):
    with pytest.raises(Fail):
        ranges.check("loan_create", args)


@pytest.mark.parametrize("args", [{"amount": "plenty"}, {"amount": None}, {"amount": True}, {"term": "a year"},
                                  {"term": {"count": "many"}}, {"rate": [1]}])
def test_what_is_no_number_is_left_to_the_operation_to_refuse(args):
    ranges.check("loan_create", args)


def test_a_list_kept_as_a_map_has_its_values_checked_as_the_elements():
    ranges.check("bids_reveal", {"openings": {"B-1": {"price": MONEY}}})
    with pytest.raises(Fail, match="openings"):
        ranges.check("bids_reveal", {"openings": {"B-1": {"price": MONEY + 1}}})


def test_an_operation_with_no_numbers_has_no_bounds():
    ranges.check("proposal_vote", {"proposal": "P-1", "choice": "yes"})
    ranges.check("not_an_operation", {"x": 10**30})
