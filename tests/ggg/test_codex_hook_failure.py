"""A legacy codex whose hook fails does not trap the message it runs in, and one that lists a hook its code does not
define is not installed (docs/codices/README.md; found in the review of realms-gos#697).

``codex.hook`` says a failing hook never fails the caller and marks the codex unhealthy until a hook succeeds. A hook
that is not defined, or that takes other arguments than the realm gives it, used to raise out of it instead: after the
switch, so ``codex_install`` came back failed with the codex changed (a hook that is not defined), or trapped (a
signature that does not fit), and a trap in the message of a vote or a timer rolls it back, so the proposal never
concludes.
"""

import json

import fake_chain
import pytest
from api import codex
from core import op, sandbox
from ggg import Codex, Proposal, Realm
from kit import call, denied, fire_timers, later, ok
from test_codex import SOURCE, build, manifest, registry

HOUR = 3600
# `init` takes ctx where the realm calls init(args=..., context=...): the call itself raises a TypeError, in the sandbox
# and not inside the hook (no @hook around it to turn the exception into a reply).
WRONG_SIGNATURE = "def init(ctx):\n    return {}\n"
UNDEFINED = "from ggg_sdk import hook\n\n@hook\ndef act(args):\n    return {}\n"  # no `init`
INIT_ONLY = {"hooks": ["init"], "capabilities": []}


def install_args(source, **more):
    return {"manifest": manifest(**{**INIT_ONLY, **more}), "source": source}


NO_CODEX = (None, False, None)  # no codex: the realm is not "healthy" either


def state():
    s = ok("codex_status", "alice")
    return s["codex"] and s["codex"]["id"], s["healthy"], s["problem"].get("hook")


def test_a_hook_the_code_does_not_define_is_refused_where_the_install_is_filed_and_where_it_is_called():
    build()
    args = install_args(UNDEFINED)
    reason = "the manifest lists the hook init, which the code does not define"
    assert reason in denied("codex_install", "ctl", **args)  # a direct call
    action = {"op": "codex_install", "args": args}
    assert reason in denied("proposal_create", "alice", title="Install", action=action)  # a filing
    assert state() == NO_CODEX and Proposal.count() == 0  # nothing installed, nothing filed


def test_a_proposal_stored_with_such_a_manifest_fails_and_the_codex_is_not_installed():
    """Stored before the refusal: the vote closes, the action fails (the check at the call), nothing switched."""
    build()
    ok("governance_update", "ctl", voting_window=0)
    pid = ok("proposal_create", "alice", title="Install",
             action={"op": "codex_install", "args": install_args(SOURCE)})["proposal"]
    Proposal[pid].action = {"op": "codex_install", "args": install_args(UNDEFINED)}  # as stored
    for voter in ("alice", "bob", "dana", "carla"):
        ok("proposal_vote", voter, proposal=pid, choice="yes")
    row = Proposal[pid]
    assert row.status == "failed" and "which the code does not define" in row.result["error"]
    assert state() == NO_CODEX  # failed: and the codex did not change


def test_a_hook_that_takes_other_arguments_marks_the_codex_unhealthy_and_the_install_stands():
    """The rule: a hook never undoes an install. The switch stands, the codex is unhealthy with the reason, and the call
    comes back ok with the hook's failure in ``init``, as it does for a hook that raises inside itself."""
    build()
    out = ok("codex_install", "ctl", **install_args(WRONG_SIGNATURE))  # no trap
    assert out["codex"] == "test@1.0.0" and out["init"]["ok"] is False and "TypeError" in out["init"]["error"]
    assert state() == ("test@1.0.0", False, "init")
    assert "TypeError" in ok("codex_status", "alice")["problem"]["error"]


