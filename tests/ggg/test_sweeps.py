"""Money paid in a quarter reaches the capital's funds (ggg D36, I-FED-5 to I-FED-7): the capital's rules, the quarter's
sweeps, their ledger transfers, and the capital's books."""

import pytest

import fake_chain
from api import quarters, sweeps, treasury
from ggg import FiscalPeriod, LedgerEntry, Quarter, Realm, Sweep
from kit import call, direct, fire_timers, later, ok
from test_quarters import CAPITAL
from test_treasury import LEDGER, finish, ledger_ok, receive, setup_finance, start, suspended

RULE = {"fund": "ROOT", "symbol": "REALM", "into": "ROADS", "keep": 100}
TO = {"owner": CAPITAL, "subaccount": None}
EPOCH = treasury.EPOCHS["monthly"]
DUPLICATE = "(variant { Err = variant { Duplicate = record { duplicate_of = 77 : nat } } })"
TOO_OLD = "(variant { Err = variant { TooOld } })"
REFUSED = "(variant { Err = variant { InsufficientFunds = record { balance = 3 : nat } } })"
ICRC1 = (LEDGER, "icrc1_transfer")


@pytest.fixture(autouse=True)
def timer_not_armed():
    sweeps._timer["armed"] = False


def transfers():
    """The arguments of every transfer the quarter sent to the ledger."""
    return [c[2] for c in fake_chain.state["calls"] if c[:2] == ICRC1]


def held():
    return treasury.balance(treasury.Fund["ROOT"], "REALM")


def sync_reply(*rules):
    directory = [{"id": i, "name": i, "index": n, "status": "active", "population": 0, "codex_id": "",
                  "codex_version": "", "last_sync": 0} for n, i in enumerate((CAPITAL, "north"))]
    return {"ok": True, "directory": directory, "codex": {"id": "", "version": ""}, "index": 1, "zones": [],
            "sweep": {"to": TO, "rules": list(rules)}}


def north(monkeypatch, balance=1_000, rules=(RULE,)):
    """North, a quarter of Agora holding ``balance`` in ROOT, whose capital sends ``rules`` with quarter_sync and books
    what is reported to it."""
    monkeypatch.setattr(fake_chain, "SELF", "north")
    setup_finance()
    receive(balance)
    realm = Realm["1"]
    realm.is_quarter, realm.is_capital, realm.federation_realm_id = True, False, CAPITAL
    Quarter(id=CAPITAL, name="Capital", index=0, status="active")
    return fake_chain.peer(CAPITAL, quarter_sync=sync_reply(*rules), quarter_sweep_report={"ok": True})


def sweep_now(**args):
    out = ok("quarter_sweep", "bob", **args)
    return [Sweep[s] for s in out["sweeps"]]


# --- the capital's rules --------------------------------------------------------------------------

def capital_with_a_quarter():
    setup_finance()
    ok("quarter_register", "ctl", quarter="north", name="North")
    ok("quarter_report", "north", population=0)


def test_only_a_capital_sets_sweep_rules():
    setup_finance()
    out = call("treasury_configure", "bob", config={"sweep": [RULE]})
    assert out["kind"] == "invalid_argument" and "only a capital" in out["error"]
    assert "sweep" not in (treasury.Treasury["1"].config if treasury.Treasury["1"] else {})


def test_a_capital_names_its_own_funds_and_tokens_and_each_pair_once():
    capital_with_a_quarter()
    assert call("treasury_configure", "bob", config={"sweep": [{**RULE, "into": "NOWHERE"}]})["kind"] == "not_found"
    assert call("treasury_configure", "bob", config={"sweep": [{**RULE, "symbol": "NOPE"}]})["kind"] == "not_found"
    twice = call("treasury_configure", "bob", config={"sweep": [RULE, {**RULE, "into": "ROOT"}]})
    assert twice["kind"] == "invalid_argument" and "two sweep rules for ROOT in REALM" in twice["error"]
    assert direct("treasury_configure", "bob", config={"sweep": [{"fund": "ROOT"}]})["kind"] == "invalid_argument"

    ok("treasury_configure", "bob", config={"sweep": [RULE, {**RULE, "fund": "ROADS", "keep": 0}]})
    assert len(treasury.Treasury["1"].config["sweep"]) == 2
    ok("treasury_configure", "bob", config={"sweep": [RULE]})  # the rules given replace the earlier ones
    assert treasury.Treasury["1"].config["sweep"] == [RULE]
    ok("treasury_configure", "bob", config={"epoch": "yearly"})  # another setting leaves them as they are
    assert treasury.Treasury["1"].config["sweep"] == [RULE]


