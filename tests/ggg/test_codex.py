import base64
import json
import pathlib

import pytest

from api import codex
import fake_chain
from core import op
import world
from ggg import Codex, Department, Event, Invoice, Realm, Token
from kit import call, denied, later, ok, rows
from test_spec import spec_ops

CODICES = pathlib.Path(__file__).resolve().parents[2] / "codices" / "codices"
SOURCE = '''
from ggg_sdk import hook, realm


@hook
def init(args):
    return {"stage": realm.stage}


@hook
def act(args):
    first = realm.effect("t_note", text="a")
    realm.effect("t_note", text=first["note"])
    realm.effect("proposal_open", proposal="P-1")
    realm.effect("t_note", text="for alice", on_behalf_of="alice")
    return {"first": first["note"], "peek": realm.call("t_peek", what="w")["seen"]}


@hook
def write_through_rpc(args):
    return realm.call("t_note", text="sneaky")


@hook
def read_undeclared(args):
    return realm.call("proposal_list")


@hook
def broken(args):
    return 1 // 0
'''


@op.op("treasury.manage")
def t_note(c, args):
    return {"note": "N-" + args["text"], "user": c.user, "origin": c.origin}


@op.op("realm.data_view", read=True)
def t_peek(c, args):
    return {"seen": args["what"]}


def manifest(version="1.0.0", **more):
    return {"id": "test", "version": version, "codex_api_version": 2, "capabilities": ["t_note", "t_peek"],
            "hooks": ["init", "act", "write_through_rpc", "read_undeclared", "broken"], **more}


def package(name, **changes):
    m = json.loads((CODICES / name / "manifest.json").read_text())
    return {"manifest": {**m, **changes}, "source": (CODICES / name / "backend" / "hooks.py").read_text()}


def build(approval=False):
    depts = world.build()
    Realm["1"].require_marketplace_approval = approval
    return depts


def hook(name, context=None):
    return op.run(codex.hook(name, context))


def vote_through(proposal):
    for v in world.PEOPLE:
        ok("proposal_vote", v, proposal=proposal, choice="yes")
    later(8 * 86400)
    ok("proposal_finalize", "ctl", proposal=proposal)
    return ok("proposal_execute", "ctl", proposal=proposal)


def test_install_status_uninstall():
    build()
    assert ok("codex_status", "alice")["codex"] is None
    out = ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert out["codex"] == "test@1.0.0" and out["init"]["result"] == {"stage": "setup"}
    s = ok("codex_status", "alice")
    assert s["codex"]["version"] == "1.0.0" and s["codex"]["status"] == "active" and s["healthy"]
    assert s["codex"]["capabilities"] == ["t_note", "t_peek"] and s["config"]["id"] == "test"
    assert "already active" in call("codex_install", "ctl", manifest=manifest(), source=SOURCE)["error"]
    ok("codex_uninstall", "ctl")
    assert ok("codex_status", "alice")["codex"] is None
    assert "no codex" in call("codex_uninstall", "ctl")["error"]


def test_permissions():
    build()
    assert "codex.install" in denied("codex_install", "alice", manifest=manifest(), source=SOURCE)
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert "codex.uninstall" in denied("codex_uninstall", "bob")
    assert "codex.revert" in denied("codex_revert", "bob")
    assert "sign in" in denied("codex_status", fake_chain.ANONYMOUS)


def test_upgrade_and_revert_to_previous():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    ok("codex_install", "ctl", manifest=manifest("1.1.0"), source=SOURCE)
    assert Codex["test@1.1.0"].previous == "test@1.0.0" and Codex["test@1.0.0"].status == "inactive"
    assert ok("codex_revert", "ctl")["codex"] == "test@1.0.0"
    assert (Codex["test@1.0.0"].status, Codex["test@1.1.0"].status) == ("active", "reverted")
    assert "no previous" in call("codex_revert", "ctl")["error"]


def test_refuses_another_hook_api():
    build()
    assert "codex API 2" in call("codex_install", "ctl", manifest=manifest(codex_api_version=1), source=SOURCE)["error"]


