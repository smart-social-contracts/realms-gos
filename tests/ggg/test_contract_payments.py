"""What has been paid on a contract (ggg I-FIN-11): the transfers that name it, the failed and cancelled excepted."""

import fake_chain
from api.procurement import seal
from ggg import Transfer
from kit import call, later, ok
from test_procurement import DAY, bid, tender
from test_treasury import LEDGER, allocate, ledger_ok, receive, setup_finance


def test_what_has_been_paid_on_a_contract_is_its_transfers_but_the_failed():
    setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    contract = ok("contract_create", "ctl", title="Bike lane", value=500, symbol="REALM")["contract"]
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, contract=contract)
    ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=50)  # names no contract
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = "(variant { Err = variant { TooOld } })"
    failed = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=70, contract=contract)
    assert failed["status"] == "failed"
    assert Transfer.paid_on(contract) == 100
    assert Transfer.paid_on("C-404") == 0


def test_a_payment_on_a_freshly_awarded_contract_is_accepted_up_to_its_value():
    setup_finance()
    allocate(ROADS=100)
    receive(100_000)
    t = tender()["tender"]
    ok("tender_open", "carla", tender=t)
    b = bid(t, "bob", "buildco", 48_000, "n1")
    later(31 * DAY)
    ok("bids_reveal", "carla", tender=t, openings={b: {"price": 48_000, "nonce": "n1"}})
    ok("bid_score", "carla", bid=b, scores={"price": 9, "quality": 8})
    contract = ok("tender_award", "carla", tender=t)["contract"]
    ledger_ok()
    first = call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=40_000, contract=contract)
    assert first["ok"], first
    over = call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=8_001, contract=contract)
    assert "has 8000 left of its value" in over["error"]
    assert call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=8_000, contract=contract)["ok"]
    assert Transfer.paid_on(contract) == 48_000