def test_quarter_sync_carries_the_rules_and_the_capitals_account_to_a_quarter():
    capital_with_a_quarter()
    ok("treasury_configure", "bob", config={"sweep": [RULE]})
    out = ok("quarter_sync", "north", population=0)
    assert out["sweep"] == {"to": {"owner": fake_chain.SELF, "subaccount": None}, "rules": [RULE]}


# --- the capital books a sweep --------------------------------------------------------------------

def reported(**over):
    return {"id": "SW-1", "quarter": "north", "fund": "ROOT", "symbol": "REALM", "into": "ROADS", "to": TO,
            "epoch": 1_787_011_200, "amount": 890, "fee": 10, "created_at": 1_790_000_000, "block": 77, "status": "sent",
            "reason": None, "reported_at": None, **over}


def test_the_capital_books_a_reported_sweep_into_its_fund_once():
    capital_with_a_quarter()
    before = treasury.balance(treasury.Fund["ROADS"], "REALM")
    booked = ok("quarter_sweep_report", "north", **reported())["sweep"]
    assert booked == "north/SW-1"
    row = Sweep[booked]
    assert (row.status, row.block, row.quarter, row.amount) == ("booked", "77", "north", 890)
    assert treasury.balance(treasury.Fund["ROADS"], "REALM") == before + 890
    entries = [(e.kind, e.category, e.debit, e.credit) for e in LedgerEntry.load_some(1, LedgerEntry.max_id()) if e.sweep]
    assert entries == [("asset", "cash", 890, 0), ("equity", "sweep", 0, 890)]

    assert ok("quarter_sweep_report", "north", **reported())["sweep"] == booked  # again: the record, nothing booked twice
    assert treasury.balance(treasury.Fund["ROADS"], "REALM") == before + 890 and Sweep.count() == 1
    other = call("quarter_sweep_report", "north", **reported(amount=891))
    assert other["kind"] == "conflict" and "booked with other figures" in other["error"]


def test_the_capital_takes_a_report_only_from_a_registered_quarter_for_a_sent_sweep_with_a_block():
    capital_with_a_quarter()
    assert call("quarter_sweep_report", "harbor", **reported())["kind"] == "forbidden"
    assert call("quarter_sweep_report", "north", **reported(status="booked"))["kind"] == "invalid_argument"
    assert call("quarter_sweep_report", "north", **{**reported(), "block": None})["kind"] == "invalid_argument"
    assert call("quarter_sweep_report", "north", **reported(into="NOWHERE"))["kind"] == "not_found"
    assert Sweep.count() == 0


def test_sweep_list_reads_the_capitals_booked_sweeps_by_quarter_and_status():
    capital_with_a_quarter()
    ok("quarter_sweep_report", "north", **reported())
    assert [s["id"] for s in ok("sweep_list", "bob")["sweeps"]] == ["north/SW-1"]
    assert [s["id"] for s in ok("sweep_list", "bob", quarter="north", status="booked")["sweeps"]] == ["north/SW-1"]
    assert ok("sweep_list", "bob", quarter="south")["sweeps"] == []
    assert ok("sweep_list", "bob", status="failed")["sweeps"] == []


# --- a quarter sweeps -----------------------------------------------------------------------------

def test_only_a_quarter_sweeps():
    setup_finance()
    assert call("quarter_sweep", "bob")["kind"] == "conflict"


