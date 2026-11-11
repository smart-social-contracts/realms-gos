"""How spending moves through its stages (ggg I-FIN-9, I-FIN-10, I-FIN-11): a budget line's reserved and committed money,
transfers charged to it from its committed money or its available credit, scheduled transfers, and the payments on a
contract."""

import fake_chain
from api import treasury
from core.db import each
from ggg import Budget, Department, Fund, FiscalPeriod, LedgerEntry, Proposal, Transfer
from ic_python_db import Database
from kit import call, fire_timers, later, ok
from test_ratios import stored_as_version_1
from test_treasury import LEDGER, draft, finish, fund_roads_with_budget, ledger_ok, propose, start, suspended

DAY = 86_400
ICRC1 = (LEDGER, "icrc1_transfer")
TOO_OLD = "(variant { Err = variant { TooOld } })"


def figures(budget):
    return Budget[budget].reserved, Budget[budget].committed, Budget[budget].actual


def stage(budget, reserve=0, commit=0):
    return call("budget_stage", "bob", budget=budget, reserve=reserve, commit=commit)


def pay(amount, **args):
    return call("transfer_create", "ctl", fund="ROADS", to="buildco", amount=amount, category="capital", **args)


def staged_line(planned=1_000):
    """ROADS with an adopted capital line of ``planned``."""
    return fund_roads_with_budget(planned)


# --- the stages of a line -------------------------------------------------------------------------

def test_a_lines_money_is_reserved_then_committed_never_past_the_line_and_never_below_nothing():
    b = staged_line()
    assert ok("budget_stage", "bob", budget=b, reserve=400, commit=0)["budget"] == b
    ok("budget_stage", "bob", budget=b, reserve=-300, commit=300)
    assert figures(b) == (100, 300, 0)

    assert "B-1 (ROADS, FY2026, capital) plans 1000; this stage would take it 100 above" in stage(b, 700)["error"]
    assert stage(b, -200)["kind"] == "conflict"
    assert stage(b, 0, 0)["kind"] == "invalid_argument"
    assert stage("B-404", 1)["kind"] == "not_found"
    assert figures(b) == (100, 300, 0)


def test_only_an_adopted_expense_line_holds_reserved_money():
    setup = staged_line()
    revenue = draft("ROOT", "property_tax", 900, kind="revenue")
    assert "not an adopted expense line" in stage(revenue, 1)["error"]  # a draft, and a revenue line
    propose()
    assert "not an adopted expense line" in stage(revenue, 1)["error"]
    assert figures(setup) == (0, 0, 0)


def test_budget_list_shows_each_line_with_its_stages():
    b = staged_line(1_000)
    ok("budget_stage", "bob", budget=b, reserve=100, commit=50)
    line = ok("budget_list", "bob", fund="ROADS", period="FY2026")["budgets"][0]
    assert (line["id"], line["fund"], line["period"], line["planned"], line["reserved"], line["committed"],
            line["actual"]) == (b, "ROADS", "FY2026", 1_000, 100, 50, 0)
    assert ok("budget_list", "bob", fund="ROOT")["budgets"] == []


def test_a_budget_line_of_the_release_before_holds_nothing_reserved_or_committed():
    b = staged_line()
    stored_as_version_1(Budget, b, drop=("reserved", "committed"), __version__=2)  # the version the lines had before
    assert figures(b) == (0, 0, 0)
    ok("budget_stage", "bob", budget=b, reserve=10)
    assert figures(b) == (10, 0, 0)


# --- charging a transfer to its line --------------------------------------------------------------

def test_a_transfer_is_charged_to_its_line_from_its_committed_money_or_its_credit_and_refused_when_short():
    b = staged_line()
    ok("budget_stage", "bob", budget=b, reserve=100, commit=300)  # 600 available

    assert pay(500)["status"] == "completed"
    short = pay(150)
    assert short["kind"] == "conflict" and f"{b} (ROADS, FY2026, capital) has 100 available; the transfer of 150 is 50 short" in short["error"]
    assert pay(250, committed=True)["status"] == "completed"
    assert "has 50 committed; the transfer of 100 is 50 short" in pay(100, committed=True)["error"]
    assert figures(b) == (100, 50, 750)
    assert [(t.budget, t.committed) for t in Transfer.load_some(1, 2)] == [(b, False), (b, True)]


def test_a_failed_transfer_gives_back_what_it_charged_to_the_stage_it_came_from():
    b = staged_line()
    ok("budget_stage", "bob", budget=b, commit=300)
    fake_chain.state["replies"][("realm-ledger", "icrc1_transfer")] = "(variant { Err = variant { TooOld } })"

    assert pay(200, committed=True)["status"] == "failed"
    assert figures(b) == (0, 300, 0)
    assert pay(200)["status"] == "failed"
    assert figures(b) == (0, 300, 0)


