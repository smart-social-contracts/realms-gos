import hashlib
import inspect
import json

from core import op
from core.ratio import PERCENT

import pytest

import fake_chain
from api import treasury
import world
from ggg import (Proposal, AllocationRule, Budget, Delegation, Department, Event, FinancialReport, FiscalPeriod, Fund,
                 Invoice, LedgerEntry, Position, Token, Transfer)
from kit import call, denied, ok, rows

LEDGER = "realm-ledger"
FY2026 = {"period": "FY2026", "starts_at": 1_767_225_600, "ends_at": 1_798_761_600}
FINANCE = ("treasury.view", "treasury.manage", "invoice.refresh", "org.manage_budget", "tax.manage", "tax.assess",
           "transfer.delete")


def setup_finance():
    """Agora with Bob as Finance treasurer and an open FY2026."""
    depts = world.build()
    world.seat("finance.treasurer", "bob", depts["Finance"], *FINANCE)
    ok("period_open", "bob", **FY2026)
    return depts


def one_item(amount):
    """The one item of an invoice for ``amount``."""
    return [{"concept": "fee", "amount": amount}]


def draft(fund, category, planned, kind="expense", period="FY2026", by="bob"):
    return ok("budget_draft", by, fund=fund, period=period, category=category, kind=kind, planned=planned)["budget"]


def propose(period="FY2026", by="bob"):
    return ok("budget_propose", by, period=period)


def balance_reply(amount):
    fake_chain.state["replies"][(LEDGER, "icrc1_balance_of")] = f"({amount:_} : nat)"


def ledger_ok(tx=18_234):
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = f"(variant {{ Ok = {tx:_} : nat }})"


def receive(amount, payer="alice"):
    """Bill ``payer``, who pays: the source fund gains ``amount``. The invoice adds the ledger's fee, which the realm pays
    to move the payment into its main account (treasury.collect)."""
    billed = amount + Token["REALM"].fee
    inv = ok("invoice_create", "bob", payer=payer, amount=billed, items=one_item(billed), reason=f"User:{payer}")["invoice"]
    balance_reply(billed)
    assert ok("invoice_check", payer, invoice=inv)["status"] == "paid"
    return inv


def payouts():
    """The ledger transfers that paid someone, from the realm's main account: a collection leaves an invoice's account."""
    return [c for c in fake_chain.state["calls"] if c[1] == "icrc1_transfer" and "from_subaccount = null" in c[2]]


def allocate(**percents):
    shares = [{"fund": fund, "share": percent * PERCENT} for fund, percent in percents.items()]
    rule = ok("allocation_rule_draft", "bob", source_fund="ROOT", rules=shares)["rule"]
    ok("allocation_rule_propose", "bob", rule=rule)
    ok("treasury_configure", "bob", config={"auto_allocate": True})
    return rule


def adopt(proposal, voter="carla"):
    """The deciding vote settles the proposal and runs its action; returns the action's reply."""
    assert ok("proposal_vote", voter, proposal=proposal, choice="yes")["status"] == "executed"
    return Proposal[proposal].result


def funds():
    return {k: v.get("REALM", 0) for k, v in ok("treasury_view", "bob")["funds"].items()}


def balanced():
    txs = {}
    for e in rows(LedgerEntry):
        d, c = txs.get(e.transaction, (0, 0))
        txs[e.transaction] = (d + e.debit, c + e.credit)
    return txs and all(d == c for d, c in txs.values())


def test_story_agora_finance():
    depts = setup_finance()
    ok("token_register", "bob", symbol="ckUSDC", ledger="xevnm", decimals=6, fee=10_000)
    ok("nft_token_register", "bob", symbol="LAND", canister="land-nft", transferable=True)
    ok("treasury_configure", "bob", config={"epoch": "quarterly"})
    ok("fund_create", "bob", fund="WELFARE", name="Welfare", kind="special_revenue")
    allocate(WELFARE=30, ROADS=20)
    receive(300_000)
    assert funds() == {"ROOT": 150_000, "ROADS": 60_000, "WELFARE": 90_000}

    fee = ok("invoice_create", "bob", payer="bob", amount=10, items=one_item(10), reason="Member:bob")
    assert fee["pay_to"]["subaccount"] == fee["invoice"].encode().ljust(32, b"\0").hex()
    assert [i["amount"] for i in ok("my_invoices", "bob")["invoices"]] == [10]

    b = draft("ROADS", "capital", 300_000)
    draft("ROADS", "payroll", 4_000)  # an adopted budget is exhaustive: the salaries below need their line
    propose()
    ok("payment_account_add", "bob", organization="buildco", network="ICP", address="buildco-wallet", symbol="REALM")

    Position["infrastructure.planning_officer"].salary = 4_000
    ledger_ok()
    pay = ok("payroll_run", "bob", department="Infrastructure", period="2026-11")
    assert (pay["seats"], pay["total"]) == (1, 4_000)
    assert "already ran" in call("payroll_run", "bob", department="Infrastructure", period="2026-11")["error"]

    world.grant(depts["Infrastructure"], "transfer.create")
    world.policy(depts["Infrastructure"], 1, 2)
    p = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=50_000, category="capital")["proposal"]
    out = adopt(p)
    assert (out["status"], out["ledger_tx"]) == ("completed", "18234")
    assert 'principal "buildco-wallet"' in fake_chain.state["calls"][-1][2]
    assert Budget[b].actual == 50_000 and funds()["ROADS"] == 5_980

    r = ok("report_issue", "bob", period="FY2026")["report"]
    st = FinancialReport[r].statements
    assert st["debit"] == st["credit"] and st["fund_balances"]["ROADS:REALM"] == 5_980
    assert ok("report_view", "bob", report=r)["report"]["issued_by"] == "bob"
    assert len(ok("ledger_view", "bob", fund="ROADS")["entries"]) == 7

    fake_chain.state["replies"][(LEDGER, "force_transfer")] = "(variant { Ok = 7 : nat })"
    fake_chain.state["replies"][(LEDGER, "freeze_account")] = "(variant { Ok = 8 : nat })"
    ok("token_freeze", "ctl", symbol="REALM", account="mallory", reason="court order CASE-12")
    t = ok("token_force_transfer", "ctl", symbol="REALM", amount=500, to="alice", reason="restitution",
           **{"from": "mallory"})["transfer"]
    assert Transfer[t].status == "completed" and balanced()


def test_I_FIN_1_every_transaction_balances():
    setup_finance()
    allocate(ROADS=25)
    receive(1_000)
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = "(variant { Err = variant { TooOld } })"
    failed = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=50)
    assert failed["status"] == "failed" and funds()["ROADS"] == 140
    assert balanced() and len({e.transaction for e in rows(LedgerEntry)}) == 4


def test_a_delegates_transfer_is_a_proposal_even_where_the_fund_pays_directly():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "transfer.create")
    ledger_ok()
    ok("delegation_grant", "carla", delegate="claude-conn", operations=["transfer.create", "proposal.vote"],
       expires_in=86400)
    out = ok("transfer_create", "claude-conn", on_behalf_of="carla", fund="ROADS", to="buildco", amount=500)
    assert out["requires_vote"] and Transfer.count() == 0
    assert not payouts()
    p = out["proposal"]
    assert "filed" in denied("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="carla")
    assert adopt(p, voter="carla")["status"] == "completed"
    assert Transfer.count() == 1


def test_I_FIN_2_money_leaves_a_fund_only_by_transfer_under_the_owners_policy():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "transfer.create")
    assert "needs transfer.create" in denied("transfer_create", "alice", fund="ROADS", to="buildco", amount=1)
    assert "party" in denied("transfer_create", "carla", fund="ROADS", to="carla", amount=1)
    assert "party" in denied("transfer_create", "carla", fund="ROADS", to="User:carla", amount=1)
    assert "less than" in call("transfer_create", "carla", fund="ROADS", to="buildco", amount=5_000)["error"]
    world.policy(depts["Infrastructure"], 2, 3)
    out = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=500)
    assert out["requires_vote"] and Transfer.count() == 0
    assert not payouts()


