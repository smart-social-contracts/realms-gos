"""A quarter sweeps what is paid in it into its capital's funds (ggg D36, I-FED-5 to I-FED-7), on the real wasm: the capital
sends its rules with quarter_sync, the quarter pushes at the end of its treasury epoch and when asked, a ledger that does not
answer is asked again with the same transfer, and the money is booked once on each side.

The mock ledger answers every transfer the same way, so a ledger is made for each answer a test needs and the quarter's
token is pointed at it; one that is stopped stands for a ledger that does not answer. Each test starts a treasury epoch of
its own: the end-of-epoch timer of the quarter does the sweeping in it.
"""

import mock_ledger
import pytest
from ic.principal import Principal
from network import Network
from pic import _CANISTER, _management, CONTROLLER

FUNDS = 1_000  # what the mock ledger reports every invoice paid with
KEEP, FEE = 100, 10
FIRST = FUNDS - KEEP - FEE  # what a quarter holding FUNDS sweeps
AGAIN = FUNDS - FEE  # what it sweeps after that: it keeps KEEP already, and FUNDS are paid in
EPOCH = 30 * 86_400  # a "monthly" treasury epoch
HOUR = 3_600
RULE = {"fund": "ROOT", "symbol": "DOM", "into": "GENERAL", "keep": KEEP}


class Ledger:
    """A mock ledger on the instance of the network, which the test can stop: a ledger that does not answer."""

    def __init__(self, pic, transfer_reply=None):
        pic.set_sender(Principal.from_str(CONTROLLER))
        self.pic, self.id = pic, mock_ledger.install(pic, FUNDS, transfer_reply)

    def stop(self):
        self._lifecycle("stop_canister")

    def start(self):
        self._lifecycle("start_canister")

    def _lifecycle(self, method):
        canister = Principal.from_str(self.id)
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        _management(self.pic, canister, method, _CANISTER, {"canister_id": canister.bytes})


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def ledgers(net):
    return {"ok": Ledger(net.pic), "duplicate": Ledger(net.pic, mock_ledger.candid_err_duplicate(77)),
            "too_old": Ledger(net.pic, mock_ledger.candid_err_too_old())}


def with_a_fund(realm, ledger, fund):
    realm.ok("token_register", "ctl", symbol="DOM", ledger=ledger.id, decimals=8, fee=FEE)
    realm.ok("fund_create", "ctl", fund=fund)
    return realm


@pytest.fixture(scope="module")
def capital(net, ledgers):
    return with_a_fund(net.realm("capital"), ledgers["ok"], "GENERAL")


@pytest.fixture(scope="module")
def north(net, ledgers, capital):
    quarter = with_a_fund(net.realm("north", members=("alice",)), ledgers["ok"], "ROOT")
    net.register_quarters(capital, quarter)
    capital.govern("treasury_configure", {"config": {"sweep": [RULE]}})
    quarter.ok("quarter_sync", "ctl")  # the rules arrive, and the timer is armed for the end of the epoch
    return quarter


def fund(realm, people):
    """Pay FUNDS into the quarter's ROOT fund: an invoice the mock ledger says is paid."""
    items = [{"concept": "funds", "amount": FUNDS}]
    invoice = realm.ok("invoice_create", "ctl", payer=people["alice"], amount=FUNDS, items=items)["invoice"]
    assert realm.ok("invoice_check", "alice", invoice=invoice)["status"] == "paid"


def held(realm, fund_id):
    return realm.ok("treasury_view", "ctl", query=True)["funds"][fund_id].get("DOM", 0)


def sweeps(realm, **filters):
    return {s["id"]: s for s in realm.ok("sweep_list", "ctl", query=True, **filters)["sweeps"]}


def into_the_next_epoch(net, realm):
    """Let the clock reach the start of the next treasury epoch, where the timer of the quarter sweeps, and the calls it
    makes finish."""
    net.wait(EPOCH - realm.now() % EPOCH + 1)
    net.wait(1)


