"""An extension's backend, the only way its screen reaches the realm: ``extension_read`` runs it as a query, which can
only read; ``extension_call`` as an update, which may wait for an operation (``yield wait(...)``) and resumes with its
reply. A backend that fails replies ``ok`` false when it changed nothing, and traps when it did."""
import base64
import json

import pytest

import fake_chain
import world
from api import extensions
from core import op
from ggg import Event, Extension, Proposal, Realm, User
from kit import call, ok, rows

LAB = '''
def me():
    return rpc("me")


def rename(nickname):
    return rpc("profile_update", nickname=nickname)


def rename_waiting(nickname):
    reply = yield wait("profile_update", nickname=nickname)
    return {"renamed": reply["ok"], "now": rpc("me")["user"]["nickname"]}


def rename_twice(first, second):
    a = yield wait("profile_update", nickname=first)
    b = yield wait("profile_update", nickname=second)
    return [a["ok"], b["ok"]]


def sneak():
    try:
        yield wait("proposal_create", title="Sneak")
    except PermissionError as e:
        return "refused: " + str(e)


def broken():
    raise ValueError("no such case")


def broken_after_rename(nickname):
    rpc("profile_update", nickname=nickname)
    raise ValueError("too late")


def _private():
    return "hidden"
'''
DECLARED = ["me", "profile_update"]


@pytest.fixture
def lab():
    world.build()
    Extension(id="lab", version="1.0.0", source=LAB, operations=DECLARED)
    extensions._open.clear()
    yield
    extensions._open.clear()


def read(caller, function, **args):
    """``extension_read`` of the lab as ``caller``, through the read endpoint: a query."""
    fake_chain.state["caller"] = caller
    arg = json.dumps({"extension": "lab", "function": function, "args": args})
    return json.loads(op.run(op.dispatch("extension_read", arg, query=True)))


def run(caller, function, **args):
    return call("extension_call", caller, extension="lab", function=function, args=args)


def test_a_read_runs_the_backend_as_a_query(lab):
    out = read("alice", "me")
    assert out["ok"] and out["result"]["ok"] and out["result"]["user"]["id"] == "alice"


def test_a_read_reaches_only_read_operations(lab):
    out = read("alice", "rename", nickname="Al")
    assert out["ok"] and not out["result"]["ok"] and "call it with run" in out["result"]["error"]
    assert User["alice"].nickname != "Al"


def test_a_read_cannot_wait(lab):
    out = read("alice", "rename_waiting", nickname="Al")
    assert not out["ok"] and "which a read cannot" in out["error"] and User["alice"].nickname != "Al"


def test_a_call_waits_for_an_operation_and_resumes_with_its_reply(lab):
    out = run("alice", "rename_waiting", nickname="Al")
    assert out["ok"] and out["result"] == {"renamed": True, "now": "Al"}
    [e] = [e for e in rows(Event) if e.op == "profile_update"]
    assert (e.user, e.by, e.origin) == ("alice", "alice", "extension")


def test_a_call_may_wait_more_than_once(lab):
    out = run("alice", "rename_twice", first="Al", second="Ally")
    assert out["result"] == [True, True] and User["alice"].nickname == "Ally"


def test_waiting_for_an_operation_the_extension_does_not_declare_raises_in_the_backend(lab):
    out = run("alice", "sneak")
    assert out["result"] == "refused: proposal_create is not declared by lab" and Proposal.count() == 0


def test_a_backend_that_fails_before_changing_anything_replies_ok_false(lab):
    out = run("alice", "broken")
    assert not out["ok"] and "lab.broken failed" in out["error"] and "no such case" in out["error"]


def test_a_backend_that_fails_after_a_change_traps_which_undoes_it(lab):
    with pytest.raises(fake_chain.Trap, match="lab.broken_after_rename failed after it called profile_update"):
        run("alice", "broken_after_rename", nickname="Al")


def test_only_the_backends_public_functions_can_be_called(lab):
    for function in ("_private", "__start", "__step"):
        out = run("alice", function)
        assert not out["ok"] and "is not a function of lab's backend" in out["error"], function
    out = run("alice", "missing")
    assert not out["ok"] and "the backend has no function missing" in out["error"]


def test_a_backend_of_more_operations_than_a_sandbox_allows_is_refused(lab):
    Extension["lab"].operations = [f"op_{i}" for i in range(33)]
    out = run("alice", "me")
    assert not out["ok"] and "declares 33 operations, more than the 32" in out["error"]


class Open:
    closed = False

    def close(self):
        self.closed = True


def test_a_call_waits_only_while_fewer_than_waiting_max_do(lab):
    now = fake_chain.state["now"]
    for i in range(extensions.WAITING_MAX):
        extensions._open[i] = (Open(), now, True)
    out = run("alice", "rename_waiting", nickname="Al")
    assert not out["ok"] and "are waiting already" in out["error"] and User["alice"].nickname != "Al"
    assert run("alice", "me")["ok"]  # one that does not wait still runs


def test_an_interpreter_still_open_after_the_wait_limit_is_closed_by_the_next_call(lab):
    stale, fresh = Open(), Open()
    now = fake_chain.state["now"]
    extensions._open.update({1: (stale, now - extensions.WAIT_LIMIT - 1, True), 2: (fresh, now, True)})
    assert run("alice", "me")["ok"]
    assert stale.closed and not fresh.closed and set(extensions._open) == {2}


MANIFEST = {"name": "wide", "version": "1.0.0", "backend": True, "operations": [f"op_{i}" for i in range(33)]}


def test_installing_an_extension_that_declares_more_than_32_operations_is_refused(monkeypatch):
    files = {"manifest.json": json.dumps(MANIFEST), "backend/entry.py": "def f():\n    return 1\n"}

    def call_text(cid, method, arg=None):
        if method == "get_namespace_approval_icc":
            return json.dumps({"approved": True, "approver": "market"})
        return json.dumps({"content_b64": base64.b64encode(files[json.loads(arg)["path"]].encode()).decode()})
        yield

    monkeypatch.setattr(fake_chain, "call_text", call_text)
    world.build()
    Realm["1"].file_registry_canister_id, Realm["1"].marketplace_canister_id = "registry", "market"
    out = call("extension_install", "ctl", extension="wide", version="1.0.0")
    assert not out["ok"] and "declares 33 operations, at most 32" in out["error"] and Extension["wide"] is None
