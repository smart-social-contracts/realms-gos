"""A realm launched through the setup wizard bills a member, receives the payment and pays it out (realms-gos#739, #740),
on the real wasm and a real ledger: ic-python-token in test mode (scripts/token_ledger.py), which keeps every account's
balance. So each step checks where the money is on the ledger, not only what the books say.

Before #740 a new realm could not book a payment: the treasury's source fund, ROOT, did not exist. Before #739 it could
not spend one: the payment stayed in the invoice's own account, while a payout draws on the realm's main account. The tests
of this module are one flow, in order.
"""

from pathlib import Path

import pytest
import token_ledger
from ic.candid import Types, decode, encode
from ic.principal import Principal
from pic import CONTROLLER, Canister

UNIT = 10**8  # one DOM: the currency has 8 decimals
FEE = 10_000  # the ledger's fee on every transfer
BILL = 25 * UNIT
WAGE = 10 * UNIT

SUBACCOUNT = Types.Opt(Types.Vec(Types.Nat8))
ACCOUNT = Types.Record({"owner": Types.Principal, "subaccount": SUBACCOUNT})
INIT = Types.Record({"name": Types.Text, "symbol": Types.Text, "decimals": Types.Nat8, "total_supply": Types.Nat,
                     "fee": Types.Nat, "test": Types.Opt(Types.Bool)})
MINT = Types.Record({"to": ACCOUNT, "amount": Types.Nat})
TRANSFER = Types.Record({"from_subaccount": SUBACCOUNT, "to": ACCOUNT, "amount": Types.Nat, "fee": Types.Opt(Types.Nat),
                         "memo": Types.Opt(Types.Vec(Types.Nat8)), "created_at_time": Types.Opt(Types.Nat)})


def account(owner, subaccount=None):
    return {"owner": Principal.from_str(owner).bytes, "subaccount": [subaccount] if subaccount else []}


def ledger_call(ledger, method, typ, value, sender=CONTROLLER, query=False):
    ledger.pic.set_sender(Principal.from_str(sender))
    call = ledger.pic.query_call if query else ledger.pic.update_call
    return decode(bytes(call(ledger.id, method, encode([{"type": typ, "value": value}]))))


def balance(ledger, owner, subaccount=None):
    """What the account of ``owner`` (and ``subaccount``) holds on the ledger."""
    return ledger_call(ledger, "icrc1_balance_of", ACCOUNT, account(owner, subaccount), query=True)[0]["value"]


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def books(realm, people):
    """What the books say ROOT holds in DOM."""
    return ok(realm, "treasury_view", people["ctl"], query=True)["funds"]["ROOT"].get("DOM", 0)


@pytest.fixture(scope="module")
def ledger_wasm():
    path = Path(__file__).resolve().parents[2] / token_ledger.LEDGER_WASM
    token_ledger.fetch_ledger_wasm(path)  # downloads it once, and checks its sha256
    return path


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    return Canister(wasm_of("production"), installer=people["installer"])


@pytest.fixture(scope="module")
def ledger(realm, ledger_wasm):
    init = {"name": "Dominion", "symbol": "DOM", "decimals": 8, "total_supply": 0, "fee": FEE, "test": [True]}
    return Canister(ledger_wasm, pic=realm.pic, arg=encode([{"type": INIT, "value": init}]))


def test_the_wizard_launches_a_realm_whose_treasury_can_take_payments(realm, ledger, people):
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Dominion", open_registration=True)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8,
       token_canister_id=ledger.id.to_str())
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    for member in ("alice", "bob"):
        ok(realm, "realm_join", people[member])
    treasury = ok(realm, "treasury_view", people["ctl"], query=True)
    assert treasury["config"]["source_fund"] == "ROOT" and treasury["funds"] == {"ROOT": {}}
    assert [(t["id"], t["ledger"], t["fee"]) for t in treasury["tokens"]] == [("DOM", ledger.id.to_str(), FEE)]


def test_a_member_pays_an_invoice_and_the_money_reaches_the_account_the_realm_pays_from(realm, ledger, people):
    alice = people["alice"]
    billed = ok(realm, "invoice_create", people["ctl"], payer=alice, amount=BILL,
                items=[{"concept": "membership", "amount": BILL}])
    pay_to = bytes.fromhex(billed["pay_to"]["subaccount"])
    assert billed["pay_to"]["owner"] == realm.id.to_str()
    ledger_call(ledger, "mint", MINT, {"to": account(alice), "amount": BILL + FEE}, sender=alice)
    paid = ledger_call(ledger, "icrc1_transfer", TRANSFER, {
        "from_subaccount": [], "to": account(realm.id.to_str(), pay_to), "amount": BILL, "fee": [], "memo": [],
        "created_at_time": []}, sender=alice)
    assert balance(ledger, realm.id.to_str(), pay_to) == BILL, paid

    assert ok(realm, "invoice_check", alice, invoice=billed["invoice"])["status"] == "paid"
    # The realm moved the payment into its main account, which cost it the ledger's fee, and booked what it holds there.
    assert balance(ledger, realm.id.to_str(), pay_to) == 0
    assert balance(ledger, realm.id.to_str()) == BILL - FEE == books(realm, people)


def test_the_realm_pays_out_what_it_received(realm, ledger, people):
    bob, ctl, main = people["bob"], people["ctl"], realm.id.to_str()
    wage = ok(realm, "transfer_create", ctl, fund="ROOT", to=bob, amount=WAGE)
    assert wage["status"] == "completed", wage
    assert balance(ledger, bob) == WAGE
    assert balance(ledger, main) == BILL - FEE - WAGE - FEE == books(realm, people)

    rest = books(realm, people) - FEE  # all that is left, and the fee of paying it
    last = ok(realm, "transfer_create", ctl, fund="ROOT", to=bob, amount=rest)
    assert last["status"] == "completed", last
    assert balance(ledger, bob) == WAGE + rest
    assert balance(ledger, main) == 0 == books(realm, people)
