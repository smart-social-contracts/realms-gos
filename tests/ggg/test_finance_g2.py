"""The finance of GGG's G2: budgets by kind and proposed per period, extended budgets, loans, invoice items."""

import pytest

import fake_chain
import world
from core import op
from ggg import Budget, Department, External, FinancialReport, FiscalPeriod, Invoice, Loan, Treasury
from kit import call, denied, ok
from test_ratios import stored_as_version_1
from test_treasury import FY2026, allocate, balance_reply, draft, ledger_ok, one_item, propose, receive, setup_finance

TERM = {"count": 10, "unit": "year"}
FY2027 = {"period": "FY2027", "starts_at": FY2026["ends_at"], "ends_at": FY2026["ends_at"] + 100}


def open_next_period():
    ok("period_open", "bob", **FY2027)


def lines_of(period):
    return [(b.fund.id, b.category, b.kind, b.planned, b.actual, b.status) for b in FiscalPeriod[period].budgets]


def agora_budget():
    """FY2026 with a revenue line and two expense lines, adopted together; FY2027 open after it."""
    setup_finance()
    draft("ROOT", "property_tax", 900, kind="revenue")
    draft("ROOT", "salaries", 500)
    draft("ROADS", "capital", 300)
    propose()
    open_next_period()


# --- budgets by kind, proposed per period ---------------------------------------------------------

def test_a_budget_line_is_revenue_or_expense():
    setup_finance()
    line = {"fund": "ROOT", "period": "FY2026", "category": "x", "planned": 1}
    assert "kind" in denied("budget_draft", "bob", kind="capital", **line)
    assert "kind" in denied("budget_draft", "bob", **line)
    assert Budget.count() == 0
    assert Budget[draft("ROOT", "x", 1, kind="revenue")].kind == "revenue"


def test_budget_propose_adopts_every_draft_line_of_the_period_together():
    setup_finance()
    a, b = draft("ROOT", "property_tax", 900, kind="revenue"), draft("ROADS", "capital", 300)
    out = propose()
    assert (out["budgets"], out["revenue"], out["expense"]) == ([a, b], 900, 300)
    assert {Budget[a].status, Budget[b].status} == {"adopted"}
    assert "no draft budget lines" in call("budget_propose", "bob", period="FY2026")["error"]
    late = draft("ROADS", "upkeep", 5)  # a line drafted after the budget was adopted waits for the next proposal
    assert Budget[late].status == "draft"
    propose()
    assert Budget[late].status == "adopted" and Budget[a].status == "adopted"


def test_a_proposal_replaces_the_adopted_line_of_the_same_fund_and_category_and_keeps_its_spending():
    setup_finance()
    old = draft("ROADS", "capital", 300)
    propose()
    Budget[old].actual = 120
    new = draft("ROADS", "capital", 400)
    propose()
    assert (Budget[old].status, Budget[new].status, Budget[new].actual) == ("amended", "adopted", 120)


def test_the_totals_of_a_budget_are_derived_facts_of_the_call():
    setup_finance()
    draft("ROOT", "property_tax", 900, kind="revenue")
    draft("ROOT", "fees", 100, kind="revenue")
    draft("ROADS", "capital", 300)
    assert op.ops()["budget_propose"]["derived"]({"period": "FY2026"}) == {"revenue": 1_000, "expense": 300}
    assert op.ops()["transfer_create"]["derived"] is None


def test_a_budget_with_a_total_out_of_range_files_nothing():
    setup_finance()
    draft("ROOT", "property_tax", 2**53 - 1, kind="revenue")
    draft("ROOT", "fees", 1, kind="revenue")
    world.policy(Department["Administration"], 2, 3)
    assert "above the largest amount" in denied("budget_propose", "bob", period="FY2026")
    assert Budget.count() == 2 and not call("budget_propose", "bob", period="FY2026").get("proposal")


def test_a_budget_needs_a_period_with_drafts_and_at_least_a_year():
    setup_finance()
    assert "unknown FiscalPeriod" in denied("budget_propose", "bob", period="FY1999")
    assert "no draft budget lines" in denied("budget_propose", "bob", period="FY2026")
    draft("ROOT", "x", 1)
    assert "at least one year" in denied("budget_propose", "bob", period="FY2026", years=0)
    assert ok("budget_propose", "bob", period="FY2026", years=3)["budgets"] == ["B-1"]


def test_the_budget_of_a_period_is_the_realms_to_decide():
    setup_finance()
    world.policy(Department["Administration"], 2, 3)
    draft("ROADS", "capital", 300)
    p = propose()["proposal"]
    assert Budget["B-1"].status == "draft"
    for voter in world.PEOPLE:
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert (Budget["B-1"].status, Budget["B-1"].proposal) == ("adopted", p)


