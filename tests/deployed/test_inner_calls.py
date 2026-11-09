"""Operations that run other operations, under a grant, on the real wasm (realms-gos#627, phase 7b): a task a delegate
made runs its steps as the delegate for the member on the time it was scheduled for, and fails once the grant is revoked;
a shell expression of the delegate meets the grant at every call it makes."""

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