def test_a_quarter_moves_what_its_fund_holds_above_what_it_keeps_and_the_fee_and_the_capital_books_it(monkeypatch):
    capital = north(monkeypatch)
    ledger_ok(90_113)
    (s,) = sweep_now()

    assert (s.id, s.status, s.block, s.amount, s.fee, s.fund, s.into) == ("SW-1", "booked", "90113", 890, 10, "ROOT", "ROADS")
    assert s.epoch == fake_chain.state["now"] - fake_chain.state["now"] % EPOCH and s.created_at == fake_chain.state["now"]
    (arg,) = transfers()
    assert f'principal "{CAPITAL}"' in arg and "amount = 890 : nat" in arg and "fee = opt (10 : nat)" in arg
    assert 'memo = opt blob "\\53\\57\\2d\\31"' in arg  # the sweep's id, SW-1
    assert f"created_at_time = opt ({s.created_at * 1_000_000_000} : nat64)" in arg
    assert held() == 100  # what it keeps
    entries = [(e.kind, e.category, e.debit, e.credit) for e in LedgerEntry.load_some(1, LedgerEntry.max_id()) if e.sweep]
    assert entries == [("asset", "cash", 0, 900), ("equity", "sweep", 890, 0), ("expense", "ledger_fee", 10, 0)]
    (told,) = capital.sent("quarter_sweep_report")
    assert (told["id"], told["block"], told["status"], told["amount"]) == ("SW-1", 90_113, "sent", 890)

    assert sweep_now() == []  # one sweep a rule and treasury epoch
    assert len(transfers()) == 1


def test_a_sweep_is_booked_in_no_period_when_the_period_that_covers_now_has_closed(monkeypatch):
    north(monkeypatch)
    ledger_ok()
    FiscalPeriod["FY2026"].status = "closed"  # the money moves all the same: its books cannot wait for a period
    (s,) = sweep_now()
    assert s.status == "booked" and held() == 100
    assert {e.period for e in LedgerEntry.load_some(1, LedgerEntry.max_id()) if e.sweep} == {None}


def test_nothing_is_swept_when_the_fund_holds_no_more_than_it_keeps_and_the_fee(monkeypatch):
    north(monkeypatch, balance=110)
    assert sweep_now() == [] and Sweep.count() == 0 and not transfers()


def test_a_quarter_sweeps_only_the_fund_it_is_asked_for(monkeypatch):
    north(monkeypatch, rules=(RULE, {**RULE, "fund": "ROADS"}))
    ledger_ok()
    assert sweep_now(fund="WELFARE") == []
    assert [s.fund for s in sweep_now(fund="ROOT")] == ["ROOT"]


def test_the_quarter_keeps_the_last_rules_when_the_capital_does_not_answer(monkeypatch):
    capital = north(monkeypatch)
    ledger_ok()
    capital.answer("quarter_sync", fake_chain.rejects("capital is down"))
    assert sweep_now() == []  # it has never heard of a rule

    capital.answer("quarter_sync", sync_reply(RULE))
    ok("quarter_sync", "ctl")
    capital.answer("quarter_sync", fake_chain.rejects("capital is down"))
    assert [s.status for s in sweep_now()] == ["booked"]


def test_a_capital_that_does_not_answer_leaves_the_sweep_sent_and_the_quarter_reports_again(monkeypatch):
    capital = north(monkeypatch)
    ledger_ok()
    capital.answer("quarter_sweep_report", fake_chain.rejects("capital is down"))
    (s,) = sweep_now()
    assert (s.status, s.block) == ("sent", "18234")
    assert held() == 100  # the quarter booked it: the money is on the capital's account

    capital.answer("quarter_sweep_report", {"ok": True})
    (again,) = sweep_now()
    assert again is s and s.status == "booked"
    assert len(transfers()) == 1 and LedgerEntry.count() > 0
    assert len([e for e in LedgerEntry.load_some(1, LedgerEntry.max_id()) if e.sweep]) == 3  # booked once


def test_a_ledger_that_refuses_the_transfer_moves_nothing_and_the_next_epoch_sweeps_again(monkeypatch):
    north(monkeypatch)
    fake_chain.state["replies"][ICRC1] = REFUSED
    (s,) = sweep_now()
    assert s.status == "failed" and "InsufficientFunds" in s.reason and held() == 1_000
    assert sweep_now() == []  # this epoch has its sweep

    later(EPOCH)
    ledger_ok()
    (next_epoch,) = sweep_now()
    assert (next_epoch.id, next_epoch.status) == ("SW-2", "booked") and held() == 100


