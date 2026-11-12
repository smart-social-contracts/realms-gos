"""What a contract page reads of the realm: the contracts a page at a time, and for one, what an amendment of it is held to
(ggg I-GOV-16, I-FIN-11)."""

import fake_chain
import world
from ggg import Tender
from kit import call, ok
from test_contract_amend import JAN_2026, bike_lane, spread
from test_framework_contracts import PARTIES, framework
from test_spending import ICRC1, TOO_OLD, fund_roads_with_budget, pay
from test_treasury import ledger_ok


def ids(reply):
    return [k["id"] for k in reply["contracts"]]


def test_the_contracts_are_listed_a_page_at_a_time_and_by_status():
    world.build()
    first = bike_lane()
    second = ok("contract_create", "ctl", title="Electricity", parties=PARTIES, value=9_000,
                framework=framework(winners=["buildco"]))["contract"]
    third = bike_lane(title="Bike racks")
    ok("contract_terminate", "ctl", contract=second)

    everything = ok("contract_list", "bob")
    assert ids(everything) == [first, second, third] and everything["next"] is None
    lane = everything["contracts"][0]
    assert (lane["title"], lane["value"], lane["symbol"], lane["status"], lane["lot"]) == ("Bike lane works", 50_000, "REALM", "active", "")
    assert lane["split"] == spread(("FY2026", 30_000), ("FY2027", 20_000)) and lane["framework"] is None
    assert lane["starts_at"] == JAN_2026 and lane["parties"] == ["Organization:buildco"]
    assert everything["contracts"][1]["framework"] == framework(winners=["buildco"])

    assert ids(ok("contract_list", "bob", status="terminated")) == [second]
    assert ids(ok("contract_list", "bob", status="active")) == [first, third]
    page = ok("contract_list", "bob", count=2)
    assert ids(page) == [first, second] and page["next"] == 3
    assert ids(ok("contract_list", "bob", count=2, **{"from": page["next"]})) == [third]


def test_a_contract_read_names_the_department_that_governs_its_amendment_and_whether_that_takes_a_vote():
    depts = world.build()
    Tender(id="T-5", title="Bike lane", requester=depts["Infrastructure"])
    from_tender, other = bike_lane(tender="T-5"), bike_lane()
    shown = ok("contract_get", "bob", contract=from_tender)
    assert (shown["governed_by"], shown["needs_vote"]) == ("Infrastructure", False)
    assert ok("contract_get", "bob", contract=other)["governed_by"] == "Administration"

    world.policy(depts["Infrastructure"], 1, 2)
    assert ok("contract_get", "bob", contract=from_tender)["needs_vote"] is True
    assert ok("contract_get", "bob", contract=other)["needs_vote"] is False
    assert "unknown Contract" in call("contract_get", "bob", contract="C-404")["error"]


def test_a_contract_read_says_what_has_been_paid_on_it_and_the_budget_lines_that_paid():
    world.build()
    line = fund_roads_with_budget(1_000)
    ledger_ok()
    contract = ok("contract_create", "ctl", title="Lane", value=500, symbol="REALM")["contract"]
    unpaid = ok("contract_get", "bob", contract=contract)
    assert (unpaid["paid"], unpaid["budgets"]) == (0, [])

    assert pay(100, contract=contract)["ok"] and pay(60, contract=contract)["ok"]
    assert pay(40)["ok"]  # names no contract
    fake_chain.state["replies"][ICRC1] = TOO_OLD
    assert pay(70, contract=contract)["status"] == "failed"
    paid = ok("contract_get", "bob", contract=contract)
    assert paid["paid"] == 160
    assert paid["budgets"] == [{"id": line, "fund": "ROADS", "period": "FY2026", "category": "capital"}]  # once, and not for the failed one
