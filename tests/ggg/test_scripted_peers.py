"""Scripted peers (``fake_chain.peer``): a canister the realm calls, played by the test.

The realm side is a quarter syncing with its capital, the one call to another realm that exists today
(``quarters.quarter_sync``); the layered codex's sync sources, followers and capitals are scripted the same way.
"""

import json

import fake_chain
import pytest
import world
from core import op
from fake_chain import peer, rejects, traps
from ggg import Quarter, Realm
from kit import call, ok
from test_quarters import CAPITAL, quarter


def row(cid, index, population=3):
    return {"id": cid, "name": cid, "index": index, "status": "active", "population": population,
            "codex_id": "agora", "codex_version": "1.4.2", "last_sync": 0}


SYNCED = {"ok": True, "directory": [row(CAPITAL, 0), row("north", 1), row("south", 2)],
          "codex": {"id": "agora", "version": "1.4.2"}, "index": 1}


def test_a_peer_answers_by_method_name_and_records_what_it_was_sent(monkeypatch):
    quarter(monkeypatch, "north")
    capital = peer(CAPITAL, quarter_sync=SYNCED)
    assert ok("quarter_sync", "ctl")["quarters"] == 3
    assert Quarter["south"].population == 3 and Realm["1"].sync_state["index"] == 1
    assert [sent["population"] for sent in capital.sent("quarter_sync")] == [len(world.PEOPLE)]
    assert capital.sent("federation_message") == []


def test_a_peer_can_answer_by_what_it_was_sent_and_change_over_time(monkeypatch):
    quarter(monkeypatch, "north")
    seen = []

    def busy_once(sent):
        seen.append(sent["population"])
        if len(seen) == 1:
            return {"ok": False, "error": "busy"}
        return {**SYNCED, "directory": [row(CAPITAL, 0), row("south", 2, population=sent["population"])]}

    peer(CAPITAL, quarter_sync=busy_once)
    assert call("quarter_sync", "ctl")["error"] == "the capital refused to sync: busy"
    ok("quarter_sync", "ctl")
    assert Quarter["south"].population == seen[1] == len(world.PEOPLE)


@pytest.mark.parametrize("reply, seen_by_the_realm", [
    (rejects("the canister is stopped"), "quarter_sync on realm failed: the canister is stopped"),
    (traps("out of memory"), "quarter_sync on realm failed: Canister realm trapped explicitly: out of memory"),
])
def test_a_forced_failure_reaches_the_realm_as_a_failed_call_and_the_operation_traps(monkeypatch, reply,
                                                                                    seen_by_the_realm):
    quarter(monkeypatch, "north")
    capital = peer(CAPITAL, quarter_sync=reply)
    with pytest.raises(fake_chain.Trap, match=seen_by_the_realm):
        call("quarter_sync", "ctl")
    assert len(capital.sent("quarter_sync")) == 1


def test_a_method_the_peer_has_no_answer_for_is_rejected(monkeypatch):
    quarter(monkeypatch, "north")
    peer(CAPITAL, federation_message={"ok": True})
    with pytest.raises(fake_chain.Trap, match="quarter_sync on realm failed: canister has no method 'quarter_sync'"):
        call("quarter_sync", "ctl")


@pytest.mark.parametrize("reply, seen_by_the_realm", [
    ("not json", "Expecting value"),
    ("", "the capital refused to sync: no reply"),
    ({"ok": True}, "missing 'directory'"),
])
def test_a_wrong_reply_is_what_the_realm_makes_of_it(monkeypatch, reply, seen_by_the_realm):
    quarter(monkeypatch, "north")
    peer(CAPITAL, quarter_sync=reply)
    try:
        refused = call("quarter_sync", "ctl")
    except fake_chain.Trap as trap:  # an exception in the operation's body traps the message
        refused = {"error": str(trap)}
    assert seen_by_the_realm in refused["error"]


def test_a_peer_is_handed_the_args_of_call_op_and_the_text_of_the_other_calls():
    echo = peer("echo", ping=lambda arg: {"got": arg})
    assert json.loads(op.run(fake_chain.call_op("echo", "ping", {"n": 1}))) == {"got": {"n": 1}}
    assert op.run(fake_chain.call_text("echo", "ping", "raw")) == '{"got": "raw"}'
    assert op.run(fake_chain.call("echo", "ping", '(record { n = 1 : nat })')) == '{"got": "(record { n = 1 : nat })"}'
    assert echo.sent("ping") == [{"n": 1}, "raw", "(record { n = 1 : nat })"]
    assert [m for _, m, _ in fake_chain.state["calls"]] == ["ping"] * 3  # the shared log keeps them too


def test_a_canister_that_is_no_peer_still_answers_from_the_fixed_replies():
    fake_chain.state["replies"][("ledger", "icrc1_balance_of")] = "(5 : nat)"
    peer("other", ping="pong")
    assert op.run(fake_chain.call("ledger", "icrc1_balance_of")) == "(5 : nat)"
    assert op.run(fake_chain.call("ledger", "unscripted")) == "()"