def test_transfer_create_is_delegable():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "transfer.create")
    Delegation(id="d1", grantor="carla", delegate="claude-conn", status="active",
               scope={"operations": ["transfer.create"]}, expires_at=fake_chain.now() + 3600)
    ledger_ok()
    out = ok("transfer_create", "claude-conn", fund="ROADS", to="buildco", amount=10, on_behalf_of="carla")
    assert out["requires_vote"] and Transfer.count() == 0


def test_I_FIN_3_invoice_paid_only_when_ledger_shows_amount():
    setup_finance()
    inv = ok("invoice_create", "bob", payer="alice", amount=120, items=one_item(120), reason="User:alice", due_at=fake_chain.now() + 10)
    inv = inv["invoice"]
    balance_reply(119)
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "pending"
    cid, method, arg = fake_chain.state["calls"][-1]
    assert (cid, method) == (LEDGER, "icrc1_balance_of")
    assert treasury.blob(inv.encode().ljust(32, b"\0")) in arg
    assert "not yours" in call("invoice_check", "bob", invoice=inv)["error"]
    fake_chain.state["now"] += 20
    assert ok("invoice_refresh", "bob")["invoices"] == {inv: "overdue"}
    balance_reply(120)
    assert ok("invoice_refresh", "bob", invoice=inv)["paid"] == 1
    assert Invoice[inv].status == "paid" and Invoice[inv].paid_at == fake_chain.now()
    entries = {(e.kind, e.category, e.debit, e.credit) for e in Invoice[inv].ledger_entries}
    # the fund gains the payment less the ledger's fee for moving it into the realm's main account
    assert entries == {("asset", "cash", 110, 0), ("expense", treasury.FEE, 10, 0), ("revenue", "user", 0, 120)}
    assert "needs invoice.refresh" in denied("invoice_refresh", "alice")


def test_I_FIN_4_adopted_budgets_rules_and_policies_are_frozen():
    setup_finance()
    b = draft("ROADS", "capital", 300_000)
    propose()
    assert "no draft budget lines" in call("budget_propose", "bob", period="FY2026")["error"]
    new = ok("budget_amend", "bob", budget=b, planned=400_000)["budget"]
    assert (Budget[b].status, Budget[b].planned) == ("amended", 300_000)
    assert (Budget[new].status, Budget[new].planned) == ("adopted", 400_000)
    assert "only an adopted" in call("budget_amend", "bob", budget=b, planned=1)["error"]
    first = allocate(ROADS=30)
    second = allocate(ROADS=35)
    assert (AllocationRule[first].status, AllocationRule[second].status) == ("superseded", "adopted")
    assert treasury.adopted_rule(Fund["ROOT"]).id == second
    assert "only a draft" in call("allocation_rule_propose", "bob", rule=first)["error"]


def fund_roads_with_budget(planned, funds_held=10_000):
    """ROADS holds ``funds_held`` and has an adopted budget of ``planned`` for its capital payments."""
    setup_finance()
    allocate(ROADS=100)
    receive(funds_held)
    b = draft("ROADS", "capital", planned)
    propose()
    ledger_ok()
    return b


def pay_capital(amount, caller="ctl"):
    return call("transfer_create", caller, fund="ROADS", to="buildco", amount=amount, category="capital")


def test_payment_up_to_the_plan_is_allowed_and_one_unit_over_is_refused():
    b = fund_roads_with_budget(500)
    assert pay_capital(499)["status"] == "completed"
    assert pay_capital(1)["status"] == "completed" and Budget[b].actual == 500
    transfers, entries, held = Transfer.count(), LedgerEntry.count(), funds()["ROADS"]

    refused = pay_capital(1)
    assert refused["ok"] is False and refused["kind"] == "conflict"
    for fact in (b, "(ROADS, FY2026, capital)", "has 0 available", "the transfer of 1 is 1 short"):
        assert fact in refused["error"]
    assert (Transfer.count(), LedgerEntry.count(), funds()["ROADS"], Budget[b].actual) == (transfers, entries, held, 500)


def test_a_payment_in_one_step_past_the_plan_is_refused_whole():
    b = fund_roads_with_budget(500)
    assert "the transfer of 501 is 1 short" in pay_capital(501)["error"]
    assert Transfer.count() == 0 and Budget[b].actual == 0


def test_an_adopted_budget_is_exhaustive_so_a_payment_in_a_category_it_has_no_line_for_is_refused():
    fund_roads_with_budget(500)
    out = call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=900)  # the category is transfer
    assert out["kind"] == "conflict" and "no expense line for transfer" in out["error"]
    assert not Transfer.count()
    assert call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, category="capital")["status"] \
        == "completed"  # and the ledger's fee needs no line


def test_nobody_names_the_ledgers_fee_as_a_category():
    fund_roads_with_budget(500)
    for name, args in (("transfer_create", {"fund": "ROADS", "to": "buildco", "amount": 900}),
                       ("budget_draft", {"fund": "ROADS", "period": "FY2026", "kind": "expense", "planned": 900})):
        out = call(name, "ctl", category="ledger_fee", **args)
        assert out["kind"] == "invalid_argument" and "ledger's fee" in out["error"], (name, out)
    assert not Transfer.count() and Budget.count() == 1  # the budget line of fund_roads_with_budget


def test_the_fee_of_a_payment_is_booked_whatever_the_adopted_budget_says():
    fund_roads_with_budget(500)
    assert call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, category="capital")["status"] \
        == "completed"
    assert {e.category for e in rows(LedgerEntry) if e.kind == "expense"} == {"capital", "ledger_fee"}


def test_a_fund_without_an_adopted_budget_pays_in_any_category():
    setup_finance()
    allocate(ROADS=100)
    receive(10_000)
    ledger_ok()
    assert call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=900)["status"] == "completed"


def test_a_failed_payment_frees_its_budget():
    b = fund_roads_with_budget(500)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = "(variant { Err = variant { TooOld } })"
    assert pay_capital(500)["status"] == "failed" and Budget[b].actual == 0
    ledger_ok()
    assert pay_capital(500)["status"] == "completed"


def voted_payment_setup():
    """Roads with a capital line of 500, and Infrastructure deciding by two votes: carla's payment is a proposal."""
    depts = setup_finance()
    allocate(ROADS=100)
    receive(10_000)
    b = draft("ROADS", "capital", 500)
    propose()
    ledger_ok()
    world.grant(depts["Infrastructure"], "transfer.create")
    world.policy(depts["Infrastructure"], 1, 2)
    return b, depts["Infrastructure"]


def test_a_payment_past_the_plan_is_refused_when_it_is_filed():
    b, _ = voted_payment_setup()

    out = call("transfer_create", "carla", fund="ROADS", to="buildco", amount=501, category="capital")
    assert out["kind"] == "conflict" and "has 500 available; the transfer of 501 is 1 short" in out["error"]
    assert (Proposal.count(), Transfer.count(), Budget[b].actual) == (0, 0, 0)

    within = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=500, category="capital")["proposal"]
    assert adopt(within)["status"] == "completed" and Budget[b].actual == 500