@pytest.mark.parametrize("timed", [False, True], ids=["vote", "timer"])
def test_an_install_whose_hook_cannot_run_concludes_executed_with_the_codex_installed_and_unhealthy(timed):
    """In the message of the vote that closes it, and in the timer of an install that waits for its time."""
    build()
    ok("governance_update", "ctl", voting_window=0)
    filing = {"title": "Install", "action": {"op": "codex_install", "args": install_args(WRONG_SIGNATURE)}}
    if timed:
        filing["effective_at"] = fake_chain.now() + HOUR
    pid = ok("proposal_create", "alice", **filing)["proposal"]
    for voter in ("alice", "bob", "dana", "carla"):
        ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap: the last runs the action or schedules it
    if timed:
        assert Proposal[pid].status == "execution_scheduled" and state() == NO_CODEX
        later(HOUR)
        fire_timers()  # no trap in the timer
    row = Proposal[pid]
    assert row.status == "executed" and row.result["ok"] is True and row.result["init"]["ok"] is False
    assert state() == ("test@1.0.0", False, "init")  # installed, and its hook failing is on record


LONG_MESSAGE = "from ggg_sdk import hook\n\n@hook\ndef init(args):\n    raise ValueError('x' * 70000)\n"
JOIN_LONG_MESSAGE = LONG_MESSAGE.replace("def init", "def on_user_register")


def assert_cut(error):
    assert error.startswith("ValueError: xxxx") and "[cut" in error and len(error) <= codex.ERROR_MAX + 100, len(error)


def test_a_hook_that_fails_with_a_long_message_is_recorded_cut_and_nothing_traps():
    """Review of #711: the SDK's reply carries the whole text of the error, and the problem recorded in
    ``Realm.codex_install_state`` (a Json field of 65,536 characters) raised on it: the install, the vote and the timer
    that carried it trapped, and with ``on_user_register`` failing so every ``realm_join``. The error is cut."""
    build()
    Realm["1"].open_registration = True
    out = ok("codex_install", "ctl", **install_args(LONG_MESSAGE))  # no trap
    assert out["codex"] == "test@1.0.0" and out["init"]["ok"] is False
    assert_cut(out["init"]["error"])
    assert state() == ("test@1.0.0", False, "init")
    assert_cut(ok("codex_status", "alice")["problem"]["error"])
    ok("codex_install", "ctl", **install_args(JOIN_LONG_MESSAGE, version="1.1.0", hooks=["on_user_register"]))
    ok("realm_join", "newbie")  # a join whose hook fails this way still joins
    assert state() == ("test@1.1.0", False, "on_user_register")
    assert_cut(ok("codex_status", "alice")["problem"]["error"])


@pytest.mark.parametrize("timed", [False, True], ids=["vote", "timer"])
def test_an_install_whose_hook_fails_with_a_long_message_concludes_executed_with_the_error_cut(timed):
    build()
    ok("governance_update", "ctl", voting_window=0)
    filing = {"title": "Install", "action": {"op": "codex_install", "args": install_args(LONG_MESSAGE)}}
    if timed:
        filing["effective_at"] = fake_chain.now() + HOUR
    pid = ok("proposal_create", "alice", **filing)["proposal"]
    for voter in ("alice", "bob", "dana", "carla"):
        ok("proposal_vote", voter, proposal=pid, choice="yes")  # no trap
    if timed:
        later(HOUR)
        fire_timers()  # no trap
    row = Proposal[pid]
    assert row.status == "executed" and row.result["init"]["ok"] is False
    assert_cut(row.result["init"]["error"])
    assert state() == ("test@1.0.0", False, "init")


def test_a_hook_the_active_codex_lost_its_function_for_is_a_failure_of_the_hook_and_not_of_the_call():
    """A codex stored before the manifest was checked against its code: its hook has no function. The hook fails, with
    the reason; nothing raises, the codex is unhealthy, and a hook that works clears it."""
    build()
    ok("codex_install", "ctl", **install_args(SOURCE, hooks=["init", "act"]))
    assert state() == ("test@1.0.0", True, None)
    Codex["test@1.0.0"].source = "from ggg_sdk import hook\n\n@hook\ndef act(args):\n    return {}\n"  # lost `init`
    out = op.run(codex.hook("init"))
    assert out["ok"] is False and "init" in out["error"]  # the sandbox's own words: they differ on the wasm
    assert state() == ("test@1.0.0", False, "init")
    assert op.run(codex.hook("act"))["ok"] is True and state() == ("test@1.0.0", True, None)