# --- extended budgets -----------------------------------------------------------------------------

def test_budget_extend_carries_the_adopted_lines_of_the_period_before():
    agora_budget()
    assert ok("budget_extend", "bob", period="FY2027")["extended"] == 3
    assert lines_of("FY2027") == [("ROOT", "property_tax", "revenue", 900, 0, "extended"),
                                  ("ROOT", "salaries", "expense", 500, 0, "extended"),
                                  ("ROADS", "capital", "expense", 300, 0, "extended")]
    assert {b.status for b in FiscalPeriod["FY2026"].budgets} == {"adopted"}


def test_budget_extend_is_refused_once_the_period_has_an_extended_or_adopted_line():
    agora_budget()
    ok("budget_extend", "bob", period="FY2027")
    again = call("budget_extend", "bob", period="FY2027")
    assert again["kind"] == "conflict" and "extended" in again["error"]
    assert len(FiscalPeriod["FY2027"].budgets) == 3
    ok("period_open", "bob", period="FY2028", starts_at=FY2027["ends_at"], ends_at=FY2027["ends_at"] + 100)
    draft("ROOT", "x", 1, period="FY2028")
    propose("FY2028")
    assert call("budget_extend", "bob", period="FY2028")["kind"] == "conflict"  # an adopted line


def test_draft_lines_stand_in_no_way_of_budget_extend():
    agora_budget()
    draft("ROOT", "salaries", 800, period="FY2027")
    assert ok("budget_extend", "bob", period="FY2027")["extended"] == 3
    assert sorted(b.status for b in FiscalPeriod["FY2027"].budgets) == ["draft", "extended", "extended", "extended"]


def test_budget_extend_needs_a_budget_to_carry_and_an_open_period():
    agora_budget()
    assert call("budget_extend", "bob", period="FY2026")["kind"] == "conflict"  # nothing before the first period
    ok("period_close", "bob", period="FY2027")
    assert call("budget_extend", "bob", period="FY2027")["kind"] == "conflict"
    assert "unknown FiscalPeriod" in denied("budget_extend", "bob", period="FY1999")


def test_budget_extend_carries_the_lines_of_a_period_that_has_been_closed_and_of_a_period_extended_itself():
    agora_budget()
    ok("period_close", "bob", period="FY2026")
    assert {b.status for b in FiscalPeriod["FY2026"].budgets} == {"closed"}
    assert ok("budget_extend", "bob", period="FY2027")["extended"] == 3
    ok("period_open", "bob", period="FY2028", starts_at=FY2027["ends_at"], ends_at=FY2027["ends_at"] + 100)
    assert ok("budget_extend", "bob", period="FY2028")["extended"] == 3  # a second year without a vote


def test_budget_extend_needs_the_budget_permission():
    agora_budget()
    assert "needs org.manage_budget" in denied("budget_extend", "alice", period="FY2027")


def test_an_extended_line_caps_spending_until_an_adopted_budget_replaces_it():
    agora_budget()
    allocate(ROADS=100)
    receive(10_000)
    ledger_ok()
    ok("budget_extend", "bob", period="FY2027")
    fake_chain.state["now"] = FY2027["starts_at"] + 1
    pay = lambda amount: call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=amount, category="capital")  # noqa: E731
    assert pay(300)["status"] == "completed"
    assert "budget" in pay(1)["error"]
    extended = next(b for b in FiscalPeriod["FY2027"].budgets if b.category == "capital")
    assert extended.actual == 300
    new = draft("ROADS", "capital", 500, period="FY2027")
    propose("FY2027")
    assert (extended.status, Budget[new].status, Budget[new].actual) == ("closed", "adopted", 300)
    assert pay(200)["status"] == "completed"


def test_the_budget_view_shows_the_extended_lines_in_force():
    agora_budget()
    ok("budget_extend", "bob", period="FY2027")
    seen = ok("budget_view", "mallory", period="FY2027")
    roads = next(f for f in seen["funds"] if f["id"] == "ROADS")
    assert [(b["category"], b["status"]) for b in roads["budgets"]] == [("capital", "extended")]


def test_a_budget_line_of_the_previous_release_has_a_kind_of_revenue_or_expense():
    setup_finance()
    expense, revenue = draft("ROOT", "a", 1), draft("ROOT", "b", 1, kind="revenue")
    stored_as_version_1(Budget, expense, kind="capital")
    stored_as_version_1(Budget, revenue)
    assert (Budget[expense].kind, Budget[revenue].kind) == ("expense", "revenue")


# --- loans ----------------------------------------------------------------------------------------

def setup_lending():
    """Agora's finance, and Bank A, an outside lender."""
    setup_finance()
    External(id="bank-a", name="Bank A", kind="lender", channel="manual")


