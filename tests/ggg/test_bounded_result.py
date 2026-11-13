"""What a proposal (or a pending action) records of the reply of its call fits the field that keeps it (review of
realms-gos#697).

A refusal can echo what it refuses: ``codex.runnable`` names the codex API a manifest asks for with ``str()``, so a
stored manifest whose ``codex_api_version`` is a long list gives a refusal larger than the 65,536 characters of
``Proposal.result``. The write raised ("Invalid value for result"), which is not a ``Fail``: the call that recorded it
trapped, and a trap inside a vote's execution or a timer rolls the message back, so the deciding vote never concluded
the proposal, for ever. ``core.db.bounded`` cuts the recorded error, with a marker, and keeps ``ok`` and ``kind``.
"""

import json
import random

import fake_chain
import pytest
import world
from core import legislation, op
from core.db import JSON_MAX, bounded
from ggg import Event, PendingAction, Proposal, Realm
from kit import fire_timers, later, ok, rows

HOUR = 3600
FOUR = ("alice", "bob", "dana", "carla")  # more than half of the seven members: enough to carry a vote
ROAD_WORKS = {"op": "mandate_create", "args": {"title": "Road works"}}
NOTE = "[cut: the reply was"


def refusing(version):
    """A package the realm cannot run, whose refusal echoes the codex API it names."""
    return {"op": "codex_install", "args": {"manifest": {"id": "lex", "version": "1.0", "codex_api_version": version},
                                            "source": "pass"}}


# Stored as filed, they fit the field (a filing is limited to it): the refusal that echoes them does not. A backslash
# takes two characters in the JSON of the action, two in the text of the list the refusal names and four in the JSON of
# the refusal (twice the value); a tab two, two and three (one and a half times). (On the canister, whose JSON leaves a
# control character as it is, a DEL is five times: tests/deployed/test_sync_checks.py.)
ECHOES = {"backslashes": ["\\" * 17000], "tabs": ["\t" * 30000]}


@pytest.fixture(autouse=True)
def realm():
    world.build()
    Realm["1"].require_marketplace_approval = False  # sent code then passes the trust policy: only the API refuses
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    yield
    legislation.load(None)


def vote_yes(proposal):
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=proposal, choice="yes")


def assert_recorded_cut(result, reason):
    assert result["ok"] is False and result["kind"] == "invalid_argument"
    assert result["error"].startswith(reason) and NOTE in result["error"]
    assert len(json.dumps(result)) <= JSON_MAX


# ---- bounded


def test_a_reply_that_fits_is_recorded_as_it_is():
    reply = {"ok": False, "error": "x" * 1000, "kind": "invalid_argument"}
    assert bounded(reply) is reply
    exact = {"ok": False, "error": "", "kind": "k"}
    exact["error"] = "y" * (JSON_MAX - len(json.dumps(exact)))
    assert len(json.dumps(exact)) == JSON_MAX and bounded(exact) is exact
    assert len(json.dumps(bounded({**exact, "error": exact["error"] + "z"}))) <= JSON_MAX


@pytest.mark.parametrize("char", ["x", "\x7f", "\x01", "é", "\\", '"'])
def test_an_error_that_is_too_long_is_cut_with_a_marker_whatever_its_characters_take_in_json(char):
    reply = {"ok": False, "error": "refused: " + char * 90000, "kind": "invalid_argument", "permission": "p"}
    out = bounded(reply)
    assert (out["ok"], out["kind"], out["permission"]) == (False, "invalid_argument", "p")
    assert out["error"].startswith("refused: ") and out["error"].endswith("a record holds 65536]")
    assert len(json.dumps(out)) <= JSON_MAX and len(json.dumps(out, ensure_ascii=False)) <= JSON_MAX
    assert str(len(json.dumps(reply))) in out["error"]  # it says how large the reply was


def test_a_reply_that_succeeds_with_a_large_field_keeps_ok_and_the_small_fields_and_notes_the_size_of_the_large_one():
    init = {"ok": True, "result": "y" * 80000}
    out = bounded({"ok": True, "codex": "lex@1.0", "init": init})
    assert out == {"ok": True, "codex": "lex@1.0", "init": f"[cut: {len(json.dumps(init))} characters]"}
    refused = bounded({"ok": False, "kind": "forbidden", "error": "no", "permission": "p" * 70000})
    assert refused == {"ok": False, "kind": "forbidden", "error": "no",
                       "permission": f"[cut: {len(json.dumps('p' * 70000))} characters]"}
    not_text = bounded({"ok": False, "kind": "conflict", "error": ["a"] * 70000})
    assert not_text["ok"] is False and not_text["kind"] == "conflict" and not_text["error"].startswith("[cut: ")


