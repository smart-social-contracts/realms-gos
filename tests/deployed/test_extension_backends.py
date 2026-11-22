"""An extension's backend on the real wasm: ``extension_read`` as a query, and ``extension_call`` waiting for an
operation that calls another canister (``invoice_check`` asks the ledger), which only a message that awaits can make.
Measures what an empty call costs, now that a backend which does not import ``ggg_sdk`` runs without it."""

import json

import mock_ledger
import pytest
from pic import Canister

AMOUNT = 5_000_000
BACKEND = '''
def idle():
    return None


def mine():
    return rpc("my_invoices")


def rename(nickname):
    return rpc("profile_update", nickname=nickname)


def check(invoice):
    reply = yield wait("invoice_check", invoice=invoice)
    return {"status": reply.get("status"), "then": rpc("my_invoices")["invoices"][0]["status"]}


def check_at_once(invoice):
    return rpc("invoice_check", invoice=invoice)


def check_and_fail(invoice):
    yield wait("invoice_check", invoice=invoice)
    raise ValueError("after the wait")
'''
WITH_SDK = "import ggg_sdk\n" + BACKEND
OPERATIONS = ["my_invoices", "profile_update", "invoice_check"]
INSTALL = """
from ggg import Extension
for name, source in (("lab", {lab!r}), ("lab_sdk", {sdk!r})):
    row = Extension[name] or Extension(id=name)
    row.version, row.manifest, row.source, row.operations = "1.0.0", {{}}, source, {ops!r}
    row.access = {{"profiles": [], "departments": []}}
print("installed")
"""
# Instructions of a call inside the canister, as Alice: run as an update (extension_call) and as a query would
# (extension_read, dispatched with query=True).
MEASURE = """
import json
from core import op
from core.op import dispatch, run
def spent(name, extension, function, query):
    arg = json.dumps({{"extension": extension, "function": function, "args": {{}}}})
    start = ic.performance_counter(0)
    reply = json.loads(run(dispatch(name, arg, signer={alice!r}, query=query)))
    assert reply["ok"], reply
    return ic.performance_counter(0) - start
print(json.dumps({{"call": spent("extension_call", "lab", "idle", False),
                  "read": spent("extension_read", "lab", "idle", True),
                  "call_with_sdk": spent("extension_call", "lab_sdk", "idle", False)}}))
"""


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm in DOM, whose ledger says every account holds a billion, with the lab installed and an invoice
    of Alice's that the ledger therefore says is paid."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder, alice = people["ctl"], people["founder"], people["alice"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ok(realm, "setup_launch", founder)
    ok(realm, "realm_join", alice)
    ok(realm, "token_register", ctl, symbol="DOM", ledger=mock_ledger.install(realm.pic, 10**9))
    ok(realm, "fund_create", ctl, fund="ROOT")  # where what an invoice pays in goes
    now = realm.now()
    ok(realm, "period_open", ctl, period="P1", starts_at=now - 86_400, ends_at=now + 365 * 86_400)
    realm.invoice = ok(realm, "invoice_create", ctl, payer=alice, amount=AMOUNT,
                       items=[{"concept": "fee", "amount": AMOUNT}])["invoice"]
    assert "installed" in realm.shell(INSTALL.format(lab=BACKEND, sdk=WITH_SDK, ops=OPERATIONS))
    return realm


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def lab(realm, who, function, query=False, **args):
    return realm.call("extension_read" if query else "extension_call",
                      {"extension": "lab", "function": function, "args": args}, sender=who, query=query)


def test_a_read_is_a_query_of_the_backend(realm, people):
    out = lab(realm, people["alice"], "mine", query=True)
    assert out["ok"] and out["result"]["ok"] and [i["id"] for i in out["result"]["invoices"]] == [realm.invoice]


def test_a_read_cannot_change_the_realm(realm, people):
    out = lab(realm, people["alice"], "rename", query=True, nickname="Al")
    assert out["ok"] and not out["result"]["ok"] and "call it with run" in out["result"]["error"]


def test_an_operation_that_calls_another_canister_cannot_run_at_once(realm, people):
    out = lab(realm, people["alice"], "check_at_once", invoice=realm.invoice)
    assert not out["ok"] and "a query cannot call other canisters" in out["error"] and "yield wait" in out["error"]


def test_a_backend_waits_for_the_ledger_and_resumes_with_the_reply(realm, people):
    out = lab(realm, people["alice"], "check", invoice=realm.invoice)
    assert out["ok"] and out["result"] == {"status": "paid", "then": "paid"}, out


def test_a_backend_that_fails_after_its_wait_traps(realm, people):
    with pytest.raises(Exception, match="lab.check_and_fail failed after it called invoice_check"):
        lab(realm, people["alice"], "check_and_fail", invoice=realm.invoice)


def test_no_interpreter_stays_open_and_the_next_calls_all_start(realm, people):
    """The trap above came after the await, which kept what the message did before it. The replica's cleanup of the
    trapped callback drops the waiting call, whose ``finally`` closes its interpreter (the reaper, api.extensions.reap,
    is for one it did not). The canister has 8: a dozen calls after it each start one and close it."""
    assert json.loads(realm.shell("from api import extensions\nprint(len(extensions._open))")) == 0
    for _ in range(12):
        assert lab(realm, people["alice"], "check", invoice=realm.invoice)["ok"]
    assert json.loads(realm.shell("from api import extensions\nprint(len(extensions._open))")) == 0


def test_what_an_empty_call_costs(realm, people, costs):
    spent = json.loads(realm.shell(MEASURE.format(alice=people["alice"])).strip().splitlines()[-1])
    for what, instructions in spent.items():
        costs.report(f"extension backend, empty {what}", instructions)
    # 141 M with ggg_sdk loaded (the study of 22 Nov 2026); a backend that does not import it skips 51.5 M of that.
    assert spent["call"] < 110_000_000 and spent["read"] < 110_000_000
    assert spent["call_with_sdk"] - spent["call"] > 40_000_000