def borrow(amount=500, **more):
    return ok("loan_create", "bob", lender="bank-a", amount=amount, term=TERM, rate=3_200_000, fund="ROADS", **more)


def test_story_a_loan_is_the_debt_of_the_realm_until_it_is_repaid():
    setup_lending()
    loan = borrow(500, facts={"purpose": "roads"})["loan"]
    row = Loan[loan]
    assert (row.lender, row.amount, row.symbol, row.rate, row.fund) == ("bank-a", 500, "REALM", 3_200_000, "ROADS")
    assert (row.term, row.outstanding, row.status, row.facts) == (TERM, 500, "active", {"purpose": "roads"})
    assert Treasury["1"].debt_outstanding == 500
    assert ok("loan_repay", "bob", loan=loan, amount=80, reason="March")["loan"] == loan
    assert (row.outstanding, row.status, Treasury["1"].debt_outstanding) == (420, "active", 420)
    assert "420 outstanding" in denied("loan_repay", "bob", loan=loan, amount=421)
    ok("loan_repay", "bob", loan=loan, amount=420)
    assert (row.outstanding, row.status, Treasury["1"].debt_outstanding) == (0, "repaid", 0)
    assert call("loan_repay", "bob", loan=loan, amount=1)["kind"] == "conflict"


def test_the_debt_of_the_realm_is_the_sum_of_its_active_loans():
    setup_lending()
    first, second = borrow(500)["loan"], borrow(200)["loan"]
    ok("loan_repay", "bob", loan=first, amount=100)
    ok("loan_repay", "bob", loan=second, amount=200)
    assert Treasury["1"].debt_outstanding == sum(x.outstanding for x in (Loan[first], Loan[second])) == 400


def test_a_loan_is_a_record_of_money_that_moves_outside_the_realm():
    setup_lending()
    receive(100)
    held = ok("treasury_view", "bob")["funds"]
    borrow(500)
    assert ok("treasury_view", "bob")["funds"] == held


def test_loan_list_pages_the_loans_and_filters_by_status():
    setup_lending()
    loans = [borrow(10 * n)["loan"] for n in (1, 2, 3)]
    ok("loan_repay", "bob", loan=loans[1], amount=20)
    first = ok("loan_list", "bob", count=2)
    assert [x["id"] for x in first["loans"]] == loans[:2] and first["next"] == 3
    assert [x["id"] for x in ok("loan_list", "bob", count=2, **{"from": first["next"]})["loans"]] == loans[2:]
    assert [x["id"] for x in ok("loan_list", "bob", status="repaid")["loans"]] == [loans[1]]
    assert [x["id"] for x in ok("loan_list", "bob", status="active")["loans"]] == [loans[0], loans[2]]


def test_a_loan_is_checked_before_it_is_recorded():
    setup_lending()
    base = {"lender": "bank-a", "amount": 500, "term": TERM, "rate": 1, "fund": "ROADS"}
    for change in ({"amount": 0}, {"fund": "NOWHERE"}, {"term": {"count": 3, "unit": "fortnight"}},
                   {"term": {"count": "3", "unit": "year"}}, {"term": 3}):
        assert not call("loan_create", "bob", **{**base, **change})["ok"], change
    assert Loan.count() == 0 and Treasury["1"].debt_outstanding == 0


def test_a_loan_is_from_an_outside_body_the_realm_knows():
    setup_lending()
    out = call("loan_create", "bob", lender="bank-b", amount=500, term=TERM, rate=1, fund="ROADS")
    assert out["kind"] == "not_found" and "bank-b" in out["error"]
    assert Loan.count() == 0 and Treasury["1"].debt_outstanding == 0


def test_borrowing_is_the_realms_to_decide():
    setup_lending()
    world.policy(Department["Administration"], 2, 3)
    p = borrow()["proposal"]
    assert Loan.count() == 0
    for voter in world.PEOPLE:
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Loan.count() == 1 and Treasury["1"].debt_outstanding == 500


def test_loan_operations_need_their_permissions():
    setup_lending()
    loan = borrow()["loan"]
    assert "needs org.manage_budget" in denied("loan_create", "alice", lender="b", amount=1, term=TERM, rate=1, fund="ROOT")
    assert "needs org.manage_budget" in denied("loan_repay", "alice", loan=loan, amount=1)
    assert "needs treasury.view" in denied("loan_list", "alice")


# --- invoice items --------------------------------------------------------------------------------

def bill(items, amount, **more):
    return call("invoice_create", "bob", payer="alice", amount=amount, items=items, **more)


WATER = [{"concept": "property_tax", "amount": 150, "legislation": "Art. 71"},
         {"concept": "large_family_bonus", "amount": -30, "quantity": 1, "unit": "family"}]