@pytest.mark.parametrize("failure", [RuntimeError("the call spent its budget"), sandbox.Spent("instruction limit"),
                                     OSError("the interpreter is gone")])
def test_a_sandbox_that_fails_for_any_reason_is_a_hook_failure_and_never_raises(monkeypatch, failure):
    build()
    ok("codex_install", "ctl", **install_args(SOURCE))

    def failing(*args, **kwargs):
        raise failure
        yield  # a generator, like the call it replaces

    monkeypatch.setattr(sandbox, "call", failing)
    out = op.run(codex.hook("init"))
    assert out["ok"] is False and str(failure) in out["error"] and type(failure).__name__ in out["error"]
    assert state() == ("test@1.0.0", False, "init")


def test_a_manifest_whose_hooks_are_top_level_functions_installs_as_before():
    """The check reads the compiled code: decorated, plain and async functions defined at the top level are hooks."""
    build()
    source = ("from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {}\n\n"
              "def act(args, context=None):\n    return {}\n\nasync def later(args):\n    return {}\n")
    out = ok("codex_install", "ctl", **install_args(source, hooks=["init", "act", "later"]))
    assert out["codex"] == "test@1.0.0" and out["init"]["ok"] is True and state() == ("test@1.0.0", True, None)


@pytest.mark.parametrize("source", [
    "init = lambda args: {}\n",                                      # bound, not defined
    "def helper():\n    def init(args):\n        return {}\n",      # nested in a function
    "class Hooks:\n    def init(self, args):\n        return {}\n",  # a method
    "class init:\n    pass\n",                                       # a class of that name
    "from elsewhere import init\n",                                  # imported
    "init = None\n",                                                 # only bound
], ids=["assigned", "nested", "method", "class", "imported", "none"])
def test_a_hook_that_is_not_a_top_level_function_is_not_one(source):
    """Only a top-level ``def`` counts: a name that is used or bound some other way is not a hook the realm can call."""
    build()
    reason = "the manifest lists the hook init, which the code does not define as a top-level function"
    assert reason in denied("codex_install", "ctl", **install_args(source))
    assert state() == NO_CODEX


def test_the_hooks_a_code_defines_are_read_from_its_compiled_form():
    code = compile("def init(a):\n    return 1\n\nclass K:\n    def m(self):\n        pass\n"
                   "x = lambda: 1\nasync def later(a):\n    return 1\n", "hooks.py", "exec")
    assert codex.defined_hooks(code) == {"init", "later"}
    assert codex.defined_hooks(compile("", "hooks.py", "exec")) == set()
    codex.check_hooks({"hooks": ["init"]}, compile("def init(a):\n    return 1\n", "hooks.py", "exec"))
    codex.check_hooks({"hooks": None}, compile("", "hooks.py", "exec"))  # a manifest of another type: not this check's


@pytest.mark.parametrize("source", ["def (", "def init(args):\n    return {}\n\x00"], ids=["syntax", "null byte"])
def test_code_that_does_not_compile_is_refused_for_that_whatever_the_manifest_lists(source):
    """The hook check reads compiled code: a source that does not compile is the compile check's, with its reason."""
    build()
    args = install_args(source)
    assert "source does not compile" in denied("codex_install", "ctl", **args)
    action = {"op": "codex_install", "args": args}
    assert "source does not compile" in denied("proposal_create", "alice", title="Install", action=action)
    assert state() == NO_CODEX


@pytest.fixture
def compiles(monkeypatch):
    """The sources the realm compiled as a codex's code, by the filename the install gives it."""
    import builtins

    seen, real = [], builtins.compile

    def spy(source, filename, *args, **kwargs):
        if filename == "hooks.py":
            seen.append(source)
        return real(source, filename, *args, **kwargs)

    monkeypatch.setattr(builtins, "compile", spy)
    return seen