def test_a_voted_payment_the_plan_can_no_longer_meet_fails_with_its_reason_and_pays_nothing():
    b, infrastructure = voted_payment_setup()
    p = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=500, category="capital")["proposal"]
    world.policy(infrastructure, 1, 1)  # while the vote is open, the line spends elsewhere
    assert pay_capital(1, "carla")["status"] == "completed"

    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "failed"
    assert Proposal[p].status == "failed" and "has 499 available; the transfer of 500 is 1 short" in Proposal[p].result["error"]
    assert (Transfer.count(), Budget[b].actual, funds()["ROADS"]) == (1, 1, 10_000 - 1 - 10)  # only the one unit, and its fee


def test_a_payroll_past_the_plan_is_refused_before_anyone_is_paid():
    fund_roads_with_budget(1)
    infrastructure = Department["Infrastructure"]
    world.seat("infrastructure.surveyor", "dana", infrastructure)
    Position["infrastructure.planning_officer"].salary = Position["infrastructure.surveyor"].salary = 2_000
    draft("ROADS", "payroll", 3_999)
    propose()
    entries = LedgerEntry.count()  # the first salary fits the plan: only the check up front keeps it from being paid
    assert "the transfer of 4000 is 1 short" in call("payroll_run", "bob", department="Infrastructure", period="2026-11")["error"]
    assert (Transfer.count(), LedgerEntry.count(), Budget["B-2"].actual) == (0, entries, 0)


def test_payment_after_an_amendment_that_raises_the_plan_is_allowed():
    b = fund_roads_with_budget(500)
    assert pay_capital(500)["status"] == "completed" and "has 0 available" in pay_capital(1)["error"]
    new = ok("budget_amend", "bob", budget=b, planned=800)["budget"]
    assert Budget[new].actual == 500
    assert pay_capital(300)["status"] == "completed" and Budget[new].actual == 800
    assert "has 0 available" in pay_capital(1)["error"]


def test_governed_budget_is_adopted_by_its_proposal():
    depts = setup_finance()
    world.policy(depts["Infrastructure"], 1, 2)
    world.seat("infrastructure.director", "dana", depts["Infrastructure"], "org.manage_budget")
    out = ok("budget_draft", "dana", fund="ROADS", period="FY2026", category="capital", kind="expense", planned=1)
    assert out["requires_vote"] and Budget.count() == 0
    adopt(out["proposal"])
    world.policy(Department["Administration"], 2, 3)  # the budget of the period is the realm's: the root decides
    p = propose(by="dana")["proposal"]
    assert (Budget["B-1"].status, Budget["B-1"].proposal) == ("proposed", p)  # fixed when the proposal is filed
    for voter in world.PEOPLE:
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert (Budget["B-1"].status, Budget["B-1"].proposal) == ("adopted", p)


def test_I_FIN_5_closed_period_accepts_no_entries():
    setup_finance()
    b = draft("ROADS", "capital", 1)
    propose()
    allocate(ROADS=100)
    receive(1_000)
    first = ok("report_issue", "bob", period="FY2026")["report"]
    inv = ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5), reason="User:alice")["invoice"]
    ok("period_close", "bob", period="FY2026")
    assert Budget[b].status == "closed"
    entries = LedgerEntry.count()
    balance_reply(5)
    assert "closed" in call("invoice_check", "alice", invoice=inv)["error"]
    assert "closed" in call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=1)["error"]
    assert "closed" in call("budget_draft", "bob", fund="ROADS", period="FY2026", category="x", planned=1)["error"]
    assert LedgerEntry.count() == entries and Invoice[inv].status == "pending"
    restated = ok("report_issue", "bob", period="FY2026")
    assert restated["supersedes"] == first


def test_I_FIN_6_the_realm_never_moves_a_members_own_funds():
    setup_finance()
    assert "unknown Fund 'alice'" in call("transfer_create", "ctl", fund="alice", to="bob", amount=1)["error"]
    allocate(ROADS=100)
    inv = receive(100)
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="bob", amount=10)
    # icrc1_transfer moves only the caller's own money: the realm's main account, or an invoice's, which the payer paid
    # into (treasury.collect); and the realm takes nothing from another account with ICRC-2
    realm_own = ("from_subaccount = null", f"from_subaccount = opt {treasury.blob(treasury.subaccount(inv))}")
    transfers = [c for c in fake_chain.state["calls"] if c[1] == "icrc1_transfer"]
    assert transfers and all(any(own in c[2] for own in realm_own) for c in transfers)
    assert "transfer_from" not in inspect.getsource(treasury)


def test_transfer_revert_only_pending():
    setup_finance()
    allocate(ROADS=100)
    receive(100)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = RuntimeError("canister stopped")
    out = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=40)
    assert (out["status"], out["error"]) == ("pending", "canister stopped")
    t = Transfer["TR-1"]
    assert t.status == "pending" and funds()["ROADS"] == 50
    assert "needs transfer.delete" in denied("transfer_revert", "alice", transfer=t.id)
    ok("transfer_revert", "bob", transfer=t.id)
    assert t.status == "failed" and funds()["ROADS"] == 100 and balanced()
    assert "only a pending" in call("transfer_revert", "bob", transfer=t.id)["error"]


def suspended(cid, method, candid="()", cycles=0):
    """A ledger call that stays in flight until the test sends its reply (or an exception) in. The caller's message ends
    as the call goes out, and the reply arrives in a new one (fake_chain.end_message)."""
    fake_chain.state["calls"].append((cid, method, candid))
    fake_chain.end_message()
    reply = yield
    fake_chain.end_message()
    if isinstance(reply, Exception):
        raise reply
    return reply


def start(name, caller, **args):
    fake_chain.state["caller"] = caller
    message = op.dispatch(name, json.dumps(args))
    next(message)
    return message


def finish(message, reply):
    with pytest.raises(StopIteration) as done:
        message.send(reply)
    return json.loads(done.value.value)


@pytest.mark.parametrize("reply, status, roads", [("(variant { Ok = 9 : nat })", "completed", 40),
                                                  ("(variant { Err = variant { TooOld } })", "failed", 100)])
def test_transfer_in_flight_cannot_be_reverted(monkeypatch, reply, status, roads):
    setup_finance()
    allocate(ROADS=100)
    receive(100)
    monkeypatch.setattr(fake_chain, "call", suspended)
    message = start("transfer_create", "ctl", fund="ROADS", to="buildco", amount=50)
    t = Transfer["TR-1"]
    assert t.status == "sending" and funds()["ROADS"] == 40
    assert "TR-1 is sending" in call("transfer_revert", "bob", transfer=t.id)["error"]
    assert finish(message, reply)["status"] == status
    assert "only a pending" in call("transfer_revert", "bob", transfer=t.id)["error"]
    assert funds()["ROADS"] == roads and balanced()
    assert len({e.transaction for e in t.ledger_entries}) == (1 if status == "completed" else 2)


# The shape ic-tokens answered with. The treasury no longer reads it: the realm token is ic-python-token.
OLD_TOKEN_PAID = "(record { 1_932_118_984 = null; 624_086_880 = opt (7 : nat); 3_092_129_219 = true })"
OLD_TOKEN_REFUSED = '(record { error = opt "insufficient balance"; block_index = null; success = false })'