def test_an_invoice_has_items_that_sum_to_its_amount():
    setup_finance()
    out = ok("invoice_create", "bob", payer="alice", amount=120, items=WATER, facts={"parcel": "L-17"})
    inv = Invoice[out["invoice"]]
    assert (inv.amount, inv.items, inv.facts) == (120, WATER, {"parcel": "L-17"})
    assert [i["amount"] for i in ok("my_invoices", "alice")["invoices"]] == [120]
    assert ok("my_invoices", "alice")["invoices"][0]["items"] == WATER


@pytest.mark.parametrize("items, amount", [([], 5), (None, 5), (one_item(5), 6), ("fee", 5),
                                           ([{"concept": "x", "amount": 5, "tip": 1}], 5),
                                           ([{"amount": 5}], 5), ([{"concept": "x", "amount": 5.5}], 5),
                                           ([{"concept": "x", "amount": 5, "quantity": -1}], 5)])
def test_an_invoice_whose_items_do_not_make_up_its_amount_is_refused(items, amount):
    setup_finance()
    out = bill(items, amount)
    assert not out["ok"] and out["kind"] == "invalid_argument", out
    assert Invoice.count() == 0


def test_invoice_item_add_adds_a_line_to_a_pending_invoice_and_moves_its_amount():
    setup_finance()
    inv = ok("invoice_create", "bob", payer="alice", amount=120, items=WATER)["invoice"]
    sanitation = {"concept": "sanitation", "amount": 40, "legislation": "Ordinance 3"}
    out = ok("invoice_item_add", "bob", invoice=inv, item=sanitation)
    assert out["invoice"] == inv and Invoice[inv].amount == 160 and Invoice[inv].items == WATER + [sanitation]
    ok("invoice_item_add", "bob", invoice=inv, item={"concept": "discount", "amount": -160})
    assert Invoice[inv].amount == 0
    before = Invoice[inv].items
    assert "not an amount of money" in denied("invoice_item_add", "bob", invoice=inv, item={"concept": "x", "amount": -1})
    assert Invoice[inv].items == before and Invoice[inv].amount == 0


def test_a_line_joins_only_a_pending_invoice():
    setup_finance()
    paid = ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5), reason="User:alice")["invoice"]
    allocate(ROADS=100)
    balance_reply(5)
    assert ok("invoice_check", "alice", invoice=paid)["status"] == "paid"
    overdue = ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5), due_at=fake_chain.now() + 1)["invoice"]
    Invoice[overdue].status = "overdue"
    for inv in (paid, overdue):
        out = call("invoice_item_add", "bob", invoice=inv, item={"concept": "late fee", "amount": 1})
        assert out["kind"] == "conflict", out
        assert Invoice[inv].amount == 5 and len(Invoice[inv].items) == 1
    assert "unknown Invoice" in denied("invoice_item_add", "bob", invoice="INV-99", item={"concept": "x", "amount": 1})


def test_invoice_item_add_needs_the_treasury_permission():
    setup_finance()
    inv = ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5))["invoice"]
    assert "needs treasury.manage" in denied("invoice_item_add", "alice", invoice=inv, item={"concept": "x", "amount": 1})


def test_an_invoice_of_the_previous_release_has_one_item_for_its_whole_amount():
    setup_finance()
    inv = ok("invoice_create", "bob", payer="alice", amount=5, items=one_item(5), reason="TaxDeclaration:TD-1")["invoice"]
    stored_as_version_1(Invoice, inv, drop=("items", "facts"))
    row = Invoice[inv]
    assert row.items == [{"concept": "taxdeclaration", "amount": 5}] and row.facts == {}
    plain = ok("invoice_create", "bob", payer="alice", amount=7, items=one_item(7))["invoice"]
    stored_as_version_1(Invoice, plain, drop=("items", "facts"))
    assert Invoice[plain].items == [{"concept": "invoice", "amount": 7}]
    stored_as_version_1(Invoice, inv)  # converted already: not converted again
    assert Invoice[inv].items == [{"concept": "taxdeclaration", "amount": 5}]


# --- reports --------------------------------------------------------------------------------------

def test_a_report_may_carry_the_adjustments_the_issuer_adds_to_the_ledger():
    setup_finance()
    adjustments = {"deferred_revenue": 1_200, "notes": {"pensions": 40}}
    plain = ok("report_issue", "bob", period="FY2026")["report"]
    assert "adjustments" not in FinancialReport[plain].statements
    adjusted = ok("report_issue", "bob", period="FY2026", adjustments=adjustments)["report"]
    assert FinancialReport[adjusted].statements["adjustments"] == adjustments
    assert FinancialReport[adjusted].source_hash == FinancialReport[plain].source_hash  # the hash is the ledger's
    assert not call("report_issue", "bob", period="FY2026", adjustments=[1, 2])["ok"]