def test_a_reply_too_large_in_many_fields_or_not_a_record_is_replaced_by_a_record_of_its_ok_and_kind():
    fields = {f"k{i}": "z" * 900 for i in range(100)}  # each is kept, together they are not
    many = bounded({"ok": True, **fields})
    assert many["ok"] is True and NOTE in many["cut"] and set(many) == {"ok", "cut"}
    refused = bounded({"ok": False, "kind": "conflict", **fields})
    assert set(refused) == {"ok", "kind", "error"} and refused["ok"] is False and NOTE in refused["error"]
    listed = bounded(["x" * 1000] * 100)  # not a reply: nothing to say whether it refused
    assert listed == {"cut": listed["cut"]} and NOTE in listed["cut"]
    no_ok = bounded(fields)  # what the audit log keeps of a reply: no ok in it
    assert set(no_ok) == {"cut"} and NOTE in no_ok["cut"]


def test_the_other_large_fields_are_cut_before_the_error_and_the_record_never_outgrows_a_field():
    padded = bounded({"ok": False, "kind": "invalid_argument", "error": "e" * 7000, "pad": "p" * 70000})
    assert padded == {"ok": False, "kind": "invalid_argument", "error": "e" * 7000,
                      "pad": f"[cut: {len(json.dumps('p' * 70000))} characters]"}  # the reason stays whole
    fields = {f"k{i}": "z" * 900 for i in range(100)}
    huge_kind = bounded({"ok": False, "kind": "k" * 70000, **fields})  # ok and kind come from what is kept
    assert huge_kind["ok"] is False and huge_kind["kind"].startswith("[cut: ") and NOTE in huge_kind["error"]
    assert len(json.dumps(huge_kind)) <= JSON_MAX


def test_whatever_the_shape_of_a_reply_what_is_recorded_fits_and_keeps_its_ok():
    rng = random.Random(716)
    chars = ["x", "\x7f", "\x01", "é", "\\", '"', "\t"]
    for _ in range(300):
        reply = {}
        for i in range(rng.randrange(1, 12)):
            kind = rng.randrange(5)
            key = rng.choice(["ok", "kind", "error", "result", "pad", f"f{i}"])
            if kind < 3:
                reply[key] = rng.choice(chars) * rng.choice([3, 900, 1200, 9000, 40000, 90000])
            elif kind == 3:
                reply[key] = [rng.choice(chars) * 500] * rng.choice([1, 20, 200])
            else:
                reply[key] = rng.choice([True, False, 7, None])
        out = bounded(reply)
        assert len(json.dumps(out)) <= JSON_MAX and len(json.dumps(out, ensure_ascii=False)) <= JSON_MAX, reply
        if len(json.dumps(reply)) <= JSON_MAX:
            assert out is reply
        elif isinstance(reply.get("ok"), bool) and len(json.dumps(reply["ok"])) <= JSON_MAX:
            assert out.get("ok") is reply["ok"] or "ok" not in out, (reply, out)


# ---- A stored proposal


@pytest.mark.parametrize("name", list(ECHOES))
def test_a_stored_proposal_whose_refusal_echoes_a_large_value_fails_with_a_cut_reason_and_concludes(name):
    """The reviewer's case: the action fits the field, the refusal that echoes it does not. Without the bound the last
    vote raised "Invalid value for result" and trapped, in every message that carried it."""
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    Proposal[p].action = refusing(ECHOES[name])  # as stored
    assert len(json.dumps(Proposal[p].action)) <= JSON_MAX
    vote_yes(p)  # no trap: the last vote runs the action in its own message
    row = Proposal[p]
    assert row.status == "failed"
    assert_recorded_cut(row.result, "this realm runs codex API 2, not ")


@pytest.mark.parametrize("name", list(ECHOES))
def test_a_stored_proposal_that_waits_for_its_time_fails_there_with_a_cut_reason_and_concludes(name):
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    Proposal[p].action = refusing(ECHOES[name])
    vote_yes(p)
    assert Proposal[p].status == "execution_scheduled"
    later(HOUR)
    fire_timers()  # no trap in the timer
    row = Proposal[p]
    assert row.status == "failed"
    assert_recorded_cut(row.result, "this realm runs codex API 2, not ")


