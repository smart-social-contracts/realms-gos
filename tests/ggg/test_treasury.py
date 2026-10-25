import inspect

from db import each

import pytest

import fake_chain
import treasury
import world
from ggg import (AllocationRule, Budget, Delegation, Department, FinancialReport, Invoice, LedgerEntry, Position,
                 Token, Transfer)
from kit import call, denied, ok

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


def balance_reply(amount):
    fake_chain.state["replies"][(LEDGER, "icrc1_balance_of")] = f"({amount:_} : nat)"


def ledger_ok(tx=18_234):
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = f"(variant {{ Ok = {tx:_} : nat }})"


def receive(amount, payer="alice"):
    inv = ok("invoice_create", "bob", payer=payer, amount=amount, reason=f"User:{payer}")["invoice"]
    balance_reply(amount)
    assert ok("invoice_check", payer, invoice=inv)["status"] == "paid"
    return inv


def allocate(**shares):
    rule = ok("allocation_rule_draft", "bob", source_fund="ROOT", rules=shares)["rule"]
    ok("allocation_rule_propose", "bob", rule=rule)
    ok("treasury_configure", "bob", config={"auto_allocate": True})
    return rule


def adopt(proposal, voter="carla"):
    ok("proposal_vote", voter, proposal=proposal, choice="yes")
    ok("proposal_finalize", "ctl", proposal=proposal)
    return ok("proposal_execute", "ctl", proposal=proposal)["result"]


def funds():
    return {k: v.get("REALM", 0) for k, v in ok("treasury_view", "bob")["funds"].items()}


def balanced():
    txs = {}
    for e in each(LedgerEntry):
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

    fee = ok("invoice_create", "bob", payer="bob", amount=10, reason="Member:bob")
    assert fee["pay_to"]["subaccount"] == fee["invoice"].encode().ljust(32, b"\0").hex()
    assert [i["amount"] for i in ok("my_invoices", "bob")["invoices"]] == [10]

    b = ok("budget_draft", "bob", fund="ROADS", period="FY2026", category="capital", planned=300_000)["budget"]
    ok("budget_propose", "bob", budget=b)
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
    assert balanced() and len({e.transaction for e in each(LedgerEntry)}) == 4


def test_I_FIN_2_money_leaves_a_fund_only_by_transfer_under_the_owners_policy():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "transfer.create")
    assert "needs transfer.create" in denied("transfer_create", "alice", fund="ROADS", to="buildco", amount=1)
    assert "party" in denied("transfer_create", "carla", fund="ROADS", to="carla", amount=1)
    assert "less than" in call("transfer_create", "carla", fund="ROADS", to="buildco", amount=5_000)["error"]
    world.policy(depts["Infrastructure"], 2, 3)
    out = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=500)
    assert out["requires_vote"] and Transfer.count() == 0
    assert not [c for c in fake_chain.state["calls"] if c[1] == "icrc1_transfer"]


def test_transfer_create_is_delegable():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "transfer.create")
    Delegation(id="d1", grantor="carla", delegate="claude-conn", status="active",
               scope={"operations": ["transfer.create"]}, expires_at=fake_chain.now() + 3600)
    ledger_ok()
    out = ok("transfer_create", "claude-conn", fund="ROADS", to="buildco", amount=10, on_behalf_of="carla")
    assert out["status"] == "completed"


def test_I_FIN_3_invoice_paid_only_when_ledger_shows_amount():
    setup_finance()
    inv = ok("invoice_create", "bob", payer="alice", amount=120, reason="User:alice", due_at=fake_chain.now() + 10)
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
    entries = {(e.kind, e.debit, e.credit) for e in Invoice[inv].ledger_entries}
    assert entries == {("asset", 120, 0), ("revenue", 0, 120)}
    assert "needs invoice.refresh" in denied("invoice_refresh", "alice")


def test_I_FIN_4_adopted_budgets_rules_and_policies_are_frozen():
    setup_finance()
    b = ok("budget_draft", "bob", fund="ROADS", period="FY2026", category="capital", planned=300_000)["budget"]
    ok("budget_propose", "bob", budget=b)
    assert "only a draft" in call("budget_propose", "bob", budget=b)["error"]
    new = ok("budget_amend", "bob", budget=b, planned=400_000)["budget"]
    assert (Budget[b].status, Budget[b].planned) == ("amended", 300_000)
    assert (Budget[new].status, Budget[new].planned) == ("adopted", 400_000)
    assert "only an adopted" in call("budget_amend", "bob", budget=b, planned=1)["error"]
    first = allocate(ROADS=30)
    second = allocate(ROADS=35)
    assert (AllocationRule[first].status, AllocationRule[second].status) == ("superseded", "adopted")
    assert "only a draft" in call("allocation_rule_propose", "bob", rule=first)["error"]


def test_governed_budget_is_adopted_by_its_proposal():
    depts = setup_finance()
    world.policy(depts["Infrastructure"], 1, 2)
    world.seat("infrastructure.director", "dana", depts["Infrastructure"], "org.manage_budget")
    out = ok("budget_draft", "dana", fund="ROADS", period="FY2026", category="capital", planned=1)
    assert out["requires_vote"] and Budget.count() == 0
    adopt(out["proposal"])
    p = ok("budget_propose", "dana", budget="B-1")["proposal"]
    assert Budget["B-1"].status == "draft"
    adopt(p)
    assert (Budget["B-1"].status, Budget["B-1"].proposal) == ("adopted", p)


def test_I_FIN_5_closed_period_accepts_no_entries():
    setup_finance()
    b = ok("budget_draft", "bob", fund="ROADS", period="FY2026", category="capital", planned=1)["budget"]
    ok("budget_propose", "bob", budget=b)
    allocate(ROADS=100)
    receive(1_000)
    first = ok("report_issue", "bob", period="FY2026")["report"]
    inv = ok("invoice_create", "bob", payer="alice", amount=5, reason="User:alice")["invoice"]
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
    receive(100)
    ledger_ok()
    ok("transfer_create", "ctl", fund="ROADS", to="bob", amount=10)
    transfers = [c for c in fake_chain.state["calls"] if c[1] == "icrc1_transfer"]
    assert transfers and all("from_subaccount = null" in c[2] for c in transfers)
    assert "transfer_from" not in inspect.getsource(treasury)


def test_transfer_revert_only_pending():
    setup_finance()
    allocate(ROADS=100)
    receive(100)
    fake_chain.state["replies"][(LEDGER, "icrc1_transfer")] = RuntimeError("canister stopped")
    with pytest.raises(fake_chain.Trap):
        call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=40)
    t = Transfer["TR-1"]
    assert t.status == "pending" and funds()["ROADS"] == 50
    assert "needs transfer.delete" in denied("transfer_revert", "alice", transfer=t.id)
    ok("transfer_revert", "bob", transfer=t.id)
    assert t.status == "failed" and funds()["ROADS"] == 100 and balanced()
    assert "only a pending" in call("transfer_revert", "bob", transfer=t.id)["error"]


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