def test_a_ledger_that_does_not_answer_gets_the_same_transfer_again_and_a_duplicate_is_its_block(monkeypatch):
    north(monkeypatch)
    fake_chain.state["replies"][ICRC1] = Exception("no answer")
    (s,) = sweep_now()
    assert s.status == "sending" and held() == 1_000

    later(3_600)
    fake_chain.state["replies"][ICRC1] = DUPLICATE  # the ledger applied the first one
    assert [x.status for x in sweep_now()] == ["booked"]
    assert (s.block, held()) == ("77", 100)
    first, second = transfers()
    assert first == second


def test_a_sweep_still_unknown_after_23_hours_is_never_sent_again_until_a_person_resolves_it(monkeypatch):
    north(monkeypatch)
    fake_chain.state["replies"][ICRC1] = Exception("no answer")
    (s,) = sweep_now()
    later(23 * 3_600)
    assert [x.status for x in sweep_now()] == ["needs_check"]
    ledger_ok()
    assert [x.status for x in sweep_now()] == ["needs_check"] and len(transfers()) == 1
    later(EPOCH)
    assert [x.id for x in sweep_now()] == ["SW-1"]  # a later epoch starts no sweep of this rule while one needs checking
    assert len(transfers()) == 1

    out = ok("sweep_resolve", "ctl", sweep=s.id, block=90_877)
    assert out["status"] == "sent" and (s.block, held()) == ("90877", 100)
    assert [x.status for x in sweep_now()] == ["booked"]  # reported at the next sweep
    assert len(transfers()) == 1


def test_a_sweep_the_ledger_never_applied_is_resolved_as_failed(monkeypatch):
    north(monkeypatch)
    fake_chain.state["replies"][ICRC1] = TOO_OLD
    (s,) = sweep_now()
    assert s.status == "needs_check"  # the ledger will not recognise a repeat any more
    ok("sweep_resolve", "ctl", sweep=s.id)
    assert (s.status, held()) == ("failed", 1_000)
    assert call("sweep_resolve", "ctl", sweep=s.id)["kind"] == "conflict"  # only a sweep that needs checking
    assert call("sweep_resolve", "ctl", sweep="SW-404")["kind"] == "not_found"


def test_two_calls_at_once_record_one_sweep_and_book_it_once(monkeypatch):
    north(monkeypatch)
    monkeypatch.setattr(fake_chain, "call", suspended)
    first = start("quarter_sweep", "bob")
    second = start("quarter_sweep", "bob")  # the ledger has not answered the first
    assert Sweep.count() == 1

    assert [s for s in finish(first, "(variant { Ok = 5 : nat })")["sweeps"]] == ["SW-1"]
    assert finish(second, DUPLICATE)["ok"]
    s = Sweep["SW-1"]
    assert (s.status, s.block, held()) == ("booked", "5", 100)
    assert len([e for e in LedgerEntry.load_some(1, LedgerEntry.max_id()) if e.sweep]) == 3


def test_a_quarter_sweeps_when_its_treasury_epoch_ends(monkeypatch):
    north(monkeypatch)
    ledger_ok()
    assert not fake_chain.state["timers"]
    ok("quarter_sync", "ctl")  # the rules arrive: the timer is armed for the end of the epoch
    (seconds, _), *_ = [t for t in fake_chain.state["timers"] if t[1] is sweeps.end_of_epoch]
    assert seconds == EPOCH - fake_chain.state["now"] % EPOCH

    receive(500)
    later(seconds)
    fire_timers()
    (s,) = Sweep.load_some(1, 1)
    assert (s.amount, s.status, held()) == (1_500 - 100 - 10, "booked", 100)
    assert [t for t in fake_chain.state["timers"] if t[1] is sweeps.end_of_epoch]  # and the next one


def test_the_timer_is_armed_again_when_a_quarter_starts_after_an_upgrade(monkeypatch):
    north(monkeypatch)
    ok("quarter_sync", "ctl")
    sweeps._timer["armed"] = False
    fake_chain.state["timers"].clear()
    quarters.resume()
    assert [t for t in fake_chain.state["timers"] if t[1] is sweeps.end_of_epoch]


def test_what_the_quarter_sent_reaches_the_gge_methods_as_a_sweep(monkeypatch):
    north(monkeypatch)
    ledger_ok()
    (s,) = sweep_now()
    listed = ok("sweep_list", "bob", quarter="north")["sweeps"]
    assert [(x["id"], x["status"], x["block"], x["to"]) for x in listed] == [(s.id, "booked", "18234", TO)]
