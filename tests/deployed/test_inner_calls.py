"""Operations that run other operations, under a grant, on the real wasm (realms-gos#627, phase 7b): a task a delegate
made runs its steps as the delegate for the member on the time it was scheduled for, and fails once the grant is revoked;
a shell expression of the delegate meets the grant at every call it makes."""

import json

import pytest
from pic import Canister
from test_delegation_groups import DAY, launch, ok

HOUR = 3600
CONSOLE = ["task.create", "task.run", "task.schedule", "task.view", "shell.execute", "realm.monitor"]
MOTION = {"op": "proposal_create", "args": {"title": "Park"}}


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm in which Alice holds the permissions of the admin group (tasks and the shell)."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    ok(realm, "profile_create", people["ctl"], profile="console", allowed_to=CONSOLE)
    ok(realm, "role_assign", people["ctl"], user=people["alice"], profile="console")
    return realm


def events(realm, people, op, origin):
    """The audit events of ``op`` made from ``origin``, oldest first."""
    rows = ok(realm, "events", people["ctl"], query=True, count=500)["events"]
    return [e for e in reversed(rows) if e["op"] == op and e["origin"] == origin]


def test_a_scheduled_task_made_under_a_grant_runs_a_step_and_fails_once_the_grant_is_revoked(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    grant = ok(realm, "delegation_grant", alice, delegate=claude, groups=["admin", "propose"],
               expires_in=30 * DAY)["delegation"]
    ok(realm, "task_create", claude, on_behalf_of=alice, name="motions", steps=[MOTION])
    ok(realm, "task_add_schedule", claude, on_behalf_of=alice, task="motions", run_at=realm.now() + HOUR,
       repeat_every=HOUR)

    realm.wait(HOUR + 60)
    runs = ok(realm, "task_logs", alice, query=True, task="motions")["runs"]
    assert [r["status"] for r in runs] == ["completed"]
    [filed] = events(realm, people, "proposal_create", "task")
    assert (filed["user"], filed["by"], filed["grant"]) == (alice, claude, grant)

    ok(realm, "delegation_revoke", alice, delegation=grant)
    realm.wait(HOUR + 60)
    runs = ok(realm, "task_logs", alice, query=True, task="motions")["runs"]
    assert [r["status"] for r in runs] == ["failed", "completed"]
    assert "revoked or replaced" in runs[0]["result"]
    realm.wait(3 * HOUR)  # its schedule was cancelled with the grant: nothing fires again
    runs = ok(realm, "task_logs", alice, query=True, task="motions")["runs"]
    assert [r["status"] for r in runs] == ["failed", "completed"]
    assert len(events(realm, people, "proposal_create", "task")) == 1


def test_a_shell_expression_under_a_grant_reaches_only_the_operations_the_grant_covers(realm, people):
    alice, bob, claude = people["alice"], people["bob"], people["claude-conn"]
    grant = ok(realm, "delegation_grant", alice, delegate=claude, groups=["admin", "vote"],
               expires_in=30 * DAY)["delegation"]
    proposal = ok(realm, "proposal_create", bob, title="Bike lane")["proposal"]
    nickname = ok(realm, "me", alice, query=True)["user"]["nickname"]

    voted = ok(realm, "shell", claude, on_behalf_of=alice, expr=f'ops.proposal_vote(proposal="{proposal}", choice="yes")')
    assert voted["result"]["ok"]
    [vote] = events(realm, people, "proposal_vote", "shell")
    assert (vote["user"], vote["by"], vote["grant"]) == (alice, claude, grant)

    renamed = ok(realm, "shell", claude, on_behalf_of=alice, expr='ops.profile_update(nickname="Alice II")')["result"]
    assert not renamed["ok"] and "does not cover" in renamed["error"]
    assert ok(realm, "me", alice, query=True)["user"]["nickname"] == nickname
    ok(realm, "delegation_revoke", alice, delegation=grant)


# One call that runs another, measured in the canister: the member's own, and the same for a delegate under a grant. Each
# is run once before it is timed, which compiles what it reads (and, for the delegate, writes the notice its first act adds).
# The extension is a row made here, as an install would make it; a task of no steps and an extension function that calls
# nothing are the baselines the inner call is taken from.
EXTENSION = (
    "def vote(proposal, choice):\n    return rpc('proposal_vote', proposal=proposal, choice=choice)\n\n\n"
    "def nothing():\n    return 1\n"
)
MEASURE = """
import json
from core.op import call
from ggg import Extension

Extension(id="voter", version="1.0.0", operations=["proposal_vote"], source={source!r})


def spent(name, args, user):
    call(name, args, user=user)
    start = ic.performance_counter(0)
    out = call(name, args, user=user)
    used = ic.performance_counter(0) - start
    assert out["ok"], out
    return used


def extension(function, user, **extra):
    args = {{"proposal": "{proposal}", "choice": "yes"}} if function == "vote" else {{}}
    return spent("extension_call", {{"extension": "voter", "function": function, "args": args, **extra}}, user)


def task(name, steps, user, **extra):
    out = call("task_create", {{"name": name, "steps": steps, **extra}}, user=user)
    assert out["ok"], out
    return spent("task_run", {{"task": name, **extra}}, user)


step = [{{"op": "proposal_vote", "args": {{"proposal": "{proposal}", "choice": "yes"}}}}]
behalf = {{"on_behalf_of": "{alice}"}}
print(json.dumps({{
    "extension, own": [extension("nothing", "{alice}"), extension("vote", "{alice}")],
    "extension, delegated": [extension("nothing", "{claude}", **behalf), extension("vote", "{claude}", **behalf)],
    "task, own": [task("own-none", [], "{alice}"), task("own-one", step, "{alice}")],
    "task, delegated": [task("tied-none", [], "{claude}", **behalf), task("tied-one", step, "{claude}", **behalf)],
}}))
"""
# The instructions of an inner call made for a member, with margin over what PocketIC measures: an inner vote costs 27M
# for the member's own extension call and 33M for a delegate's, 31M and 36M in a task step; an extension call spends about
# 135M before it calls anything, in starting its sandbox.
INNER_CALL_BUDGET = 150_000_000


def test_the_instructions_of_an_inner_delegated_call_stay_within_budget(realm, people, costs):
    alice, bob, claude = people["alice"], people["bob"], people["claude-conn"]
    ok(realm, "delegation_grant", alice, delegate=claude, groups=["admin", "vote"], expires_in=30 * DAY)
    proposal = ok(realm, "proposal_create", bob, title="Cycle track")["proposal"]
    code = MEASURE.format(source=EXTENSION, proposal=proposal, alice=alice, claude=claude)
    spent = json.loads(realm.shell(code).strip().splitlines()[-1])
    for what, (baseline, with_call) in spent.items():
        costs.report(f"{what}: the call that runs another, with nothing to run", baseline)
        costs.report(f"{what}: the same with one inner call, ie. proposal_vote", with_call)
        costs.report(f"{what}: the inner call alone", with_call - baseline)
        print(f"instructions, {what}: {baseline:,} with nothing to run, {with_call:,} with one inner call, "
              f"{with_call - baseline:,} for the inner call")
        assert with_call - baseline < INNER_CALL_BUDGET and with_call < 2 * INNER_CALL_BUDGET
