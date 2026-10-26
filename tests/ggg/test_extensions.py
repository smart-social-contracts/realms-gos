import base64
import json

import pytest

import fake_chain
import world
from api.extensions import checksum
from ggg import Department, Event, Extension, Realm, Vote
from kit import call, denied, later, ok, rows

VOTING = '''
def cast_vote(proposal, choice):
    return rpc("proposal_vote", proposal=proposal, choice=choice)


def sneak(title):
    return rpc("proposal_create", title=title)


def quorum(percent):
    return rpc("governance_update", quorum_percent=percent)
'''
MANIFEST = {"name": "voting", "version": "2.1.0", "profiles": ["member"], "categories": ["governance"],
            "show_in_sidebar": True, "backend": True, "operations": ["proposal_vote", "governance_update"]}


@pytest.fixture
def registry(monkeypatch):
    """A file registry holding voting 2.1.0, approved by the marketplace ``market``."""
    files = {"manifest.json": json.dumps(MANIFEST), "backend/entry.py": VOTING}
    approvals = {"ext/voting/2.1.0": {"approved": True, "approver": "market"}}

    def call_text(cid, method, arg=None):
        fake_chain.state["calls"].append((cid, method, arg))
        if method == "get_namespace_approval_icc":
            return json.dumps(approvals.get(arg, {"approved": False, "status": "unapproved"}))
        return json.dumps({"content_b64": base64.b64encode(files[json.loads(arg)["path"]].encode()).decode()})
        yield

    monkeypatch.setattr(fake_chain, "call_text", call_text)
    depts = world.build()
    realm = Realm["1"]
    realm.file_registry_canister_id, realm.marketplace_canister_id = "registry", "market"
    return approvals, depts


def test_story_the_voting_extension_casts_alices_vote(registry):
    out = ok("extension_install", "ctl", extension="voting", version="2.1.0")
    assert out["approved_by"] == "market" and Extension["voting"].operations == MANIFEST["operations"]
    p = ok("proposal_create", "bob", title="Bike lane on Main St")["proposal"]
    res = ok("extension_call", "alice", extension="voting", function="cast_vote", args={"proposal": p, "choice": "yes"})
    assert res["result"]["ok"]
    v = Vote[f"{p}:alice"]
    assert (v.voter, v.cast_by, v.choice) == ("alice", "alice", "yes")
    assert [(e.op, e.user, e.origin) for e in rows(Event) if e.op == "proposal_vote"] == [("proposal_vote", "alice", "extension")]
    assert "voting" in [e["id"] for e in ok("extension_list", "alice")["extensions"] if e["available"]]


def test_a_frontend_only_extension_installs_without_a_backend(registry, monkeypatch):
    approvals, _ = registry
    approvals["ext/board/1.0.0"] = {"approved": True, "approver": "market"}
    board = {"name": "board", "version": "1.0.0", "runtime": "sandboxed", "sdk_version": "1",
             "capabilities": ["call_operation"], "operations": ["proposal_list"], "secret_note": "not for the host"}
    files = {"manifest.json": json.dumps(board)}
    fetched = []

    def call_text(cid, method, arg=None):
        if method == "get_namespace_approval_icc":
            return json.dumps(approvals[arg])
        fetched.append(json.loads(arg)["path"])
        return json.dumps({"content_b64": base64.b64encode(files[fetched[-1]].encode()).decode()})
        yield

    monkeypatch.setattr(fake_chain, "call_text", call_text)
    ok("extension_install", "ctl", extension="board", version="1.0.0")
    assert fetched == ["manifest.json"] and not Extension["board"].source
    row = next(e for e in ok("extension_list", "alice")["extensions"] if e["id"] == "board")
    assert row["operations"] == ["proposal_list"]
    assert row["manifest"] == {"runtime": "sandboxed", "sdk_version": "1", "capabilities": ["call_operation"]}
    assert "has no backend" in call("extension_call", "alice", extension="board", function="x")["error"]


def test_I_EXT_1_an_extension_calls_only_declared_ops_as_the_caller(registry):
    ok("extension_install", "ctl", extension="voting", version="2.1.0")
    sneaked = ok("extension_call", "alice", extension="voting", function="sneak", args={"title": "x"})["result"]
    assert not sneaked["ok"] and "not declared" in sneaked["error"]
    weaker = ok("extension_call", "alice", extension="voting", function="quorum", args={"percent": 0})["result"]
    assert not weaker["ok"] and "governance.update" in weaker["error"]
    world.person("dana", "admin")
    assert ok("extension_call", "dana", extension="voting", function="quorum", args={"percent": 0})["result"]["ok"]
    denied("extension_call", fake_chain.ANONYMOUS, extension="voting", function="quorum", args={"percent": 0})