def test_a_fund_with_no_adopted_budget_charges_no_line_and_records_committed_false():
    from test_treasury import allocate, receive, setup_finance

    setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    ledger_ok()
    assert ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, committed=True)["status"] == "completed"
    t = Transfer["TR-1"]
    assert (t.budget, t.committed) == ("", False)


# --- scheduled transfers --------------------------------------------------------------------------

def test_a_scheduled_transfer_is_owed_until_it_is_paid_and_cancelling_it_gives_its_money_back():
    b = staged_line()
    ok("budget_stage", "bob", budget=b, reserve=100, commit=50)
    due = fake_chain.state["now"] + 30 * DAY
    assert pay(10, due_at=fake_chain.state["now"] - DAY)["kind"] == "invalid_argument"

    cancelled = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=30, category="capital", committed=True,
                   due_at=due)["transfer"]
    assert figures(b) == (100, 20, 30), "charged when the obligation is recognised"
    assert not [c for c in fake_chain.state["calls"] if c[1] == "icrc1_transfer"], "no money moves"
    owed = ok("transfer_list", "bob", fund="ROADS", status="scheduled")["transfers"]
    assert [(t["id"], t["due_at"], t["committed"]) for t in owed] == [(cancelled, due, True)]

    assert ok("transfer_revert", "ctl", transfer=cancelled)["status"] == "cancelled"
    assert figures(b) == (100, 50, 0), "back to the committed money it came from"
    assert call("transfer_pay", "ctl", transfer=cancelled)["kind"] == "conflict"

    paid = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, category="capital", due_at=due)["transfer"]
    assert figures(b) == (100, 50, 100)
    assert ok("transfer_pay", "ctl", transfer=paid)["status"] == "completed"
    assert figures(b) == (100, 50, 100), "paying moves no budget money"
    assert call("transfer_pay", "ctl", transfer=paid)["kind"] == "conflict"
    assert call("transfer_revert", "ctl", transfer=paid)["kind"] == "conflict"
    assert call("transfer_pay", "ctl", transfer="TR-404")["kind"] == "not_found"


def test_a_scheduled_transfer_cancelled_in_a_later_period_gives_back_to_the_line_it_charged():
    from test_finance_g2 import FY2027

    b = staged_line()
    ok("period_open", "bob", **FY2027)
    ok("budget_stage", "bob", budget=b, commit=100)
    t = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, category="capital", committed=True,
           due_at=FY2027["starts_at"] + DAY)["transfer"]
    assert figures(b) == (0, 0, 100)

    fake_chain.state["now"] = FY2027["starts_at"] + 1
    ok("budget_extend", "bob", period="FY2027")
    ok("transfer_revert", "ctl", transfer=t)
    assert figures(b) == (0, 100, 0)


def test_transfer_list_pages_the_realms_transfers_by_fund_and_status():
    staged_line()
    ledger_ok()
    paid, due = pay(10)["transfer"], pay(20, due_at=fake_chain.state["now"] + DAY)["transfer"]
    assert [t["id"] for t in ok("transfer_list", "bob")["transfers"]] == [paid, due]
    assert [t["id"] for t in ok("transfer_list", "bob", status="scheduled")["transfers"]] == [due]
    assert ok("transfer_list", "bob", fund="ROOT")["transfers"] == []


# --- a line that replaces another -----------------------------------------------------------------

def test_a_line_that_replaces_another_carries_its_money_and_is_refused_when_it_plans_less():
    b = staged_line(1_000)
    ok("budget_stage", "bob", budget=b, reserve=100, commit=50)
    assert pay(850)["status"] == "completed"

    draft("ROADS", "capital", 1_500)
    propose()
    replacing = Budget["B-2"]
    assert (replacing.status, replacing.planned, figures("B-2")) == ("adopted", 1_500, (100, 50, 850))
    assert Budget[b].status == "amended"

    draft("ROADS", "capital", 900)
    refused = call("budget_propose", "bob", period="FY2026")
    assert refused["kind"] == "conflict" and "line B-3 plans 900, less than the 1000 that B-2 holds" in refused["error"]
    assert Budget["B-2"].status == "adopted" and Budget["B-3"].status == "draft"

    amended = call("budget_amend", "bob", budget="B-2", planned=999)
    assert amended["kind"] == "conflict" and "less than the 1000 that B-2 holds" in amended["error"]
    assert ok("budget_amend", "bob", budget="B-2", planned=1_000)["amends"] == "B-2"


# --- the payments on a contract -------------------------------------------------------------------