def test_revert_refuses_a_codex_on_another_hook_api():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    Codex["test@1.0.0"].manifest = manifest(codex_api_version=1)
    ok("codex_install", "ctl", manifest=manifest("1.1.0"), source=SOURCE)
    assert "codex API 2, not 1" in call("codex_revert", "ctl")["error"]
    assert (Codex["test@1.0.0"].status, Codex["test@1.1.0"].status) == ("inactive", "active")


def test_status_pages_the_install_history():
    build()
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        ok("codex_install", "ctl", manifest=manifest(v), source=SOURCE)
    first = ok("codex_status", "alice", count=2)
    assert [h["id"] for h in first["history"]] == ["test@1.0.0", "test@1.1.0"] and first["next"] == 3
    rest = ok("codex_status", "alice", count=2, **{"from": first["next"]})
    assert [h["id"] for h in rest["history"]] == ["test@1.2.0"] and rest["next"] is None
    assert rest["codex"]["id"] == "test@1.2.0"


def test_refuses_more_reads_than_the_sandbox_allows():
    build()
    reads = sorted(n for n, s in op.ops().items() if s["read"])[:33]
    assert "at most 32" in call("codex_install", "ctl", manifest=manifest(capabilities=reads), source=SOURCE)["error"]


def test_hook_needs_an_active_codex_declaring_it():
    build()
    assert hook("act") is None
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert hook("on_user_register", {"user": "bob"}) is None


def test_I_EXT_2_codex_applies_only_declared_effects():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    out = hook("act")
    assert out["ok"] and out["result"] == {"first": "N-a", "peek": "w"}
    first, chained, undeclared, impersonating = out["effects"]
    assert first["ok"] and (first["user"], first["origin"]) == (fake_chain.SELF, "codex")
    assert chained["note"] == "N-N-a"
    assert not undeclared["ok"] and "not declared" in undeclared["error"]
    assert not impersonating["ok"] and "power of attorney" in impersonating["error"]
    assert [e.op for e in rows(Event) if e.origin == "codex"] == ["t_note", "t_note"]


def test_I_EXT_2_reads_only_declared_and_never_writes():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert "not a read operation" in hook("write_through_rpc")["error"]
    assert "not declared" in hook("read_undeclared")["error"]


def test_failing_hook_marks_the_codex_unhealthy_until_one_succeeds():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert "ZeroDivisionError" in hook("broken")["error"]
    s = ok("codex_status", "alice")
    assert not s["healthy"] and s["problem"]["hook"] == "broken"
    hook("init")
    assert ok("codex_status", "alice")["healthy"]


def registry(monkeypatch, approval, files):
    def call_text(cid, method, arg=None):
        fake_chain.state["calls"].append((cid, method, arg))
        if method == "get_file":
            return json.dumps({"content_b64": base64.b64encode(files[json.loads(arg)["path"]].encode()).decode()})
        return json.dumps(approval)
        yield

    monkeypatch.setattr(fake_chain, "call_text", call_text)
    Realm["1"].file_registry_canister_id, Realm["1"].marketplace_canister_id = "registry", "mkt"


def test_I_EXT_3_code_from_args_needs_an_allowlisted_checksum():
    build(approval=True)
    m = manifest()
    assert "neither allowlisted" in call("codex_install", "ctl", manifest=m, source=SOURCE)["error"]
    Realm["1"].trusted_approvers = ["sha256:" + codex.checksum(m, SOURCE)]
    ok("codex_install", "ctl", manifest=m, source=SOURCE)
    assert "neither allowlisted" in call("codex_install", "ctl", manifest=manifest("1.1.0"), source=SOURCE)["error"]


