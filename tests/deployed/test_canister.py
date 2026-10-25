"""The real wasm on PocketIC: the running example, through public operations only."""

import json
import pathlib

CODICES = pathlib.Path(__file__).resolve().parents[2] / "codices" / "codices"


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def refused(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert not out["ok"], (op, out)
    return out["error"]


def test_story(realm, people):
    ctl, founder, alice, bob, claude = (people[k] for k in ("ctl", "founder", "alice", "bob", "claude-conn"))
    assert ok(realm, "status", None, query=True)["status"] == "setup"
    assert "sign in" in refused(realm, "proposal_list", None, query=True)

    ok(realm, "setup_step", ctl, step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=False)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ok(realm, "token_register", ctl, symbol="DOM", ledger="ryjl3-tyaaa-aaaaa-aaaba-cai", decimals=8)
    dominion = CODICES / "dominion"
    out = ok(realm, "codex_install", founder, manifest=json.loads((dominion / "manifest.json").read_text()),
             source=(dominion / "backend" / "hooks.py").read_text())
    assert out["codex"].startswith("dominion@")
    ok(realm, "setup_launch", founder)
    assert ok(realm, "realm_info", None, query=True)["realm"]["status"] == "alpha"

    code = ok(realm, "invite_create", ctl, max_uses=5)["invite"]["code"]
    assert "invite" in refused(realm, "realm_join", bob)
    ok(realm, "realm_join", alice, code=code)
    ok(realm, "realm_join", bob, code=code)
    invoices = ok(realm, "my_invoices", alice, query=True)["invoices"]
    assert invoices and invoices[0]["reason"] == "registration" and invoices[0]["symbol"] == "DOM", invoices
    status = ok(realm, "codex_status", ctl, query=True)
    assert status["healthy"], status["problem"]

    p = ok(realm, "proposal_create", alice, title="Bike lane on Main St")["proposal"]
    ok(realm, "delegation_grant", alice, delegate=claude, operations=["proposal.vote"], expires_in=24 * 3600)
    ok(realm, "proposal_vote", claude, proposal=p, choice="yes", on_behalf_of=alice)
    assert "power of attorney" in refused(realm, "proposal_create", claude, title="x", on_behalf_of=alice)
    ok(realm, "proposal_vote", bob, proposal=p, choice="no")
    votes = ok(realm, "proposal_get", alice, query=True, proposal=p)["votes"]
    assert {(v["voter"], v["choice"], v["cast_by"]) for v in votes} == {(alice, "yes", claude), (bob, "no", bob)}

    events = ok(realm, "events", ctl, query=True)["events"]
    assert any(e["op"] == "proposal_vote" and e["user"] == alice and e["by"] == claude for e in events)
    assert len(ok(realm, "ops_list", None, query=True)["operations"]) >= 200
    assert "call it with run" in refused(realm, "proposal_create", alice, query=True, title="x")
    assert ok(realm, "shell", ctl, expr="ops.proposal_list()")["result"]["proposals"][0]["id"] == p
