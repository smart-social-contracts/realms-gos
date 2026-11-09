"""Who reads a pending action (realms-gos#633, from the review of #639). A card holds what the delegate asked for, the codes,
keys and private data of the call among them, so a member's other delegates must not read it: a delegate acting for the
member reads the actions it prepared and no others, and anyone who is neither the grantor in person nor the delegate that
prepared the action gets the card without the secret fields of its call."""
import json

import fake_chain
import keys_people
from core import pending
from core.op import Call
from ggg import Delegation, Event, Notification, PendingAction
from kit import denied, ok, rows
from test_delegation_affairs import lend
from test_inner_calls import build as console_world, grant as lend_console, made
from test_pending_actions import approved, card

SECRETS = ("JOIN-1", keys_people.DERIVED)


def seen(value):
    return json.dumps(value, default=str)


def two_delegates():
    """Alice's AI connection claude-conn prepares a joining (with a code) and a key; a second delegate, ``reader``, holds
    "See my affairs" and prepares a private-data update of its own. Returns the three actions' ids."""
    cases = keys_people.build()
    lend("alice", "membership", "keys", "see")
    lend("alice", "see", "keys", delegate="reader")
    join = ok("realm_join", "claude-conn", on_behalf_of="alice", **cases["realm_join"][2])["pending"]
    derive = ok("crypto_derive_key", "claude-conn", on_behalf_of="alice", **cases["crypto_derive_key"][2])["pending"]
    own = ok("private_data_update", "reader", on_behalf_of="alice", **cases["private_data_update"][2])["pending"]
    return join, derive, own


def test_a_second_delegate_of_the_member_reads_none_of_the_actions_another_delegate_prepared():
    join, derive, own = two_delegates()
    approved(derive, "alice")  # the key is now in the action's result, for claude-conn and the member
    replies = [
        ok("action_get", "reader", action=join, on_behalf_of="alice"),
        ok("action_get", "reader", action=derive, on_behalf_of="alice"),
        ok("read_for", "reader", method="action_get", args={"action": derive}, grantor="alice"),
    ]
    assert [r["action"] for r in replies] == [None, None, None]
    listed = [ok("action_list", "reader", on_behalf_of="alice"),
              ok("read_for", "reader", method="action_list", args={}, grantor="alice"),
              ok("action_list", "reader", on_behalf_of="alice", status="done")]
    assert [[c["id"] for c in r["actions"]] for r in listed] == [[own], [own], []]
    assert not any(secret in seen([replies, listed]) for secret in SECRETS)


def test_each_reader_reads_what_it_should():
    join, derive, own = two_delegates()
    assert [c["id"] for c in ok("action_list", "alice")["actions"]] == [join, derive, own]  # the member in person: all
    assert [c["id"] for c in ok("action_list", "claude-conn", on_behalf_of="alice")["actions"]] == [join, derive]
    assert [c["id"] for c in ok("action_list", "claude-conn", role="delegate")["actions"]] == [join, derive]
    assert card(join, "claude-conn")["args"] == {"code": "JOIN-1"} and card(own, "reader")["id"] == own
    assert ok("action_get", "claude-conn", action=join, on_behalf_of="alice")["action"]["args"] == {"code": "JOIN-1"}
    assert ok("action_get", "claude-conn", action=own, on_behalf_of="alice")["action"] is None
    assert ok("action_get", "reader", action=own, on_behalf_of="alice")["action"]["id"] == own
    approved(derive, "alice")
    mine = ok("action_get", "claude-conn", action=derive, on_behalf_of="alice")["action"]
    assert mine["result"]["encrypted_key"] == keys_people.DERIVED  # the key the member handed to this delegate


def test_a_reader_that_is_neither_the_grantor_in_person_nor_the_preparing_delegate_gets_no_secret_field():
    join, derive, _ = two_delegates()
    approved(derive, "alice")
    for name, action in (("realm_join", join), ("crypto_derive_key", derive)):
        a = PendingAction[action]
        whole = pending.card(Call("alice", "alice", "user"), a)
        assert whole["args"] == dict(a.args) and whole["summary"] == a.summary
        for reader in (Call("alice", "alice", "task"), Call("alice", "alice", "extension"), Call("alice", "alice", "shell"),
                       Call("alice", "reader", "user")):
            shown = pending.card(reader, a)
            assert not any(secret in seen(shown) for secret in SECRETS), (name, reader.origin, reader.by)
            assert shown["id"] == action and shown["method"] == name and shown["status"] == a.status
            assert "What happens next" in [line["label"] for line in shown["summary"]]