@pytest.mark.parametrize("reply, expected", [
    ("(variant { Ok = 9_001 : nat })", ("completed", "9001")),
    ("(variant { 17_724 = 9 : nat })", ("completed", "9")),
    ("(variant { Err = variant { InsufficientFunds = record { balance = 1 : nat } } })", ("failed", None)),
    ("(variant { 3_456_837 = variant { TooOld } })", ("failed", None)),
    (OLD_TOKEN_PAID, ("unconfirmed", None)),
    ("(record { error = null; block_index = opt (7 : nat); success = true })", ("unconfirmed", None)),
    (OLD_TOKEN_REFUSED, ("unconfirmed", None)),
    ("(record { error = null; block_index = null; success = true })", ("unconfirmed", None)),
    ("(record { error = null; block_index = null; success = false })", ("unconfirmed", None)),
    ("(variant { Ok = null })", ("unconfirmed", None)),
    ("(true)", ("unconfirmed", None)),
    ("()", ("unconfirmed", None)),
    ("", ("unconfirmed", None)),
    ("not candid at all", ("unconfirmed", None)),
    ('(record { error = opt "x"', ("unconfirmed", None)),
    ("(variant { Ok = true })", ("unconfirmed", None)),
    ("(variant { Ok = -1 : int })", ("unconfirmed", None)),
    ("(record { error = null; block_index = opt true; success = true })", ("unconfirmed", None)),
    ("(record { error = null; block_index = opt (-1 : int); success = true })", ("unconfirmed", None)),
    ("(" + "opt " * 2000 + "null)", ("unconfirmed", None)),
])
def test_outcome_trusts_only_a_definite_answer(reply, expected):
    assert treasury.outcome(reply) == expected


@pytest.mark.parametrize("reply, status, roads", [("(variant { Ok = 7 : nat })", "completed", 40),
                                                  ("(variant { Err = variant { BadFee = record { expected_fee = 10 : nat } } })", "failed", 100),
                                                  (OLD_TOKEN_PAID, "unconfirmed", 40),
                                                  (OLD_TOKEN_REFUSED, "unconfirmed", 40),
                                                  ("garbage", "unconfirmed", 40)])
def test_transfer_booked_by_the_ledgers_answer(reply, status, roads):
    setup_finance()
    allocate(ROADS=100)
    receive(100)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = reply
    out = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=50)
    t = Transfer["TR-1"]
    assert (out["status"], t.status, funds()["ROADS"]) == (status, status, roads) and balanced()
    assert t.reply == ("" if status == "failed" else reply[:200])
    if status == "unconfirmed":
        assert "only a pending" in call("transfer_revert", "bob", transfer=t.id)["error"]
        assert len({e.transaction for e in t.ledger_entries}) == 1


def test_ledger_reply_does_not_complete_a_reverted_transfer(monkeypatch):
    setup_finance()
    allocate(ROADS=100)
    receive(100)
    monkeypatch.setattr(fake_chain, "call", suspended)
    message = start("transfer_create", "ctl", fund="ROADS", to="buildco", amount=50)
    t = Transfer["TR-1"]
    treasury.reverse(t, "reverted while in flight")
    out = finish(message, "(variant { Ok = 9 : nat })")
    assert (out["status"], out["ledger_tx"], t.status, t.ledger_tx) == ("failed", "9", "failed", "")
    assert funds()["ROADS"] == 100 and len(t.ledger_entries) == 6 and balanced()
    message = start("transfer_create", "ctl", fund="ROADS", to="buildco", amount=50)
    t = Transfer["TR-2"]
    treasury.reverse(t, "reverted while in flight")
    assert finish(message, RuntimeError("canister stopped"))["status"] == "failed"
    assert t.status == "failed" and funds()["ROADS"] == 100


def summed(fund, symbol):
    return sum(e.debit - e.credit for e in fund.ledger_entries if e.kind == "asset" and e.symbol == symbol)


def test_fund_balances_are_the_sum_of_their_ledger_entries():
    setup_finance()
    ok("token_register", "bob", symbol="ckUSDC", ledger=LEDGER, decimals=6, fee=5)
    ok("fund_create", "bob", fund="WELFARE")
    allocate(WELFARE=30, ROADS=20)
    receive(1_000)
    receive(333, payer="dana")
    inv = ok("invoice_create", "bob", payer="alice", amount=700, items=one_item(700), symbol="ckUSDC", reason="User:alice")["invoice"]
    balance_reply(700)
    ok("invoice_check", "alice", invoice=inv)
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=60)
    ok("transfer_create", "ctl", fund="ROOT", to="buildco", amount=100, symbol="ckUSDC")
    Position["infrastructure.planning_officer"].salary = 25
    ok("payroll_run", "ctl", department="Infrastructure", period="2026-11")
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = "(variant { Err = variant { TooOld } })"
    ok("transfer_create", "ctl", fund="WELFARE", to="buildco", amount=40)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = RuntimeError("canister stopped")
    ok("transfer_create", "ctl", fund="WELFARE", to="buildco", amount=50)
    ok("transfer_revert", "bob", transfer=Transfer.load_some(Transfer.max_id(), 1)[0].id)

    summed_view = {f: {} for f in ("ROOT", "ROADS", "WELFARE")}
    for e in rows(LedgerEntry):
        if e.kind == "asset" and e.fund is not None:
            summed_view[e.fund.id][e.symbol] = summed_view[e.fund.id].get(e.symbol, 0) + e.debit - e.credit
    assert ok("treasury_view", "bob")["funds"] == summed_view
    for code in summed_view:
        for symbol in ("REALM", "ckUSDC"):
            assert treasury.balance(Fund[code], symbol) == summed(Fund[code], symbol)
    assert summed_view["ROADS"]["REALM"] == 200 + 66 - 70 - 35


def test_report_statements_and_hash_match_the_periods_entries():
    setup_finance()
    allocate(ROADS=40)
    receive(500)
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=70, category="capital")
    rows = sorted(FiscalPeriod["FY2026"].ledger_entries, key=lambda e: int(e.id.split("-")[1]))
    accounts, balances = {}, {}
    for e in rows:
        a = accounts.setdefault(f"{e.kind}:{e.category}:{e.symbol}", {"debit": 0, "credit": 0})
        a["debit"], a["credit"] = a["debit"] + e.debit, a["credit"] + e.credit
        if e.kind == "asset" and e.fund is not None:
            balances[f"{e.fund.id}:{e.symbol}"] = balances.get(f"{e.fund.id}:{e.symbol}", 0) + e.debit - e.credit
    chained = hashlib.sha256(b"").hexdigest()
    for e in rows:
        line = json.dumps([e.id, e.transaction, e.debit, e.credit, e.symbol])
        chained = hashlib.sha256((chained + line).encode()).hexdigest()
    out = ok("report_issue", "bob", period="FY2026")
    assert out["source_hash"] == "sha256:" + chained
    assert FinancialReport[out["report"]].statements == {
        "accounts": dict(sorted(accounts.items())), "fund_balances": dict(sorted(balances.items())),
        "debit": sum(e.debit for e in rows), "credit": sum(e.credit for e in rows)}


def test_periods_open_in_order_and_bookings_find_theirs():
    setup_finance()
    year = 365 * 86400
    earlier = {"period": "FY2025", "starts_at": FY2026["starts_at"] - year, "ends_at": FY2026["starts_at"]}
    assert "starts before fiscal period FY2026 ends" in call("period_open", "bob", **earlier)["error"]
    for n in (1, 2, 3):
        ok("period_open", "bob", period=f"FY{2026 + n}", starts_at=FY2026["ends_at"] + (n - 1) * year,
           ends_at=FY2026["ends_at"] + n * year)
    assert treasury.period().id == "FY2026"
    fake_chain.state["now"] = FY2026["ends_at"] + year + 5
    receive(10)
    assert {e.period.id for e in LedgerEntry.load_some(1, 10)} == {"FY2028"}
    ok("period_close", "bob", period="FY2028")
    assert "fiscal period FY2028 is closed" in call("invoice_refresh", "bob")["error"]
    fake_chain.state["now"] = FY2026["ends_at"] + 3 * year
    assert treasury.period() is None