def test_a_refusal_that_fits_is_recorded_whole():
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    Proposal[p].action = refusing([1])
    vote_yes(p)
    assert Proposal[p].status == "failed"
    assert Proposal[p].result == {"ok": False, "error": "this realm runs codex API 2, not [1]",
                                  "kind": "invalid_argument"}


def test_what_the_operation_returns_is_recorded_whole_when_it_fits():
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    vote_yes(p)
    row = Proposal[p]
    assert row.status == "executed" and row.result["ok"] is True and "mandate" in row.result


def test_an_ok_reply_too_large_for_the_field_does_not_trap_the_vote_that_ran_it(monkeypatch):
    """A refusal is not the only reply that can be large: the action ran, and its record keeps what is small of it. The
    operation itself returns the text (as an extension's function can), so the audit log meets it first, in
    ``dispatch``, and the proposal's result after it (review-716: the audit's ``Event.result`` raised first)."""
    real = op.OPS["mandate_create"]["fn"]

    def large(call, args):
        return {**real(call, args), "document": "z" * 80000}

    monkeypatch.setitem(op.OPS["mandate_create"], "fn", large)
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    vote_yes(p)  # no trap, in the audit or in the result
    row = Proposal[p]
    assert row.status == "executed" and row.result["ok"] is True and "mandate" in row.result
    assert row.result["document"] == "[cut: 80002 characters]"
    logged = [e for e in rows(Event) if e.op == "mandate_create"][-1]
    assert logged.result["document"] == "[cut: 80002 characters]" and "mandate" in logged.result


RETURNS_A_LOT = ("from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {'big': 'x' * 70000}\n")


@pytest.mark.parametrize("timed", [False, True], ids=["vote", "timer"])
def test_an_install_whose_hook_succeeds_with_a_large_result_concludes_executed_with_the_result_cut(timed):
    """Review of #711: an ``init`` that succeeds and returns more than the record holds trapped the vote that ran the
    install: ``codex_install`` replies with it, and the write of the proposal's result raised."""
    ok("governance_update", "ctl", voting_window=0)
    manifest = {"id": "lex", "version": "1.0", "codex_api_version": 2, "hooks": ["init"], "capabilities": []}
    action = {"op": "codex_install", "args": {"manifest": manifest, "source": RETURNS_A_LOT}}
    filing = {"title": "Install", "action": action}
    if timed:
        filing["effective_at"] = fake_chain.now() + HOUR
    p = ok("proposal_create", "alice", **filing)["proposal"]
    vote_yes(p)  # no trap
    if timed:
        later(HOUR)
        fire_timers()  # no trap
    row = Proposal[p]
    assert row.status == "executed" and row.result["ok"] is True and row.result["codex"] == "lex@1.0"
    assert row.result["init"].startswith("[cut: ") and len(json.dumps(row.result)) <= JSON_MAX


# ---- A pending action


def test_the_reply_a_pending_action_keeps_is_bounded_too(monkeypatch):
    """A delegate's call the member approves runs as the member and records its reply on the action: a refusal that
    echoes the action's own arguments is as large as in a proposal."""
    ok("delegation_grant", "alice", delegate="claude-conn", groups=["payout", "profile"], expires_in=30 * 86400)
    action = ok("payment_account_add", "claude-conn", on_behalf_of="alice", network="ICP", address="aaaaa-bbbbb",
                symbol="REALM", label="Savings")["pending"]
    card = ok("action_get", "alice", action=action)["action"]

    real = op.dispatch

    def refusal(name, arg, **kw):  # the approved call refuses; the approval itself is dispatched as usual
        if name != "payment_account_add":
            return (yield from real(name, arg, **kw))
        return json.dumps({"ok": False, "error": "no such account: " + "\x7f" * 70000, "kind": "not_found"})

    monkeypatch.setattr("core.op.dispatch", refusal)
    out = ok("action_approve", "alice", action=action, hash=card["hash"])  # no trap
    row = PendingAction[action]
    assert row.status == "failed" and row.result["ok"] is False and row.result["kind"] == "not_found"
    assert row.result["error"].startswith("no such account: ") and NOTE in row.result["error"]
    assert len(json.dumps(row.result)) <= JSON_MAX
    assert out["action"]["status"] == "failed"