def contract(value=500, symbol="REALM"):
    return ok("contract_create", "ctl", title="Bike lane", value=value, symbol=symbol)["contract"]


def test_a_transfer_for_a_contract_is_found_in_its_symbol_and_within_its_value():
    staged_line(10_000)
    k = contract(500)
    assert pay(100, contract="C-404")["kind"] == "not_found"

    other = contract(500, symbol="OTHER")
    assert pay(100, contract=other)["kind"] == "invalid_argument"

    assert pay(300, contract=k)["status"] == "completed"
    over = pay(250, contract=k)
    assert over["kind"] == "conflict" and f"contract {k} has 200 left of its value; the transfer of 250 is 50 over" in over["error"]
    assert pay(200, contract=k)["status"] == "completed"
    assert Transfer.paid_on(k) == 500


def test_a_scheduled_transfer_counts_against_its_contract_and_a_cancelled_one_does_not():
    staged_line(10_000)
    k = contract(500)
    due = fake_chain.state["now"] + DAY
    owed = ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=400, category="capital", contract=k,
              due_at=due)["transfer"]
    assert "100 left" in pay(150, contract=k)["error"]
    ok("transfer_revert", "ctl", transfer=owed)
    assert pay(500, contract=k)["status"] == "completed"


def test_two_voted_payments_that_together_overpay_a_contract_cannot_both_execute():
    from test_treasury import adopt
    import world

    staged = staged_line(10_000)
    infrastructure = Department["Infrastructure"]
    world.grant(infrastructure, "transfer.create")
    world.policy(infrastructure, 1, 2)
    k = contract(500)
    first = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=300, category="capital", contract=k)["proposal"]
    second = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=300, category="capital", contract=k)["proposal"]
    assert adopt(first)["status"] == "completed"
    assert ok("proposal_vote", "carla", proposal=second, choice="yes")["status"] == "failed"
    assert Transfer.paid_on(k) == 300 and figures(staged)[2] == 300


# --- transfers of the release before --------------------------------------------------------------

def stored_as_the_release_before(transfer_id, contract):
    """Save the transfer as the last release did: no contract on the row, and no entry in the index that finds it by contract."""
    row = Transfer[transfer_id]
    Database.get_instance().field_index_remove("Transfer", "contract", contract, row._id)
    stored_as_version_1(Transfer, transfer_id, drop=("contract",))


def test_the_payments_on_a_contract_include_the_transfers_of_the_release_before_once_the_upgrade_has_read_them():
    staged_line(10_000)
    k = contract(500)
    assert pay(400, contract=k)["status"] == "completed"
    stored_as_the_release_before("TR-1", k)
    assert Transfer.paid_on(k) == 0  # until the pass after the upgrade has read the row

    treasury.resume()
    fire_timers()  # nothing has read the old row but the pass
    assert Transfer.paid_on(k) == 400
    over = pay(400, contract=k)
    assert over["kind"] == "conflict" and "has 100 left of its value" in over["error"]
    assert Database.get_instance().load("Transfer", Transfer["TR-1"]._id)["__version__"] == 2


def test_the_pass_after_an_upgrade_reads_every_page_of_transfers(monkeypatch):
    staged_line(10_000)
    k = contract(500)
    for _ in range(3):
        assert pay(100, contract=k)["status"] == "completed"
    for n in (1, 2, 3):
        stored_as_the_release_before(f"TR-{n}", k)
    monkeypatch.setattr(treasury, "MIGRATE_PAGE", 2)

    treasury.resume()
    assert len(fake_chain.state["timers"]) == 1 and Transfer.paid_on(k) == 200
    fire_timers()
    assert Transfer.paid_on(k) == 300


def test_the_pass_after_an_upgrade_indexes_the_status_of_a_pending_transfer_of_the_release_before():
    staged_line()
    fake_chain.state["replies"][ICRC1] = Exception("the ledger is down")
    pending = pay(100)["transfer"]
    row = Transfer[pending]
    Database.get_instance().field_index_remove("Transfer", "status", "pending", row._id)  # not in the index of its release
    stored_as_version_1(Transfer, pending, drop=("contract",))
    assert not list(each(Transfer, "status", "pending"))

    treasury.resume()
    fire_timers()
    refused = call("period_close", "bob", period="FY2026")
    assert refused["kind"] == "conflict" and pending in refused["error"]


def test_an_old_transfer_that_is_read_before_the_pass_takes_its_contract_from_its_ledger_entries():
    staged_line(10_000)
    k = contract(500)
    assert pay(200, contract=k)["status"] == "completed"
    entry = next(e for e in LedgerEntry.load_some(1, LedgerEntry.max_id()) if e.transfer)
    assert entry.contract.id == k  # the release before recorded the contract here only
    stored_as_the_release_before("TR-1", k)

    assert Transfer["TR-1"].contract == k  # read by anything else, the row migrates as well
    assert Transfer.paid_on(k) == 200


