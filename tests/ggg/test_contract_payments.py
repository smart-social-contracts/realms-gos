"""What has been paid on a contract (ggg I-FIN-11): the transfers that name it, the failed and cancelled excepted."""

import fake_chain
from ggg import Transfer
from kit import ok
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