def test_treasury_view_reads_funds_a_page_at_a_time():
    setup_finance()
    ok("fund_create", "bob", fund="WELFARE")
    first = ok("treasury_view", "bob", count=2)
    assert list(first["funds"]) == ["ROOT", "ROADS"] and first["tokens"][0]["id"] == "REALM"
    second = ok("treasury_view", "bob", count=2, **{"from": first["next"]})
    assert (list(second["funds"]), second["next"]) == (["WELFARE"], None)


def test_registries_are_bounded(monkeypatch):
    setup_finance()
    monkeypatch.setattr(treasury, "REGISTRY", 2)
    ok("token_register", "bob", symbol="ckUSDC", ledger="x")
    assert "registry is full" in call("token_register", "bob", symbol="ckETH", ledger="y")["error"]
    ok("token_register", "bob", symbol="ckUSDC", ledger="z")
    ok("nft_token_register", "bob", symbol="LAND", canister="a")
    ok("nft_token_register", "bob", symbol="DEED", canister="b")
    assert "registry is full" in call("nft_token_register", "bob", symbol="PASS", canister="c")["error"]


def test_invoice_refresh_checks_a_batch_at_a_time():
    setup_finance()
    due = fake_chain.now() + 10
    invoices = [ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5), due_at=due)["invoice"] for _ in range(3)]
    ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5))
    fake_chain.state["now"] += 20
    balance_reply(0)
    Invoice[invoices[1]].status = "overdue"
    first = ok("invoice_refresh", "bob", count=2)
    assert first["invoices"] == {invoices[0]: "overdue", invoices[1]: "overdue"} and first["next"]
    balance_reply(5)
    second = ok("invoice_refresh", "bob", count=2, **{"from": first["next"]})
    assert (second["checked"], second["paid"], second["next"]) == (2, 2, None)
    assert len([c for c in fake_chain.state["calls"] if c[1] == "icrc1_balance_of"]) == 4
    balance_reply(0)
    assert ok("invoice_refresh", "bob")["invoices"] == {invoices[0]: "overdue", invoices[1]: "overdue"}


def test_invoice_refresh_makes_at_most_twenty_ledger_calls():
    setup_finance()
    for _ in range(25):
        ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5))
    balance_reply(0)
    out = ok("invoice_refresh", "bob", count=100)
    assert out["checked"] == 20 and out["next"]
    assert ok("invoice_refresh", "bob", **{"from": out["next"]})["checked"] == 5


# --- a payment reaches the account the realm pays from (realms-gos#739, #740) ----------------------

def collections():
    """The ledger transfers that moved a payment out of an invoice's account (treasury.collect)."""
    return [c for c in fake_chain.state["calls"] if c[1] == "icrc1_transfer" and "from_subaccount = opt" in c[2]]


def billed(amount):
    return ok("invoice_create", "bob", payer="alice", amount=amount, items=one_item(amount))["invoice"]


def test_a_new_realm_books_payments_into_a_fund_that_the_root_governs():
    setup_finance()
    assert treasury.treasury_config()["source_fund"] == "ROOT"
    fund = Fund["ROOT"]
    assert fund.department is None and treasury.owner_of(fund).id == "Administration"
    assert ok("realm_monitor", "ctl")["problems"] == []
    # a codex may still give the root department a fund of its own, as Agora's and Syntropia's seeds do
    ok("fund_create", "bob", fund="DEPT-ADMIN", department="Administration")


def test_a_realm_without_its_source_fund_refuses_the_payment_in_words_and_books_it_once_the_fund_exists():
    setup_finance()
    Fund["ROOT"].delete()  # a realm made before setup created the fund
    assert ok("realm_monitor", "ctl")["problems"] == [treasury.NO_SOURCE_FUND.format("ROOT")]
    inv = billed(120)
    balance_reply(120)
    refused = call("invoice_check", "alice", invoice=inv)
    assert refused["kind"] == "unavailable" and "fund_create" in refused["error"] and "treasury_configure" in refused["error"]
    assert Invoice[inv].status == "pending" and not collections(), "the payment waits in the invoice's account"
    ok("fund_create", "bob", fund="ROOT")
    ledger_ok()
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert funds()["ROOT"] == 110 and ok("realm_monitor", "ctl")["problems"] == []


def test_a_source_fund_configured_elsewhere_takes_the_payments_and_root_none():
    setup_finance()
    ok("treasury_configure", "bob", config={"source_fund": "ROADS"})
    ledger_ok()
    balance_reply(120)
    assert ok("invoice_check", "alice", invoice=billed(120))["status"] == "paid"
    assert funds() == {"ROOT": 0, "ROADS": 110}


def test_a_payment_moves_into_the_realms_main_account_less_the_fee():
    setup_finance()
    inv = billed(120)
    balance_reply(125)  # paid 5 over
    ledger_ok(tx=55)
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    # all the invoice's account holds leaves it, less the fee; the 5 over reach the main account unbooked
    moved = treasury.icrc1_arg(treasury.account(fake_chain.SELF), 115, inv.encode(), 10, from_sub=treasury.subaccount(inv))
    assert [c[2] for c in collections()] == [moved]
    assert (Invoice[inv].payment["collected"], Invoice[inv].payment["block"], Invoice[inv].payment["moved"]) == \
        (True, "55", 115)
    assert funds()["ROOT"] == 110 and balanced()
    calls = len(fake_chain.state["calls"])
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert len(fake_chain.state["calls"]) == calls, "a collected payment needs no more calls"