def test_two_calls_at_once_make_one_sweep_that_the_quarter_and_the_capital_book_once(net, capital, north, people):
    fund(north, people)
    first, second = (north.submit("quarter_sweep", {}, sender=people["ctl"]) for _ in range(2))
    for reply in north.finish(first, second):
        assert reply["ok"], reply

    (sweep,) = sweeps(north).values()
    assert (sweep["id"], sweep["status"], sweep["block"], sweep["amount"], sweep["fee"]) == ("SW-1", "booked", "1", FIRST, FEE)
    assert held(north, "ROOT") == KEEP  # the amount and the fee left once
    booked = sweeps(capital, quarter=north.cid)
    assert list(booked) == [f"{north.cid}/SW-1"] and booked[f"{north.cid}/SW-1"]["status"] == "booked"
    assert held(capital, "GENERAL") == FIRST  # booked once


def test_the_quarter_sweeps_when_its_treasury_epoch_ends(net, capital, north, people):
    fund(north, people)
    into_the_next_epoch(net, north)

    assert sweeps(north)["SW-2"]["status"] == "booked" and held(north, "ROOT") == KEEP
    assert held(capital, "GENERAL") == FIRST + AGAIN


def test_a_ledger_that_does_not_answer_is_asked_again_with_the_same_transfer_and_a_duplicate_is_its_block(
        net, capital, north, ledgers, people):
    fund(north, people)
    ledgers["ok"].stop()
    into_the_next_epoch(net, north)
    assert sweeps(north)["SW-3"]["status"] == "sending" and held(north, "ROOT") == KEEP + FUNDS  # nothing moved

    ledgers["ok"].start()
    north.ok("token_register", "ctl", symbol="DOM", ledger=ledgers["duplicate"].id)  # the ledger that applied it already
    net.wait(HOUR)
    north.ok("quarter_sweep", "ctl")

    sweep = sweeps(north)["SW-3"]
    assert (sweep["status"], sweep["block"]) == ("booked", "77")  # the block the ledger gave in answering Duplicate
    assert held(north, "ROOT") == KEEP and held(capital, "GENERAL") == FIRST + 2 * AGAIN
    assert len(sweeps(capital, quarter=north.cid)) == 3


def test_a_sweep_still_unknown_after_23_hours_needs_a_check_and_a_person_settles_it(net, capital, north, ledgers, people):
    north.ok("token_register", "ctl", symbol="DOM", ledger=ledgers["ok"].id)
    fund(north, people)
    ledgers["ok"].stop()
    into_the_next_epoch(net, north)
    assert sweeps(north)["SW-4"]["status"] == "sending"

    net.wait(23 * HOUR)
    north.ok("quarter_sweep", "ctl")
    assert sweeps(north)["SW-4"]["status"] == "needs_check"
    ledgers["ok"].start()
    north.ok("quarter_sweep", "ctl")  # it is never sent again
    assert sweeps(north)["SW-4"]["status"] == "needs_check" and held(capital, "GENERAL") == FIRST + 2 * AGAIN

    north.govern("sweep_resolve", {"sweep": "SW-4", "block": 9})
    assert sweeps(north)["SW-4"]["status"] == "sent" and held(north, "ROOT") == KEEP
    north.ok("quarter_sweep", "ctl")  # reported at the next sweep
    assert (sweeps(north)["SW-4"]["status"], sweeps(north)["SW-4"]["block"]) == ("booked", "9")
    assert held(capital, "GENERAL") == FIRST + 3 * AGAIN


def test_a_transfer_older_than_the_ledgers_window_needs_a_check_at_once_and_resolves_as_failed(net, capital, north, ledgers, people):
    north.ok("token_register", "ctl", symbol="DOM", ledger=ledgers["too_old"].id)
    fund(north, people)
    into_the_next_epoch(net, north)
    assert sweeps(north)["SW-5"]["status"] == "needs_check"

    north.govern("sweep_resolve", {"sweep": "SW-5"})  # the ledger holds no such transfer
    assert sweeps(north)["SW-5"]["status"] == "failed"
    assert held(north, "ROOT") == KEEP + FUNDS and held(capital, "GENERAL") == FIRST + 3 * AGAIN