def test_a_grants_activity_is_read_by_its_grantor_and_its_delegate_alone():
    two_delegates()
    claude = next(d.id for d in rows(Delegation) if d.delegate == "claude-conn")
    assert ok("delegation_activity", "alice", delegation=claude)["events"]
    assert ok("delegation_activity", "claude-conn", delegation=claude)["events"]
    assert "not yours" in denied("delegation_activity", "reader", delegation=claude, on_behalf_of="alice")
    lend("alice", "see", delegate="agent-3")
    assert "not yours" in denied("delegation_activity", "agent-3", delegation=claude, on_behalf_of="alice")


def test_nothing_of_a_card_is_in_a_notice_an_event_or_what_a_second_delegate_reads_of_the_member():
    join, derive, own = two_delegates()
    approved(derive, "alice")
    approved(join, "alice")
    inbox = ok("notification_list", "reader", on_behalf_of="alice", unread=False)
    assert not any(secret in seen([inbox, [(n.title, n.body, n.href) for n in rows(Notification)]]) for secret in SECRETS)
    assert not any(secret in seen([(e.args, e.result) for e in rows(Event)]) for secret in SECRETS)


def test_a_delegate_does_not_export_the_pending_actions():
    two_delegates()
    lend("ctl", "admin", delegate="agent-4")
    assert "does not export PendingAction" in denied("realm_export", "agent-4", entity="PendingAction", on_behalf_of="ctl")
    assert ok("realm_export", "ctl", entity="PendingAction")["rows"]  # the member's own full export is theirs


def test_an_export_of_the_pending_actions_blanks_the_secrets_of_their_calls_whoever_exports():
    join, derive, _ = two_delegates()
    approved(derive, "alice")
    rows_ = ok("realm_export", "ctl", entity="PendingAction")["rows"]
    assert [r["id"] for r in rows_] == [join, derive, "AC-3"]
    assert not any(secret in seen(rows_) for secret in SECRETS)
    shown = {r["id"]: r for r in rows_}
    assert shown[join]["args"] == {"code": ""} and shown[derive]["result"]["encrypted_key"] == ""
    assert shown[join]["status"] == "pending" and shown[join]["method"] == "realm_join"
    assert "What happens next" in [line["label"] for line in shown[join]["summary"]]
    assert card(join)["args"] == {"code": "JOIN-1"}  # the card the member reads in the app is whole


def test_a_card_read_by_a_task_hides_the_secrets_of_a_call_that_the_call_carries():
    cases = keys_people.build()
    lend("alice", "propose")
    carried = {"op": "crypto_grant", "args": cases["crypto_grant"][2]}
    a = ok("proposal_create", "claude-conn", on_behalf_of="alice", title="Share", action=carried)["pending"]
    whole = pending.card(Call("alice", "alice", "user"), PendingAction[a])
    assert "env:c" in seen(whole)  # the member in person reads it all
    for reader in (Call("alice", "alice", "task"), Call("alice", "reader", "user")):
        shown = pending.card(reader, PendingAction[a])
        assert "env:c" not in seen(shown) and shown["args"]["action"]["op"] == "crypto_grant"
        assert shown["args"]["action"]["args"]["scope"] == keys_people.SCOPE


def test_a_delegated_task_reads_only_the_actions_its_delegate_prepared():
    console_world()
    fake_chain.state["replies"][("aaaaa-aa", "vetkd_derive_key")] = keys_people.VETKD_KEY
    lend_console("admin", "see", delegate="claude-conn")
    lend_console("keys", delegate="agent-2")
    derive = ok("crypto_derive_key", "agent-2", on_behalf_of="carla", transport_public_key="beef")["pending"]
    approved(derive, "carla")
    made("peek", [{"op": "action_get", "args": {"action": derive}}])
    ok("task_run", "claude-conn", task="peek", on_behalf_of="carla")
    logs = ok("task_logs", "claude-conn", task="peek", on_behalf_of="carla")
    (run,) = logs["runs"]
    assert run["status"] == "completed" and json.loads(run["result"]) == {"ok": True, "action": None}
    assert keys_people.DERIVED not in seen(logs)
