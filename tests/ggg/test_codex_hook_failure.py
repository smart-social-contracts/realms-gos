"""A legacy codex whose hook fails does not trap the message it runs in, and one that lists a hook its code does not
define is not installed (docs/codices/README.md; found in the review of realms-gos#697).

``codex.hook`` says a failing hook never fails the caller and marks the codex unhealthy until a hook succeeds. A hook
that is not defined, or that takes other arguments than the realm gives it, used to raise out of it instead: after the
switch, so ``codex_install`` came back failed with the codex changed (a hook that is not defined), or trapped (a
signature that does not fit), and a trap in the message of a vote or a timer rolls it back, so the proposal never
concludes.
"""

import fake_chain
import pytest
import world
from api import codex
from core import op, sandbox
from ggg import Codex, Proposal, Realm
from kit import call, denied, fire_timers, later, ok
from test_codex import SOURCE, build, manifest, vote_through

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


def test_a_hook_the_active_codex_lost_its_function_for_is_a_failure_of_the_hook_and_not_of_the_call():
    """A codex stored before the manifest was checked against its code: its hook has no function. The hook fails, with
    the reason; nothing raises, the codex is unhealthy, and a hook that works clears it."""
    build()
    ok("codex_install", "ctl", **install_args(SOURCE, hooks=["init", "act"]))
    assert state() == ("test@1.0.0", True, None)
    Codex["test@1.0.0"].source = "from ggg_sdk import hook\n\n@hook\ndef act(args):\n    return {}\n"  # lost `init`
    out = op.run(codex.hook("init"))
    assert out["ok"] is False and out["error"] == "KeyError: the code does not define the hook init"
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
    """The check reads the syntax tree: decorated, plain and async functions defined at the top level are hooks."""
    build()
    source = ("from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {}\n\n"
              "def act(args, context=None):\n    return {}\n\nasync def later(args):\n    return {}\n")
    out = ok("codex_install", "ctl", **install_args(source, hooks=["init", "act", "later"]))
    assert out["codex"] == "test@1.0.0" and out["init"]["ok"] is True and state() == ("test@1.0.0", True, None)


@pytest.mark.parametrize("source", [
    "init = lambda args: {}\n",                                      # bound, not defined
    "def helper():\n    def init(args):\n        return {}\n",      # nested in a function
    "class Hooks:\n    def init(self, args):\n        return {}\n",  # a method
    "if True:\n    def init(args):\n        return {}\n",           # not at the top level
    "from elsewhere import init\n",                                  # imported
], ids=["assigned", "nested", "method", "inside an if", "imported"])
def test_a_hook_that_is_not_a_top_level_function_is_not_one(source):
    """Only a top-level ``def`` counts: a name that is used or bound some other way is not a hook the realm can call."""
    build()
    reason = "the manifest lists the hook init, which the code does not define as a top-level function"
    assert reason in denied("codex_install", "ctl", **install_args(source))
    assert state() == NO_CODEX


@pytest.mark.parametrize("failure", [SyntaxError("invalid syntax"), ValueError("a null byte"),
                                     RecursionError("maximum recursion depth exceeded"), MemoryError()])
def test_a_syntax_tree_that_cannot_be_made_is_left_to_the_checks_of_the_code_itself(monkeypatch, failure):
    """Code that does not compile, or that is nested too deep for the parser (a RecursionError or a MemoryError, which
    the wasm can catch): there is no tree to read, so the hook check says nothing and raises nothing."""
    import ast

    def parse(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ast, "parse", parse)
    assert codex.defined_hooks("def init(args):\n    return {}\n") is None
    codex.check_hooks({"hooks": ["init"]}, "def init(args):\n    return {}\n")  # returns: nothing to judge


def test_a_source_that_does_not_parse_is_refused_by_the_compile_check_and_not_by_the_hook_check():
    assert codex.defined_hooks("def (") is None
    codex.check_hooks({"hooks": ["init"]}, "def (")