@pytest.mark.parametrize("answer", [Exception("canister stopped"), "(variant { Err = variant { TemporarilyUnavailable } })"])
def test_a_payment_the_ledger_did_not_move_is_moved_at_the_next_check(answer):
    setup_finance()
    inv = billed(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = answer
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert Invoice[inv].payment["collected"] is False and funds()["ROOT"] == 110
    ledger_ok(tx=56)  # the invoice's account still holds the payment
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert len(collections()) == 2 and Invoice[inv].payment["block"] == "56"
    assert funds()["ROOT"] == 110, "booked once"


def test_a_payment_moved_by_a_transfer_whose_answer_was_lost_is_not_moved_again():
    setup_finance()
    inv = billed(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = "garbage"  # an answer that says nothing
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert Invoice[inv].payment["collected"] is False
    balance_reply(0)  # the transfer had gone through: the next check reads the account, not what it meant to move
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert len(collections()) == 1 and Invoice[inv].payment["collected"] is True and "block" not in Invoice[inv].payment
    assert funds()["ROOT"] == 110


def test_a_payment_left_on_a_ledger_its_token_has_moved_from_is_never_marked_collected():
    setup_finance()
    inv = billed(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    ok("token_register", "bob", symbol="REALM", ledger="new-ledger")
    calls = len(fake_chain.state["calls"])
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert len(fake_chain.state["calls"]) == calls, "the new ledger's empty account is not read for the payment"
    assert Invoice[inv].payment["collected"] is False and funds()["ROOT"] == 110


def test_a_payment_whose_fee_changed_before_its_move_is_not_moved_and_says_why():
    setup_finance()
    inv = billed(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    ok("token_register", "bob", symbol="REALM", ledger=LEDGER, fee=12)
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert len(collections()) == 1, "the retry sends nothing"
    payment = Invoice[inv].payment
    assert payment["stuck"] == treasury.FEE_CHANGED.format(booked=10, now=12) and payment["collected"] is False
    assert funds()["ROOT"] == 110, "the books keep the fee they were booked with"
    ok("token_register", "bob", symbol="REALM", ledger=LEDGER, fee=10)
    ledger_ok()
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert Invoice[inv].payment["collected"] is True and "stuck" not in Invoice[inv].payment


def test_a_move_the_ledger_refuses_for_its_fee_names_the_fee_it_asks():
    setup_finance()
    inv = billed(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = \
        "(variant { Err = variant { BadFee = record { expected_fee = 12 : nat } } })"
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    payment = Invoice[inv].payment
    assert payment["stuck"] == treasury.FEE_CHANGED.format(booked=10, now=12) and payment["collected"] is False


def hashed(name):
    from core import candid

    return f"{candid.idl_hash(name):_}"


@pytest.mark.parametrize("reply, fee", [
    ("(variant { Err = variant { BadFee = record { expected_fee = 12 : nat } } })", 12),
    (f"(variant {{ {hashed('Err')} = variant {{ {hashed('BadFee')} = record {{ {hashed('expected_fee')} = 7 : nat }} }} }})", 7),
    ("(variant { Err = variant { InsufficientFunds = record { balance = 1 : nat } } })", None),
    ("(variant { Ok = 9 : nat })", None),
    ("garbage", None),
])
def test_asked_fee_reads_only_a_bad_fee(reply, fee):
    assert treasury.asked_fee(reply) == fee


def waiting():
    """What realm_monitor says of the payments not yet moved into the realm's account, and their count."""
    said = [p for p in ok("realm_monitor", "ctl")["problems"] if "not yet moved" in p]
    return said, treasury.Treasury["1"].uncollected


def test_realm_monitor_counts_the_payments_that_wait_to_be_moved():
    setup_finance()
    assert waiting() == ([], 0)
    first, second = billed(120), billed(130)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    ok("invoice_check", "alice", invoice=first)
    balance_reply(130)
    ok("invoice_check", "alice", invoice=second)
    assert waiting() == ([treasury.UNCOLLECTED.format(count=2)], 2)
    ledger_ok()
    ok("invoice_check", "alice", invoice=first)
    assert waiting() == ([treasury.UNCOLLECTED.format(count=1)], 1)
    balance_reply(0)  # the second's move had gone through: its next check finds the account empty
    ok("invoice_check", "alice", invoice=second)
    assert waiting() == ([], 0)


def test_a_payment_moved_at_once_or_too_small_to_move_never_counts():
    setup_finance()
    ledger_ok()
    balance_reply(120)
    ok("invoice_check", "alice", invoice=billed(120))
    balance_reply(10)
    ok("invoice_check", "alice", invoice=billed(10))  # no more than the fee: nothing to move
    assert waiting() == ([], 0)


def test_a_move_answered_after_another_check_marked_it_collected_takes_it_off_the_count_once(monkeypatch):
    setup_finance()
    first, second = billed(120), billed(130)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    balance_reply(120)
    ok("invoice_check", "alice", invoice=first)
    balance_reply(130)
    ok("invoice_check", "alice", invoice=second)
    assert waiting()[1] == 2
    monkeypatch.setattr(fake_chain, "call", suspended)
    moving = start("invoice_check", "alice", invoice=first)
    moving.send("(120 : nat)")  # its retry reads the account and sends the move, which stays in flight
    checking = start("invoice_check", "alice", invoice=first)
    assert finish(checking, "(0 : nat)")["status"] == "paid"  # the move has applied: it marks the payment collected
    assert finish(moving, "(variant { Ok = 9 : nat })")["status"] == "paid"
    assert waiting()[1] == 1, "the second still waits; the first came off once"


def test_a_payment_the_count_never_held_is_not_taken_off_it():
    setup_finance()
    inv = Invoice[billed(120)]
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    ok("invoice_check", "alice", invoice=inv.id)
    # as a build that kept no count left it: waiting, and not in the count
    inv.payment = {k: v for k, v in inv.payment.items() if k != "counted"}
    treasury.Treasury["1"].uncollected = 0
    ledger_ok()
    ok("invoice_check", "alice", invoice=inv.id)
    assert Invoice[inv.id].payment["collected"] is True and waiting() == ([], 0)


def test_a_count_below_zero_is_reported_as_broken_not_hidden():
    setup_finance()
    treasury.Treasury["1"].uncollected = -1
    assert ok("realm_monitor", "ctl")["problems"] == [treasury.COUNT_BROKEN.format(count=-1)]


def waiting_by_scan():
    """The payments that wait to be moved, found by reading every invoice: what the count stands for."""
    return sum(1 for i in rows(Invoice) if i.payment.get("collected") is False and i.payment.get("counted"))


def test_the_count_is_the_number_of_payments_that_wait(monkeypatch):
    """The count is a cache of what the invoices say. Through receipts, moves that fail, retries, a lost answer, a move
    interrupted by another check and invoice_collect, it stays equal to a scan of the invoices."""
    setup_finance()

    def agrees():
        assert treasury.Treasury["1"].uncollected == waiting_by_scan()

    refuse = Exception("canister stopped")
    ok_now, failing, lost, tiny = billed(120), billed(130), billed(140), billed(10)
    ledger_ok(tx=70)
    balance_reply(120)
    ok("invoice_check", "alice", invoice=ok_now)  # moved at once
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = refuse
    balance_reply(130)
    ok("invoice_check", "alice", invoice=failing)  # waits
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = "garbage"
    balance_reply(140)
    ok("invoice_check", "alice", invoice=lost)  # the move went through, its answer said nothing: waits
    balance_reply(10)
    ok("invoice_check", "alice", invoice=tiny)  # nothing to move
    agrees()
    assert waiting_by_scan() == 2

    balance_reply(0)
    ok("invoice_check", "alice", invoice=lost)  # the account is empty: marked collected, nothing sent
    agrees()

    old = paid_before_collection(150)
    balance_reply(150)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = refuse
    assert ok("invoice_collect", "bob", invoice=old)["collected"] is False
    agrees()
    assert waiting_by_scan() == 2

    monkeypatch.setattr(fake_chain, "call", suspended)
    moving = start("invoice_check", "alice", invoice=failing)
    moving.send("(130 : nat)")  # the retry sends the move, which stays in flight
    checking = start("invoice_check", "alice", invoice=failing)
    finish(checking, "(0 : nat)")  # the move has applied: this check marks it collected
    finish(moving, "(variant { Ok = 71 : nat })")
    agrees()
    retry = start("invoice_check", "alice", invoice=old)
    retry.send("(150 : nat)")
    finish(retry, "(variant { Ok = 72 : nat })")
    agrees()
    assert waiting_by_scan() == 0 and waiting() == ([], 0)


def test_an_invoice_of_no_more_than_the_fee_credits_no_fund_and_moves_nothing():
    setup_finance()
    inv = billed(10)  # the fee of REALM
    balance_reply(10)
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert funds()["ROOT"] == 0 and not collections() and Invoice[inv].payment["collected"] is True and balanced()


def test_two_checks_in_flight_book_a_payment_once(monkeypatch):
    setup_finance()
    inv = billed(120)
    monkeypatch.setattr(fake_chain, "call", suspended)
    first = start("invoice_check", "alice", invoice=inv)
    second = start("invoice_check", "alice", invoice=inv)
    first.send("(120 : nat)")  # it books the payment, and waits on moving it
    assert finish(second, "(120 : nat)")["status"] == "paid"
    assert finish(first, "(variant { Ok = 9 : nat })")["status"] == "paid"
    assert len({e.transaction for e in Invoice[inv].ledger_entries}) == 1 and funds()["ROOT"] == 110
    assert len(collections()) == 1
    assert waiting() == ([], 0), "counted once, and taken away once"


def test_a_line_added_while_a_check_waits_on_the_ledger_is_kept_and_billed(monkeypatch):
    setup_finance()
    inv = billed(120)
    monkeypatch.setattr(fake_chain, "call", suspended)
    check = start("invoice_check", "alice", invoice=inv)
    ok("invoice_item_add", "bob", invoice=inv, item={"concept": "late_fee", "amount": 30})
    assert finish(check, "(120 : nat)")["status"] == "pending", "120 is paid of 150"
    assert Invoice[inv].amount == 150 and [i["concept"] for i in Invoice[inv].items] == ["fee", "late_fee"]
    assert not Invoice[inv].ledger_entries


def test_a_payment_read_on_a_ledger_its_token_moved_from_meanwhile_is_not_booked(monkeypatch):
    setup_finance()
    inv = billed(120)
    monkeypatch.setattr(fake_chain, "call", suspended)
    check = start("invoice_check", "alice", invoice=inv)  # reads the invoice's account on realm-ledger
    ok("token_register", "bob", symbol="REALM", ledger="new-ledger")
    assert finish(check, "(120 : nat)")["status"] == "pending"
    assert not Invoice[inv].ledger_entries and funds()["ROOT"] == 0, "neither booked nor marked as on the new ledger"


def test_a_payment_whose_token_moved_while_its_retry_read_the_old_ledger_stays_uncollected(monkeypatch):
    setup_finance()
    inv = billed(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    monkeypatch.setattr(fake_chain, "call", suspended)
    check = start("invoice_check", "alice", invoice=inv)  # the retry reads the account on realm-ledger
    ok("token_register", "bob", symbol="REALM", ledger="new-ledger")
    assert finish(check, "(120 : nat)")["status"] == "paid"
    assert fake_chain.state["calls"][-1][1] == "icrc1_balance_of", "no move on either ledger"
    assert Invoice[inv].payment["collected"] is False and Invoice[inv].payment["ledger"] == LEDGER


# --- moving the payment of an invoice paid before the realm did so itself -------------------------

def paid_before_collection(amount):
    """An invoice paid and booked as the release before collection did: ROOT credited the whole amount, the money left in
    the invoice's own account, and a payment that names no ``collected``."""
    inv = Invoice[billed(amount)]
    root = Fund["ROOT"]
    treasury.post(treasury.period(), [("asset", "cash", amount, 0, root), ("revenue", "invoice", 0, amount, root)],
                  "REALM", f"payment of {inv.id}", invoice=inv, user="alice")
    inv.status, inv.payment = "paid", {"received": amount, "subaccount": treasury.pay_to(inv)}
    return inv.id


def test_the_payment_of_an_invoice_paid_before_collection_is_moved_by_a_governed_call():
    setup_finance()
    inv = paid_before_collection(120)
    balance_reply(120)
    ledger_ok(tx=60)
    reply = ok("invoice_collect", "bob", invoice=inv)
    assert reply == {"ok": True, "invoice": inv, "collected": True, "moved": 110, "fee": 10}
    moved = treasury.icrc1_arg(treasury.account(fake_chain.SELF), 110, inv.encode(), 10, from_sub=treasury.subaccount(inv))
    assert [c[2] for c in collections()] == [moved]
    payment = Invoice[inv].payment
    assert (payment["block"], payment["moved"], payment["collected_by"]) == ("60", 110, "bob")
    assert "proposal" not in payment, "bob acted himself, without a vote"
    assert funds()["ROOT"] == 110 and balanced(), "ROOT bears the ledger's fee"
    fee = [e for e in Invoice[inv].ledger_entries if e.category == treasury.FEE]
    assert [(e.debit, e.description) for e in fee] == [(10, f"moving the payment of {inv} into the realm's account")]
    events = [e for e in rows(Event) if e.op == "invoice_collect"]
    assert [(e.user, e.result["moved"], e.result["fee"]) for e in events] == [("bob", 110, 10)]
    again = call("invoice_collect", "bob", invoice=inv)
    assert again["kind"] == "conflict" and "invoice_refresh naming the invoice" in again["error"]


def test_where_the_root_decides_by_vote_moving_a_payment_is_a_proposal():
    setup_finance()
    inv = paid_before_collection(120)
    world.policy(Department["Administration"], 2, 3)
    p = ok("invoice_collect", "bob", invoice=inv)["proposal"]
    assert not collections() and "collected" not in Invoice[inv].payment
    balance_reply(120)
    ledger_ok(tx=62)
    for voter in world.PEOPLE:
        if Proposal[p].status == "voting":
            ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Proposal[p].result["moved"] == 110
    payment = Invoice[inv].payment
    assert (payment["collected_by"], payment["proposal"], payment["block"]) == ("bob", p, "62"), "who filed it, the vote"


def test_a_payment_whose_fund_no_entry_names_is_refused_in_words():
    setup_finance()
    inv = Invoice[billed(120)]
    inv.status, inv.payment = "paid", {"received": 120, "subaccount": treasury.pay_to(inv)}  # booked nowhere
    refused = call("invoice_collect", "bob", invoice=inv.id)
    assert refused["kind"] == "conflict" and "no revenue entry" in refused["error"]


def test_only_a_payment_booked_before_collection_and_still_in_its_account_is_moved():
    setup_finance()
    assert "only the payment of a paid invoice" in call("invoice_collect", "bob", invoice=billed(5))["error"]
    inv = paid_before_collection(120)
    balance_reply(119)  # less than it was paid with: not on this token's ledger, or moved by other means
    refused = call("invoice_collect", "bob", invoice=inv)
    assert refused["kind"] == "conflict" and "not on this token's ledger" in refused["error"]
    assert funds()["ROOT"] == 120 and not collections() and "collected" not in Invoice[inv].payment
    Fund["ROOT"].balances = {"REALM": 9}  # spent below the fee since
    assert "less than the ledger's fee" in call("invoice_collect", "bob", invoice=inv)["error"]


def test_a_move_the_ledger_does_not_make_is_made_at_the_next_check():
    setup_finance()
    inv = paid_before_collection(120)
    balance_reply(120)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = Exception("canister stopped")
    assert ok("invoice_collect", "bob", invoice=inv)["collected"] is False
    assert waiting()[1] == 1
    ledger_ok(tx=61)
    assert ok("invoice_check", "alice", invoice=inv)["status"] == "paid"
    assert waiting() == ([], 0)
    assert Invoice[inv].payment["block"] == "61" and len(collections()) == 2 and funds()["ROOT"] == 110
    assert Invoice[inv].payment["moved"] == 110, "the move the retry made records what it moved"


def test_two_calls_in_flight_move_a_payment_and_book_its_fee_once(monkeypatch):
    setup_finance()
    inv = paid_before_collection(120)
    monkeypatch.setattr(fake_chain, "call", suspended)
    first = start("invoice_collect", "bob", invoice=inv)
    second = start("invoice_collect", "bob", invoice=inv)
    first.send("(120 : nat)")  # it books the fee, and waits on the move
    assert "invoice_refresh naming the invoice" in finish(second, "(120 : nat)")["error"]
    assert finish(first, "(variant { Ok = 9 : nat })")["collected"] is True
    assert funds()["ROOT"] == 110 and balanced() and len(collections()) == 1


def test_a_token_moved_while_its_account_is_read_changes_nothing(monkeypatch):
    setup_finance()
    inv = paid_before_collection(120)
    monkeypatch.setattr(fake_chain, "call", suspended)
    collecting = start("invoice_collect", "bob", invoice=inv)  # reads the invoice's account on realm-ledger
    ok("token_register", "bob", symbol="REALM", ledger="new-ledger")
    assert "moved to another ledger" in finish(collecting, "(120 : nat)")["error"]
    assert funds()["ROOT"] == 120 and "collected" not in Invoice[inv].payment


def test_reports_and_my_invoices_are_paginated():
    setup_finance()
    reports = [ok("report_issue", "bob", period="FY2026")["report"] for _ in range(3)]
    first = ok("report_view", "bob", count=2)
    second = ok("report_view", "bob", count=2, **{"from": first["next"]})
    assert [r["id"] for r in first["reports"] + second["reports"]] == reports and second["next"] is None
    mine = [ok("invoice_create", "bob", payer="alice", amount=n, items=one_item(n))["invoice"] for n in (1, 2, 3)]
    ok("invoice_create", "bob", payer="dana", amount=4, items=one_item(4))
    first = ok("my_invoices", "alice", count=2)
    second = ok("my_invoices", "alice", count=2, **{"from": first["next"]})
    assert [i["id"] for i in first["invoices"] + second["invoices"]] == mine and second["next"] is None


def test_signing_ops_need_a_human():
    depts = setup_finance()
    world.seat("finance.bot", "records-bot", depts["Finance"], "treasury.manage", "token.freeze", agent=True)
    assert "human" in denied("report_issue", "records-bot", period="FY2026")
    assert "human" in denied("token_freeze", "records-bot", symbol="REALM", account="mallory")
    assert "party" in denied("token_freeze", "ctl", symbol="REALM", account="ctl")


def test_payment_accounts_are_own():
    setup_finance()
    a = ok("payment_account_add", "alice", network="ckBTC", address="alice-btc", symbol="ckBTC")["account"]
    assert "not yours" in call("payment_account_remove", "bob", account=a)["error"]
    assert "do not administer" in call("payment_account_add", "alice", organization="buildco", network="x",
                                       address="y")["error"]
    ok("payment_account_remove", "alice", account=a)


FINANCE_OPS = ["treasury_view", "ledger_view", "report_view", "treasury_configure", "token_register",
               "nft_token_register", "transfer_create", "transfer_revert", "invoice_create", "invoice_refresh",
               "fund_create", "period_open", "period_close", "budget_draft", "budget_propose", "budget_amend",
               "allocation_rule_draft", "allocation_rule_propose", "payroll_run", "report_issue",
               "token_force_transfer", "token_freeze"]
SELF_OPS = ["invoice_check", "my_invoices", "payment_account_add", "payment_account_remove"]


@pytest.mark.parametrize("name", FINANCE_OPS)
def test_members_cannot_run_finance_ops(name):
    world.build()
    assert "needs" in denied(name, "mallory")


@pytest.mark.parametrize("name", SELF_OPS)
def test_self_ops_need_sign_in_and_a_power_of_attorney(name):
    world.build()
    assert "sign in" in denied(name, fake_chain.ANONYMOUS)
    assert "power of attorney" in denied(name, "claude-conn", on_behalf_of="alice")


def test_token_register_updates():
    setup_finance()
    ok("token_register", "bob", symbol="REALM", ledger="new-ledger")
    assert (Token["REALM"].ledger, Token["REALM"].fee) == ("new-ledger", 10)
    world.policy(Department["Administration"], 2, 3)
    assert ok("token_register", "bob", symbol="ckETH", ledger="eth")["requires_vote"]


def test_a_member_reads_the_budgets_of_the_funds():
    setup_finance()
    ok("fund_create", "bob", fund="WELFARE", name="Welfare", kind="special_revenue")
    b = draft("ROADS", "capital", 300_000)
    propose()
    spare_line = draft("ROADS", "upkeep", 5)
    rule = allocate(ROADS=30, WELFARE=20)
    spare = ok("allocation_rule_draft", "bob", source_fund="ROOT", rules=[{"fund": "ROADS", "share": 10 * PERCENT}])["rule"]

    seen = ok("budget_view", "mallory")
    funds = {f["id"]: f for f in seen["funds"]}
    assert set(funds) == {"ROOT", "ROADS", "WELFARE"}
    assert funds["ROOT"]["rule"] == {"id": rule, "rules": [{"fund": "ROADS", "share": 30 * PERCENT}, {"fund": "WELFARE", "share": 20 * PERCENT}]}
    assert funds["ROADS"]["rule"] is None
    assert [(x["id"], x["period"], x["planned"], x["actual"]) for x in funds["ROADS"]["budgets"]] == [
        (b, "FY2026", 300_000, 0)]  # the draft is not shown as a budget
    assert spare_line not in str(seen["funds"])
    assert (seen["period"], [p["id"] for p in seen["periods"]]) == ("FY2026", ["FY2026"])
    assert seen["config"]["auto_allocate"] is True
    assert (seen["currency"], seen["decimals"]) == ("REALM", 8)
    assert [r["id"] for r in seen["rule_drafts"]] == [spare]


def test_budget_view_reads_one_period_and_only_its_adopted_budgets():
    setup_finance()
    b = draft("ROOT", "upkeep", 10)
    propose()
    for planned in range(11, 16):  # five superseded versions of the same line
        b = ok("budget_amend", "bob", budget=b, planned=planned)["budget"]
    draft("ROOT", "spare", 1)
    ok("period_open", "bob", period="FY2027", starts_at=FY2026["ends_at"], ends_at=FY2026["ends_at"] + 100)
    later = draft("ROOT", "roads", 99, period="FY2027")
    propose("FY2027")

    def lines(**args):
        seen = ok("budget_view", "mallory", **args)
        return seen["period"], [(x["id"], x["period"], x["planned"]) for x in seen["funds"][0]["budgets"]]

    assert lines() == ("FY2026", [(b, "FY2026", 15)])  # the period covering now: the latest version only
    assert lines(period="FY2027") == ("FY2027", [(later, "FY2027", 99)])
    assert [p["id"] for p in ok("budget_view", "mallory")["periods"]] == ["FY2026", "FY2027"]
    assert "unknown FiscalPeriod" in denied("budget_view", "mallory", period="FY1999")


def test_budget_view_without_a_period_covering_now_lists_no_budgets():
    setup_finance()
    draft("ROOT", "x", 1)
    propose()
    fake_chain.state["now"] = FY2026["ends_at"] + 1
    seen = ok("budget_view", "mallory")
    assert seen["period"] is None and seen["funds"][0]["budgets"] == []


def test_budget_view_does_not_show_ledger_entries_and_is_paginated():
    setup_finance()
    receive(300_000)
    ok("fund_create", "bob", fund="WELFARE")
    seen = ok("budget_view", "mallory", count=2)
    assert len(seen["funds"]) == 2 and seen["next"]
    assert "entries" not in seen and "transaction" not in str(seen)
    assert len(ok("budget_view", "mallory", count=2, **{"from": seen["next"]})["funds"]) == 1
    assert "needs" in denied("ledger_view", "mallory")


def test_budget_view_needs_a_signed_in_member():
    world.build()
    assert "sign in" in denied("budget_view", fake_chain.ANONYMOUS)