def test_code_the_realm_would_refuse_as_untrusted_is_not_compiled_by_the_hook_check(compiles):
    """Review of #711: the hook check compiled the code before the trust policy, and on the canister code nested too
    deep for the compiler ("x = " + "1 if 1 else " * 3000 + "1") crashes it past any ``except``: with approval
    required and the code not pinned, a stored proposal trapped its deciding vote for ever, where the base refused the
    code at trust. The check runs after it now, and filing compiles only what the realm takes without asking anyone."""
    build(approval=True)
    args = install_args(UNDEFINED)  # lists a hook it does not define: the hook check would refuse it, if it ran
    untrusted = "neither allowlisted nor approved by the marketplace"
    assert untrusted in denied("codex_install", "ctl", **args)  # the trust policy refuses it first
    action = {"op": "codex_install", "args": args}
    ok("proposal_create", "alice", title="Install", action=action)  # filed: the trust policy judges it at the vote
    ok("governance_update", "ctl", voting_window=0)
    pid = ok("proposal_create", "alice", title="Install again", action=action)["proposal"]
    for voter in ("alice", "bob", "dana", "carla"):
        ok("proposal_vote", voter, proposal=pid, choice="yes")
    row = Proposal[pid]
    assert row.status == "failed" and untrusted in row.result["error"]
    assert compiles == []  # no filing, call or vote compiled it
    assert state() == NO_CODEX


def test_code_the_realm_trusts_is_hook_checked_where_it_is_filed_and_where_it_is_called(compiles):
    """With the code's checksum pinned the realm takes it without asking: that code is read, filed and called alike."""
    from api import extensions

    build(approval=True)
    args = install_args(UNDEFINED)
    Realm["1"].trusted_approvers = [f"sha256:{extensions.checksum(args['manifest'], args['source'])}"]
    reason = "the manifest lists the hook init, which the code does not define"
    assert reason in denied("codex_install", "ctl", **args)
    action = {"op": "codex_install", "args": args}
    assert reason in denied("proposal_create", "alice", title="Install", action=action)
    assert compiles and state() == NO_CODEX and Proposal.count() == 0


def registry_files(source):
    return {"manifest.json": json.dumps(manifest(**INIT_ONLY)), "backend/hooks.py": source}


def test_a_codex_of_the_registry_whose_manifest_lists_a_hook_its_code_lacks_is_refused_and_nothing_installs():
    """The registry path (``codex`` and ``version``) is fetched by the call, so the hook check is made there only: after
    the trust policy has accepted the namespace, on the code it fetched."""
    build(approval=True)
    registry({"namespace": "ext/test/1.0.0", "approved": True, "approver": "mkt"}, registry_files(UNDEFINED))
    out = call("codex_install", "ctl", codex="test", version="1.0.0")
    assert not out["ok"] and "the manifest lists the hook init, which the code does not define" in out["error"]
    assert Codex["test@1.0.0"] is None and state() == NO_CODEX
    registry({"namespace": "ext/test/1.0.0", "approved": True, "approver": "mkt"}, registry_files(SOURCE))
    assert ok("codex_install", "ctl", codex="test", version="1.0.0")["codex"] == "test@1.0.0"  # the same, with the hook


@pytest.mark.parametrize("approval", [{"approved": False, "status": "pending"}, {"approved": True, "approver": "evil"}],
                         ids=["unapproved", "approved by another"])
def test_a_codex_of_the_registry_the_realm_does_not_trust_is_refused_for_that_and_never_compiled(compiles, approval):
    build(approval=True)
    registry({"namespace": "ext/test/1.0.0", **approval}, registry_files(UNDEFINED))
    out = call("codex_install", "ctl", codex="test", version="1.0.0")
    assert not out["ok"] and "which the code does not define" not in out["error"]  # the trust policy's reason
    assert compiles == [] and Codex["test@1.0.0"] is None and state() == NO_CODEX