# --- what is owed or in flight keeps its period open ----------------------------------------------

def schedule(amount=100, **args):
    return ok("transfer_create", "ctl", fund="ROADS", to="buildco", amount=amount, category="capital",
              due_at=fake_chain.state["now"] + DAY, **args)["transfer"]


def test_a_period_does_not_close_while_it_holds_a_scheduled_transfer():
    staged_line()
    owed = schedule()
    refused = call("period_close", "bob", period="FY2026")
    assert refused["kind"] == "conflict" and f"FY2026 holds 1 transfers still scheduled, sending or pending, among them {owed}" in refused["error"]
    assert FiscalPeriod["FY2026"].status == "open"

    ok("transfer_pay", "ctl", transfer=owed)
    assert ok("period_close", "bob", period="FY2026")["status"] == "closed"


def test_a_period_closes_once_its_scheduled_transfer_is_cancelled():
    staged_line()
    owed = schedule()
    ok("transfer_revert", "ctl", transfer=owed)
    assert ok("period_close", "bob", period="FY2026")["status"] == "closed"


def test_a_period_does_not_close_while_a_rejected_transfer_waits_to_be_reverted():
    staged_line()
    fake_chain.state["replies"][ICRC1] = Exception("the ledger is down")
    pending = pay(100)["transfer"]
    assert Transfer[pending].status == "pending"
    assert call("period_close", "bob", period="FY2026")["kind"] == "conflict"

    ok("transfer_revert", "ctl", transfer=pending)
    assert ok("period_close", "bob", period="FY2026")["status"] == "closed"


def test_a_period_does_not_close_while_a_transfer_is_in_flight(monkeypatch):
    staged_line()
    monkeypatch.setattr(fake_chain, "call", suspended)
    message = start("transfer_create", "ctl", fund="ROADS", to="buildco", amount=100, category="capital")
    assert call("period_close", "bob", period="FY2026")["kind"] == "conflict"
    finish(message, "(variant { Ok = 9 : nat })")
    assert ok("period_close", "bob", period="FY2026")["status"] == "closed"


# --- a transfer whose call has been made ends in a final state ------------------------------------

def close_the_period_under(transfer_id):
    """A period that has closed with the transfer open in it: a row the rule above would not have let through."""
    next(iter(Transfer[transfer_id].ledger_entries)).period.status = "closed"


def test_a_refusing_ledger_fails_a_scheduled_transfer_paid_after_its_period_closed_and_refunds_the_fund():
    b = staged_line()
    ok("budget_stage", "bob", budget=b, commit=100)
    owed = schedule(100, committed=True)
    fund_before = treasury.balance(Fund["ROADS"], "REALM")
    close_the_period_under(owed)
    fake_chain.state["replies"][ICRC1] = TOO_OLD

    out = ok("transfer_pay", "ctl", transfer=owed)
    assert out["status"] == "failed" and Transfer[owed].status == "failed"
    assert treasury.balance(Fund["ROADS"], "REALM") == fund_before + 100 + 10  # the amount and the fee come back


def test_a_scheduled_transfer_can_be_cancelled_after_its_period_closed():
    staged_line()
    owed = schedule(100)
    close_the_period_under(owed)
    assert ok("transfer_revert", "ctl", transfer=owed)["status"] == "cancelled"


def test_a_ledger_that_refuses_a_scheduled_transfer_fails_it_and_gives_back_what_it_charged():
    b = staged_line()
    ok("budget_stage", "bob", budget=b, commit=100)
    owed = schedule(100, committed=True)
    assert figures(b) == (0, 0, 100)
    fake_chain.state["replies"][ICRC1] = TOO_OLD

    assert ok("transfer_pay", "ctl", transfer=owed)["status"] == "failed"
    assert figures(b) == (0, 100, 0)


# --- a decision of the model -----------------------------------------------------------------------

def test_a_voted_scheduled_transfer_whose_due_date_passes_during_the_vote_fails_when_it_applies():
    """ggg I-FIN-10 states it literally: ``due_at`` must lie after the call that applies it."""
    import world

    staged_line(10_000)
    infrastructure = Department["Infrastructure"]
    world.grant(infrastructure, "transfer.create")
    world.policy(infrastructure, 1, 2)
    p = ok("transfer_create", "carla", fund="ROADS", to="buildco", amount=100, category="capital",
           due_at=fake_chain.state["now"] + DAY)["proposal"]

    later(2 * DAY)
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "failed"
    assert "due_at must lie after the call" in Proposal[p].result["error"] and Transfer.count() == 0
