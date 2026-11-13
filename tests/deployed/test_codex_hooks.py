"""A legacy codex whose hook fails, on the real wasm (tests/ggg/test_codex_hook_failure.py runs the same in process).

A hook that is not defined or takes other arguments than the realm gives it raised out of ``codex.hook``, after the
switch: the install came back failed with the codex changed, or trapped, and the replica rolls back the message of a
vote or a timer that traps, so the proposal never concluded. The hook's failure is the codex's now: the codex is
unhealthy, the install stands. A manifest that lists a hook its code does not define is refused before anything is
installed.
"""

import pytest
from network import Network

HOUR = 3600
WRONG_SIGNATURE = "def init(ctx):\n    return {}\n"  # the realm calls init(args=..., context=...)
UNDEFINED = "from ggg_sdk import hook\n\n@hook\ndef act(args):\n    return {}\n"  # lists `init`, defines `act`
REASON = "the manifest lists the hook init, which the code does not define"


def manifest(version):
    return {"id": "lex", "version": version, "codex_api_version": 2, "hooks": ["init"], "capabilities": []}


def package(source, version):
    return {"manifest": manifest(version), "source": source}


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def realm(net):
    return net.realm("legacy")


def status(realm):
    return realm.ok("codex_status", "alice", query=True)


def file_install(realm, source, version, **more):
    action = {"op": "codex_install", "args": package(source, version)}
    return realm.ok("proposal_create", "alice", title=f"Install lex {version}", action=action, **more)["proposal"]


def vote(realm, proposal):
    for member in realm.members:
        if realm.proposal(proposal)["status"] != "voting":
            break
        realm.ok("proposal_vote", member, proposal=proposal, choice="yes")
    return realm.proposal(proposal)["status"]


def test_a_hook_the_code_does_not_define_is_refused_and_a_stored_proposal_fails_and_installs_nothing(net, realm):
    refused = realm.call("proposal_create", {"title": "Install", "action": {"op": "codex_install", "args": package(
        UNDEFINED, "1.0")}}, sender=net.people["alice"])
    assert not refused["ok"] and REASON in refused["error"]
    direct = realm.call("codex_install", package(UNDEFINED, "1.0"), sender=net.people["ctl"])
    assert not direct["ok"] and REASON in direct["error"]
    assert status(realm)["codex"] is None

    pid = file_install(realm, "from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {}\n", "1.0")
    stored = {"op": "codex_install", "args": package(UNDEFINED, "1.0")}
    out = realm.shell(f"from ggg import Proposal\nProposal[{pid!r}].action = {stored!r}\nprint('stored')")
    assert "Traceback" not in out
    assert vote(realm, pid) == "failed"  # the vote concludes
    row = realm.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
    assert row["status"] == "failed" and REASON in row["result"]["error"]
    assert status(realm)["codex"] is None  # it failed, and the codex did not change


def test_a_hook_that_takes_other_arguments_leaves_the_install_in_place_and_the_codex_unhealthy(net, realm):
    out = realm.ok("codex_install", "ctl", **package(WRONG_SIGNATURE, "1.1"))  # no trap
    assert out["codex"] == "lex@1.1" and out["init"]["ok"] is False and "TypeError" in out["init"]["error"]
    s = status(realm)
    assert s["codex"]["id"] == "lex@1.1" and s["healthy"] is False and s["problem"]["hook"] == "init"


def test_an_install_whose_hook_cannot_run_concludes_executed_by_vote_and_by_timer(net, realm):
    """The message of the vote that closes it, and the timer of an install that waits for its time, both complete."""
    voted = file_install(realm, WRONG_SIGNATURE, "1.2")
    assert vote(realm, voted) == "executed"
    row = realm.ok("proposal_get", "alice", query=True, proposal=voted)["proposal"]
    assert row["result"]["ok"] is True and row["result"]["init"]["ok"] is False
    assert status(realm)["codex"]["id"] == "lex@1.2" and status(realm)["healthy"] is False

    timed = file_install(realm, WRONG_SIGNATURE, "1.3", effective_at=realm.now() + HOUR)
    assert vote(realm, timed) == "execution_scheduled"
    assert status(realm)["codex"]["id"] == "lex@1.2"  # not yet
    net.wait(HOUR + 5)
    row = realm.ok("proposal_get", "alice", query=True, proposal=timed)["proposal"]
    assert row["status"] == "executed" and row["result"]["init"]["ok"] is False
    s = status(realm)
    assert s["codex"]["id"] == "lex@1.3" and s["healthy"] is False and s["problem"]["hook"] == "init"


# The canister has 8 subinterpreter handles, shared by every hook, extension call, the layered codex's sandbox and the
# controller's shell. This spawns until the table says it is full and gives them back: how many were free.
FREE = """
import _basilisk_sandbox as s
handles, err = [], ''
program = 'def f():\\n    return 1\\n'
digest = s.sha256(program)
s.approve_hash(digest)
try:
    while len(handles) < 12:
        handles.append(s.spawn_subinterpreter(program, digest, '', (), lambda *a: None, 1000000))
except Exception as e:
    err = str(e)[:200]
finally:
    for h in handles:
        s.close_subinterpreter(h)
    s.revoke_hash(digest)
print(len(handles), err)
"""
RAISES = "from ggg_sdk import hook\n\n@hook\ndef init(args):\n    raise ValueError('boom')\n"
GOOD = "from ggg_sdk import hook\n\n@hook\ndef init(args):\n    return {'fine': True}\n"


def free_handles(realm):
    out = realm.shell(FREE).strip()
    assert "Traceback" not in out, out
    return int(out.split()[0])


def test_hooks_that_fail_leave_no_sandbox_handle_open_and_the_realm_keeps_working(net, realm):
    """A failing hook no longer traps, so the call that made it commits: ``sandbox.execute`` has to close its
    subinterpreter itself. It did not, and each failed install (or join, with a buggy ``on_user_register``) kept one of
    the 8 handles for good: after them every hook failed with "sandbox handle table exhausted" and the shell trapped."""
    before = free_handles(realm)
    assert before >= 2
    for i in range(before + 4):  # more failures than there are handles
        source = WRONG_SIGNATURE if i % 2 else RAISES
        out = realm.ok("codex_install", "ctl", **package(source, f"4.{i}"))
        assert out["init"]["ok"] is False
    assert free_handles(realm) == before  # none was kept
    good = realm.ok("codex_install", "ctl", **package(GOOD, "5.0"))
    assert good["init"]["ok"] is True  # a hook still runs
    assert realm.shell("print('alive')").strip() == "alive"  # and so does the shell
    assert status(realm)["healthy"] is True
