"""A contract that is a call-off under a framework agreement another body holds (ggg I-PRC-6): what the realm checks, since
it holds the data, and the claim it keeps for what only the holder knows."""

import pytest

import world
from core import chain
from ggg import Contract
from kit import call, ok

OTHER_REALM = "aaaaa-aa"
PARTIES = ["Department:Infrastructure", "Organization:buildco"]


def framework(**overrides):
    return {"holder": {"Realm": OTHER_REALM}, "reference": "T-12", "winners": [], **overrides}


def call_off(**framework_overrides):
    return call("contract_create", "ctl", title="Electricity", parties=PARTIES, value=9_000,
                framework=framework(**framework_overrides))


def test_a_contract_is_a_call_off_under_another_realms_framework():
    world.build()
    made = ok("contract_create", "ctl", title="Electricity", parties=PARTIES, value=9_000,
              framework=framework(winners=["buildco", "roadco"]))["contract"]
    assert Contract[made].framework == framework(winners=["buildco", "roadco"])
    plain = ok("contract_create", "ctl", title="Not a call-off")["contract"]
    assert Contract[plain].framework is None
    assert call_off()["ok"]  # winners unknown: nothing to compare with


def test_an_external_holder_is_one_the_realm_knows():
    world.build()
    holder = {"External": "province"}
    assert "unknown External" in call_off(holder=holder)["error"]
    ok("external_add", "ctl", external="province", name="The province", kind="authority", channel="manual")
    made = ok("contract_create", "ctl", title="Gas", parties=PARTIES,
              framework=framework(holder=holder, reference="AM-2025/04"))["contract"]
    assert Contract[made].framework["holder"] == holder


@pytest.mark.parametrize("overrides, why", [
    ({"holder": {"Realm": chain.self_id()}}, "another realm"),
    ({"holder": {"Realm": "not a principal"}}, "another realm"),
    ({"reference": ""}, "1 to 128 code points"),
    ({"reference": "x" * 129}, "1 to 128 code points"),
    ({"winners": [f"w{i}" for i in range(33)]}, "at most 32 winners"),
    ({"winners": ["roadco"]}, "none of the contract's parties"),
])
def test_a_framework_out_of_bounds_is_refused_and_leaves_no_contract(overrides, why):
    world.build()
    assert why in call_off(**overrides)["error"]
    assert Contract.count() == 0