@pytest.mark.parametrize("approval, trusted, error", [
    ({"approved": True, "approver": "mkt"}, [], None),
    ({"approved": True, "approver": "mkt"}, ["sha256:" + "0" * 64], None),
    ({"approved": True, "approver": "evil"}, [], "does not trust"),
    ({"approved": True, "approver": "evil"}, ["evil"], None),
    ({"approved": False, "status": "pending"}, [], "not approved"),
])
def test_I_EXT_3_registry_code_needs_a_trusted_approval(monkeypatch, approval, trusted, error):
    build(approval=True)
    files = {"manifest.json": json.dumps(manifest()), "backend/hooks.py": SOURCE}
    registry(monkeypatch, {"namespace": "ext/test/1.0.0", **approval}, files)
    Realm["1"].trusted_approvers = trusted
    out = call("codex_install", "ctl", codex="test", version="1.0.0")
    assert (out.get("error") is None if error is None else error in out["error"]), out
    assert ("registry", "get_namespace_approval_icc", "ext/test/1.0.0") in fake_chain.state["calls"]
    assert (Codex["test@1.0.0"] is not None) == (error is None)


def test_story_agora_upgrade_by_realm_wide_vote_then_revert():
    build()
    ok("codex_install", "ctl", **package("agora", version="1.4.2"))
    assert Department["Congress"] is not None
    world.policy(Department["Administration"], 2, 3)
    p = ok("codex_install", "ctl", **package("agora", version="1.5.0"))["proposal"]
    assert ok("codex_status", "alice")["codex"]["version"] == "1.4.2"
    vote_through(p)
    s = ok("codex_status", "alice")["codex"]
    assert (s["version"], s["previous"]) == ("1.5.0", "agora@1.4.2")
    vote_through(ok("codex_revert", "ctl")["proposal"])
    assert ok("codex_status", "alice")["codex"]["version"] == "1.4.2"


@pytest.mark.parametrize("name", ["agora", "dominion", "syntropia"])
def test_rewritten_codex_hooks_run_against_the_world(name):
    build()
    Token(id="DOM", ledger="dom-ledger", decimals=8)
    Realm["1"].status = "beta"
    pkg = package(name)
    hooks, declared, specified = pkg["manifest"]["hooks"], pkg["manifest"]["capabilities"], spec_ops()[0]
    assert set(declared) <= set(specified)
    runs = [ok("codex_install", "ctl", **pkg)["init"], hook("on_user_register", {"user": "bob"})]
    if "on_stage_change" in hooks:
        runs.append(hook("on_stage_change", {"stage": "beta"}))
        assert runs[-1]["result"]["invoiced"] >= len(world.PEOPLE) - 1
    if "assign_quarter" in hooks:
        quarters = [{"id": "north", "population": 5}, {"id": "south", "population": 2}]
        assert hook("assign_quarter", {"user": "bob", "quarters": quarters})["result"] == {"quarter": "south"}
        assert hook("assign_quarter", {"user": "bob", "quarters": quarters, "preferred": "north"})["result"] == {
            "quarter": "north"}
    for out in runs:
        assert out["ok"], out
        for e in out["effects"]:
            assert e["op"] in declared and "not declared" not in e.get("error", ""), e
    registered = runs[1]["effects"]
    assert [e["op"] for e in registered] == ["invoice_create", "notification_send"]
    assert registered[0]["ok"] and Invoice[registered[0]["invoice"]].payer == "User:bob", registered[0]


WIRED = '''
from ggg_sdk import hook, realm


@hook
def on_user_register(args):
    realm.effect("t_note", text="welcome " + args["user"])
    return {}


@hook
def on_stage_change(args):
    realm.effect("t_note", text="stage " + args["stage"])
    return {}


@hook
def assign_quarter(args):
    return {"quarter": args["quarters"][-1]["id"]}
'''


def test_hooks_fire_from_their_operations():
    build()
    Realm["1"].open_registration = True
    m = manifest(hooks=["on_user_register", "on_stage_change", "assign_quarter"])
    ok("codex_install", "ctl", manifest=m, source=WIRED)
    ok("realm_join", "newbie")
    notes = [e.result.get("note") for e in rows(Event) if e.op == "t_note"]
    assert "N-welcome newbie" in notes
