"""Pending actions on the real wasm (realms-gos#595, phase 4): a delegate prepares a call through GGG's ``ggg_call_for``,
the member approves it with ``ggg_action_approve``, the realm runs it and the delegate reads the result; a second
approval sent while the first waits on another canister is refused; the actions survive an upgrade.
"""

import json

import pytest
from ic.candid import Types, decode, encode
from network import Network
from test_delegation_groups import launch, ok, refused

DAY = 86400
FSI, PDI = "\u2068", "\u2069"  # the isolate each quotation of the realm sits in


def plain(text):
    """``text`` without the isolates its quotations sit in, which no one types."""
    return text.replace(FSI, "").replace(PDI, "")


TEXT, BLOB = Types.Text, Types.Vec(Types.Nat8)
SUMMARY_LINE = Types.Record({"label": TEXT, "value": TEXT})
STATUS = Types.Variant({name: Types.Null for name in (
    "Pending", "Approved", "Approving", "Done", "Failed", "Declined", "Withdrawn", "Expired", "Void")})
ACTION = Types.Record({
    "id": TEXT, "grantor": Types.Principal, "delegate": Types.Principal, "grant": TEXT,
    "call": Types.Record({"method": TEXT, "arg": BLOB}), "summary": Types.Vec(SUMMARY_LINE), "risk": TEXT,
    "note": Types.Opt(TEXT), "status": STATUS, "hash": TEXT, "filed_at": Types.Nat64, "expires_at": Types.Nat64,
    "decided_at": Types.Opt(Types.Nat64), "result": Types.Opt(BLOB)})
CALL_FOR = Types.Record({"method": TEXT, "arg": BLOB, "on_behalf_of": Types.Principal, "note": Types.Opt(TEXT)})
CALL_FOR_REPLY = Types.Variant({"Done": BLOB, "Proposed": TEXT, "Pending": ACTION})
READ_FOR = Types.Record({"method": TEXT, "arg": BLOB, "on_behalf_of": Types.Principal})
APPROVE = Types.Record({"action": TEXT, "hash": TEXT, "on_behalf_of": Types.Opt(Types.Principal)})
ACCOUNT_ARGS = Types.Record({"network": TEXT, "address": TEXT, "symbol": Types.Opt(TEXT), "label": Types.Opt(TEXT),
                             "organization": Types.Opt(TEXT)})
ME_ARGS = Types.Record({"on_behalf_of": Types.Opt(Types.Principal)})

# What a filing and an approval cost in the canister, measured by the controller's shell.
FILING = """
from core.op import call
out = call("payment_account_add", {{"network": "ICP", "address": "aaaaa-bbbbb", "symbol": "REALM", "label": "Savings",
                                   "on_behalf_of": "{alice}"}}, user="{claude}")
assert out["ok"], out
"""
APPROVAL = """
from core.op import call
action = call("action_get", {{"action": "{action}"}}, user="{alice}")["action"]
out = call("action_approve", {{"action": "{action}", "hash": action["hash"]}}, user="{alice}")
assert out["ok"] and out["action"]["status"] == "done", out
"""
COUNT = "from ggg import PaymentAccount, Forward\nprint(PaymentAccount.count(), Forward.count())"
# The instructions of a filing and of an approval, with margin: measured on PocketIC at about 100M and 150M (a filing
# writes a record, an audit event and a notice and meets one Cedar decision; an approval meets two and runs the call).
FILING_BUDGET = APPROVAL_BUDGET = 300_000_000


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    from pic import Canister

    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    return realm


def account(address="aaaaa-bbbbb"):
    value = {"network": "ICP", "address": address, "symbol": ["REALM"], "label": ["Savings"], "organization": []}
    return encode([{"type": ACCOUNT_ARGS, "value": value}])


def prepare(realm, claude, alice, address="aaaaa-bbbbb"):
    call = {"method": "ggg_payment_account_add", "arg": account(address), "on_behalf_of": alice,
            "note": ["for the savings"]}
    out = realm.ggg("ggg_call_for", CALL_FOR, call, CALL_FOR_REPLY, claude)
    assert "Ok" in out and "Pending" in out["Ok"], out
    return out["Ok"]["Pending"]


def counts(realm):
    return [int(n) for n in realm.shell(COUNT).split()]