def test_I_EXT_3_installing_code_needs_the_trust_policy(registry):
    approvals, _ = registry
    assert "not approved" in call("extension_install", "ctl", extension="voting", version="9.9.9")["error"]
    approvals["ext/voting/2.1.0"]["approver"] = "someone-else"
    assert "does not trust" in call("extension_install", "ctl", extension="voting", version="2.1.0")["error"]
    assert Extension.count() == 0
    ok("realm_configure_trust_policy", "ctl", trusted_approvers=["someone-else"])
    ok("extension_install", "ctl", extension="voting", version="2.1.0")
    Realm["1"].require_marketplace_approval = False
    fake_chain.state["calls"].clear()
    ok("extension_install", "ctl", extension="voting", version="9.9.9")
    assert "get_namespace_approval_icc" not in [m for _, m, _ in fake_chain.state["calls"]]
    denied("extension_install", "alice", extension="voting", version="2.1.0")


def test_I_EXT_3_a_pinned_extension_needs_no_approval(registry):
    pin = "sha256:" + checksum(MANIFEST, VOTING)
    ok("realm_configure_trust_policy", "ctl", trusted_approvers=[pin])
    fake_chain.state["calls"].clear()
    assert ok("extension_install", "ctl", extension="voting", version="9.9.9")["approved_by"] == pin
    assert "get_namespace_approval_icc" not in [m for _, m, _ in fake_chain.state["calls"]]


def test_I_EXT_3_pins_alone_keep_trusting_the_marketplace(registry):
    approvals, _ = registry
    ok("realm_configure_trust_policy", "ctl", trusted_approvers=["sha256:" + "0" * 64])
    assert ok("extension_install", "ctl", extension="voting", version="2.1.0")["approved_by"] == "market"
    approvals["ext/voting/2.1.0"]["approver"] = "someone-else"
    assert "does not trust" in call("extension_install", "ctl", extension="voting", version="2.1.0")["error"]


def test_list_pages_follow_next(registry):
    for name in ("a", "b", "c"):
        Extension(id=name, version="1.0.0")
    first = ok("extension_list", "alice", count=2)
    assert [e["id"] for e in first["extensions"]] == ["a", "b"] and first["next"] == 3
    rest = ok("extension_list", "alice", count=2, **{"from": first["next"]})
    assert [e["id"] for e in rest["extensions"]] == ["c"] and rest["next"] is None
    assert rest["extensions"][0]["available"]


def test_install_is_a_realm_wide_vote(registry):
    world.policy(Department["Administration"], 2, 3)
    out = ok("extension_install", "ctl", extension="voting", version="2.1.0")
    assert out["requires_vote"] and Extension.count() == 0
    for v in world.PEOPLE:
        ok("proposal_vote", v, proposal=out["proposal"], choice="yes")
    later(8 * 86400)
    ok("proposal_finalize", "ctl", proposal=out["proposal"])
    assert ok("proposal_execute", "ctl", proposal=out["proposal"])["status"] == "executed"
    assert Extension["voting"].version == "2.1.0"


def test_access_sidebar_and_uninstall(registry):
    _, depts = registry
    ok("extension_install", "ctl", extension="voting", version="2.1.0")
    world.seat("finance.treasurer", "bob", depts["Finance"])
    ok("extension_set_access", "ctl", extension="voting", profiles=[], departments=["Finance"])
    denied("extension_set_access", "alice", extension="voting", profiles=["member"])
    assert "not available" in call("extension_call", "alice", extension="voting", function="sneak", args={"title": "x"})["error"]
    assert ok("extension_call", "bob", extension="voting", function="sneak", args={"title": "x"})
    shown = {e["id"]: e["available"] for e in ok("extension_list", "alice")["extensions"]}
    assert shown == {"voting": False}
    assert "unknown Department" in call("extension_set_access", "ctl", extension="voting", departments=["Nope"])["error"]
    ok("sidebar_configure", "ctl", extension="voting", sidebar={"category": "Governance", "order": 1})
    assert Extension["voting"].sidebar["category"] == "Governance" and Extension["voting"].sidebar["show_in_sidebar"]
    denied("sidebar_configure", "alice", extension="voting", sidebar={})
    denied("extension_list", fake_chain.ANONYMOUS)
    denied("extension_uninstall", "alice", extension="voting")
    ok("extension_uninstall", "ctl", extension="voting")
    assert Extension["voting"] is None