def test_prepare_through_ggg_call_for_approve_through_ggg_action_approve_and_read_the_result(realm, people, costs):
    alice, claude = people["alice"], people["claude-conn"]
    ok(realm, "delegation_grant", alice, delegate=claude, groups=["payout", "see"], expires_in=30 * DAY)
    action = prepare(realm, claude, alice)
    assert next(iter(action["status"])) == "Pending" and action["note"] == ["for the savings"]
    assert str(action["grantor"]) == alice and str(action["delegate"]) == claude
    assert counts(realm) == [0, 0]  # nothing ran
    assert {"label": "address", "value": "aaaaa-bbbbb"} in action["summary"]

    wrong = realm.ggg("ggg_action_approve", APPROVE, {"action": action["id"], "hash": "0" * 64, "on_behalf_of": []},
                      ACTION, alice)
    assert "Err" in wrong and counts(realm) == [0, 0]
    tap = {"action": action["id"], "hash": action["hash"], "on_behalf_of": []}
    delegate_tap = realm.ggg("ggg_action_approve", APPROVE, tap, ACTION, claude)
    assert "Err" in delegate_tap and counts(realm) == [0, 0]

    done = realm.ggg("ggg_action_approve", APPROVE, {"action": action["id"], "hash": action["hash"], "on_behalf_of": []},
                     ACTION, alice)["Ok"]
    assert next(iter(done["status"])) == "Done" and done["result"] and counts(realm) == [1, 0]
    assert "Err" in realm.ggg("ggg_action_approve", APPROVE, {"action": action["id"], "hash": action["hash"],
                                                              "on_behalf_of": []}, ACTION, alice)

    # the delegate reads the result, Candid-encoded with the method's own type
    got = realm.ggg("ggg_action_get", TEXT, action["id"], Types.Opt(ACTION), claude, query=True)["Ok"][0]
    assert next(iter(got["status"])) == "Done" and got["result"] == done["result"]
    assert decode(bytes(got["result"][0]))
    events = ok(realm, "events", people["ctl"], query=True, count=20)["events"]
    approval = [e for e in events if e["origin"] == "confirmed"][0]
    assert (approval["user"], approval["by"], approval["prepared_by"]) == (alice, alice, claude)

    # a read, for the member, through the same grant
    me = realm.ggg("ggg_read_for", READ_FOR, {"method": "ggg_me", "arg": encode([{"type": ME_ARGS, "value": {
        "on_behalf_of": []}}]), "on_behalf_of": alice}, BLOB, claude, query=True)
    assert decode(bytes(me["Ok"]))

    costs.measure(realm, "pending action: a delegate files a payout account", FILING.format(alice=alice, claude=claude))
    filed = ok(realm, "action_list", alice, query=True, status="pending")["actions"][0]["id"]
    spent = costs.measure(realm, "pending action: the member approves it", APPROVAL.format(action=filed, alice=alice))
    assert spent < APPROVAL_BUDGET and max(costs.samples["pending action: a delegate files a payout account"]) < FILING_BUDGET


def test_the_actions_and_their_states_survive_an_upgrade(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    waiting = prepare(realm, claude, alice, address="after-the-upgrade")
    done = ok(realm, "action_list", alice, query=True, status="done")["actions"]
    assert done
    realm.wait(600)
    realm.upgrade()
    again = ok(realm, "action_get", alice, query=True, action=waiting["id"])["action"]
    assert again["status"] == "pending" and again["hash"] == waiting["hash"] and again["args"] == {
        "network": "ICP", "address": "after-the-upgrade", "symbol": "REALM", "label": "Savings"}
    kept = ok(realm, "action_get", claude, query=True, action=done[0]["id"])["action"]
    assert kept["status"] == "done" and kept["result"] == done[0]["result"]
    after = ok(realm, "action_approve", alice, action=waiting["id"], hash=waiting["hash"])["action"]
    assert after["status"] == "done", after
    assert counts(realm)[0] == 3
    assert "is done" in refused(realm, "action_approve", alice, action=waiting["id"], hash=waiting["hash"])


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


def test_a_second_approval_sent_while_the_first_waits_on_another_canister_is_refused(net, people):
    alice, claude = people["alice"], people["claude-conn"]
    capital, north, south = net.realm("capital"), net.realm("north", members=("alice",)), net.realm("south")
    net.register_quarters(capital, north, south)
    north.ok("delegation_grant", "alice", delegate=claude, groups=["membership"], expires_in=30 * DAY)
    filed = north.ok("self_change_quarter", claude, quarter=south.cid, on_behalf_of=alice)
    action = north.ok("action_get", "alice", query=True, action=filed["pending"])["action"]
    assert action["status"] == "pending" and "guest" in json.dumps(action["summary"])
    assert north.ok("user_get", "ctl", query=True, user=alice)["user"].get("home_quarter") != south.cid

    # both calls reach the canister in the same round: the first awaits south, the second finds it approving
    first = north.submit("action_approve", {"action": action["id"], "hash": action["hash"]}, sender=alice)
    second = north.submit("action_approve", {"action": action["id"], "hash": action["hash"]}, sender=alice)
    replies = north.finish(first, second)
    approved, refused_ = sorted(replies, key=lambda r: not r["ok"])  # the replica picks which of the two runs first
    assert approved["ok"] and approved["action"]["status"] == "done", replies
    assert not refused_["ok"] and "is approving" in refused_["error"], replies
    assert north.ok("user_get", "ctl", query=True, user=alice)["user"]["home_quarter"] == south.cid
    assert north.shell("from ggg import Forward\nprint(Forward.count())").strip() == "1"
    again = north.ok("action_get", "alice", query=True, action=action["id"])["action"]
    assert again["status"] == "done" and again["result"]["ok"]
