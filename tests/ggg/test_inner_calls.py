"""Power of attorney, phase 7b: a task, the shell and an extension call run other operations for the member. Under a
grant each of those runs as the delegate for the member, so it meets the grant like a call of the delegate's own, and a
task a delegate made is tied to the grant it was made under."""
import pytest

import fake_chain
import world
from api import setup, tasks
from core import op, poa
from ggg import (Delegation, Event, Extension, PaymentAccount, PendingAction, Proposal, Task, TaskExecution, TaskSchedule,
                 Transfer, User, Vote)
from kit import call, denied, fire_timers, later, ok, rows
from test_delegation_groups import finance_for_carla

DAY = 86400
ACCOUNT = dict(network="ICP", address="aaaaa-bbbbb", symbol="REALM", label="Savings")
MOTION = {"op": "proposal_create", "args": {"title": "Bike lane"}}
RENAME = {"op": "profile_update", "args": {"nickname": "Carla II"}}
ADD_ACCOUNT = {"op": "payment_account_add", "args": ACCOUNT}
CONSOLE = ("shell.execute", "task.create", "task.edit", "task.run", "task.schedule", "task.delete", "task.view",
           "realm.monitor")

# An extension that calls realm operations. The manifest declares three of the five it can call.
ASSISTANT = '''
def vote(proposal):
    return rpc("proposal_vote", proposal=proposal, choice="yes")


def vote_for(proposal, who):
    return rpc("proposal_vote", proposal=proposal, choice="no", on_behalf_of=who)


def motion(title):
    return rpc("proposal_create", title=title)


def rename(nickname):
    return rpc("profile_update", nickname=nickname)


def add_account(address):
    return rpc("payment_account_add", network="ICP", address=address, symbol="REALM", label="Savings")


def add_account_for(address, who):
    return rpc("payment_account_add", network="ICP", address=address, symbol="REALM", on_behalf_of=who)


def prove_identity():
    return rpc("identity_submit", kind="passport")
'''
DECLARED = ["proposal_vote", "profile_update", "payment_account_add"]


def build():
    """The world, with carla holding the permissions of the admin group (tasks and the shell)."""
    depts = world.build()
    world.profile("console", *CONSOLE)
    world.person("carla", "console")
    Extension(id="assistant", version="1.0.0", source=ASSISTANT, operations=DECLARED)
    return depts


def grant(*groups, grantor="carla", delegate="claude-conn", days=30):
    return ok("delegation_grant", grantor, delegate=delegate, groups=list(groups), expires_in=days * DAY)["delegation"]


def as_carla(name, **args):
    return call(name, "claude-conn", on_behalf_of="carla", **args)


def made(name, steps, **extra):
    """A task the delegate makes for carla."""
    return ok("task_create", "claude-conn", on_behalf_of="carla", name=name, steps=steps, **extra)


def events(op_name):
    return [e for e in rows(Event) if e.op == op_name]


def run_of(task):
    """The newest run of ``task``, as its owner reads it."""
    return ok("task_logs", "carla", task=task)["runs"][0]


def end(how, g):
    """End carla's grant ``g`` to claude-conn in one of the three ways a grant ends."""
    if how == "revoked":
        ok("delegation_revoke", "carla", delegation=g)
    elif how == "replaced":
        r = ok("delegation_request", "claude-conn", grantor="carla", groups=["admin", "propose"], expires_in=7 * DAY)
        ok("delegation_request_decide", "carla", request=r["request"], approve=True)
    else:
        later(31 * DAY)


# ---- The tasks


def test_a_task_made_under_a_grant_runs_its_steps_as_the_delegate_for_the_member():
    build()
    g = grant("admin", "propose")
    made("motions", [MOTION])
    assert tasks.creator(Task["motions"]) == "carla" and tasks.tie(Task["motions"]) == g
    assert as_carla("task_run", task="motions")["status"] == "completed"
    [p] = rows(Proposal)
    assert (p.proposer, p.filed_under) == ("carla", g)
    [e] = events("proposal_create")
    assert (e.user, e.by, e.origin, e.grant) == ("carla", "claude-conn", "task", g)


def test_a_scheduled_task_made_under_a_grant_runs_on_its_time_under_the_grant():
    build()
    g = grant("admin", "propose")
    out = as_carla("task_schedule", **MOTION, at=fake_chain.now() + DAY)
    assert tasks.tie(Task[out["task"]]) == g and Proposal.count() == 0
    later(DAY)
    fire_timers()
    [p] = rows(Proposal)
    assert (p.proposer, p.filed_under) == ("carla", g) and Task[out["task"]].status == "completed"
    [e] = events("proposal_create")
    assert (e.user, e.by, e.origin, e.grant) == ("carla", "claude-conn", "task", g)


def test_a_repeating_schedule_of_a_tied_task_is_armed_again_after_an_upgrade():
    build()
    g = grant("admin", "propose")
    made("motions", [MOTION])
    as_carla("task_add_schedule", task="motions", run_at=fake_chain.now() + DAY, repeat_every=DAY)
    fake_chain.state["timers"] = []
    setup.start()  # an upgrade drops the timers
    later(DAY)
    fire_timers()
    assert [p.filed_under for p in rows(Proposal)] == [g]


@pytest.mark.parametrize("ending, reason", [("revoked", "revoked or replaced"), ("replaced", "revoked or replaced"),
                                            ("expired", "expired")])
def test_a_tied_task_fails_at_its_next_step_once_its_grant_has_ended(ending, reason):
    build()
    g = grant("admin", "propose")
    made("motions", [{**MOTION, "wait": 3600}, MOTION])
    assert as_carla("task_run", task="motions")["status"] == "waiting" and Proposal.count() == 1
    end(ending, g)
    later(3600)
    fire_timers()
    assert Task["motions"].status == "failed" and Proposal.count() == 1
    run = run_of("motions")
    assert run["status"] == "failed" and reason in run["result"]


@pytest.mark.parametrize("ending", ("revoked", "replaced", "expired"))
def test_a_tied_task_whose_grant_has_ended_fires_no_more(ending):
    build()
    g = grant("admin", "propose")
    made("motions", [MOTION])
    as_carla("task_add_schedule", task="motions", run_at=fake_chain.now() + DAY, repeat_every=DAY)
    later(DAY)
    fire_timers()
    assert Proposal.count() == 1 and not TaskSchedule["motions@1"].disabled
    end(ending, g)
    later(DAY)
    fire_timers()
    assert Task["motions"].status == "failed" and TaskSchedule["motions@1"].disabled
    runs = TaskExecution.count()
    for _ in range(3):
        later(DAY)
        fire_timers()
    assert TaskExecution.count() == runs and Proposal.count() == 1
    fake_chain.state["timers"] = []
    setup.start()  # nor is it armed again after an upgrade
    assert fake_chain.state["timers"] == []


def test_a_scheduled_task_fails_when_its_grant_ended_before_its_time():
    build()
    g = grant("admin", "propose")
    out = as_carla("task_schedule", **MOTION, at=fake_chain.now() + DAY)
    end("revoked", g)
    later(DAY)
    fire_timers()
    assert Task[out["task"]].status == "failed" and Proposal.count() == 0
    assert "revoked or replaced" in run_of(out["task"])["result"]


def test_a_task_made_by_the_member_runs_as_before_whatever_grant_they_gave():
    build()
    g = grant("admin", "propose")
    ok("task_create", "carla", name="rename", steps=[RENAME])
    assert tasks.tie(Task["rename"]) == ""
    end("revoked", g)
    assert ok("task_run", "carla", task="rename")["status"] == "completed"
    assert User["carla"].nickname == "Carla II"
    [e] = events("profile_update")
    assert (e.user, e.by, e.origin, e.grant) == ("carla", "carla", "task", None)


@pytest.mark.parametrize("step, reason", [(RENAME, "does not cover"), (ADD_ACCOUNT, "asks the member each time")])
def test_a_tied_task_cannot_reach_what_its_grant_does_not_cover_directly(step, reason):
    build()
    grant("admin", "payout")  # the payout group covers the call, but only as a pending action
    made("grab", [step])
    assert as_carla("task_run", task="grab")["status"] == "failed"
    assert reason in run_of("grab")["result"]
    assert User["carla"].nickname == "Carla" and PaymentAccount.count() == 0 and PendingAction.count() == 0


NESTED = {
    "shell": {"op": "shell", "args": {"expr": "ops.payment_account_add(network='ICP', address='aaaaa-bbbbb', symbol='REALM')"}},
    "extension": {"op": "extension_call", "args": {"extension": "assistant", "function": "add_account",
                                                    "args": {"address": "aaaaa-bbbbb"}}},
    "shell then extension": {"op": "shell", "args": {
        "expr": "ops.extension_call({'extension': 'assistant', 'function': 'add_account', 'args': {'address': 'aaaaa-bbbbb'}})"}},
}


@pytest.mark.parametrize("step", NESTED.values(), ids=NESTED)
def test_what_a_task_runs_through_the_shell_or_an_extension_asks_the_member_nothing(step):
    """The task is unattended whatever it runs through: it prepares nothing and runs only what its grant covers directly."""
    build()
    grant("admin", "payout")
    made("nested", [step])
    as_carla("task_run", task="nested")
    assert "asks the member each time" in run_of("nested")["result"]
    assert PendingAction.count() == 0 and PaymentAccount.count() == 0


def test_a_delegate_cannot_run_what_the_member_wrote_with_the_members_authority():
    """Else it would plant steps in the member's own task, or run them, and the steps would run as the member."""
    build()
    grant("admin", "propose")
    ok("task_create", "carla", name="rename", steps=[RENAME])
    ok("task_schedule", "carla", **RENAME, at=fake_chain.now() + DAY)
    mine = next(t for t in rows(Task) if t.name.startswith("TASK-"))
    steps = [{"op": "proposal_create", "args": {"title": "Planted"}}]
    for name, args in (("task_run", {}), ("task_edit", {"steps": steps}), ("task_add_schedule", {}),
                       ("task_cancel", {}), ("task_delete", {})):
        assert "was not made under this power of attorney" in denied(
            name, "claude-conn", on_behalf_of="carla", task="rename", **args), name
    assert "was not made under this power of attorney" in denied(
        "task_reschedule", "claude-conn", on_behalf_of="carla", task=mine.name, at=fake_chain.now() + 2 * DAY)
    assert Task["rename"].status == "pending" and "Planted" not in Task["rename"].steps[0].action


def test_a_delegate_edits_and_runs_the_tasks_it_made_and_the_member_makes_them_their_own_by_editing():
    build()
    g = grant("admin", "propose")
    made("motions", [MOTION])
    ok("task_edit", "claude-conn", on_behalf_of="carla", task="motions", steps=[{**MOTION, "args": {"title": "Park"}}])
    assert tasks.tie(Task["motions"]) == g
    assert as_carla("task_run", task="motions")["status"] == "completed"
    ok("task_edit", "carla", task="motions", steps=[RENAME])  # the steps are carla's now: they are not held to the grant
    assert tasks.tie(Task["motions"]) == ""
    assert ok("task_run", "carla", task="motions")["status"] == "completed" and User["carla"].nickname == "Carla II"


def test_a_tied_task_step_that_names_another_grantor_acts_for_the_creator():
    build()
    grant("admin", "vote")
    grant("vote", grantor="bob")
    p = ok("proposal_create", "dana", title="Park")["proposal"]
    made("vote", [{"op": "proposal_vote", "args": {"proposal": p, "choice": "no", "on_behalf_of": "bob"}}])
    assert as_carla("task_run", task="vote")["status"] == "completed"
    assert Vote[f"{p}:bob"] is None and Vote[f"{p}:carla"].choice == "no"


def test_a_grant_that_was_replaced_cannot_act_on_the_tasks_of_the_one_before():
    build()
    g = grant("admin", "propose")
    made("motions", [MOTION])
    end("replaced", g)
    assert "was not made under this power of attorney" in denied(
        "task_run", "claude-conn", on_behalf_of="carla", task="motions")


def test_a_task_step_of_a_task_a_delegate_made_can_make_a_task_that_is_tied_too():
    build()
    g = grant("admin", "propose")
    inner = {"op": "task_create", "args": {"name": "inner", "steps": [MOTION]}}
    made("outer", [inner])
    assert as_carla("task_run", task="outer")["status"] == "completed"
    assert tasks.tie(Task["inner"]) == g and tasks.creator(Task["inner"]) == "carla"


def test_a_task_the_delegate_made_is_listed_with_its_grant():
    build()
    g = grant("admin", "propose")
    made("motions", [MOTION])
    ok("task_create", "carla", name="rename", steps=[RENAME])
    listed = {t["name"]: t["grant"] for t in ok("task_list", "carla")["tasks"]}
    assert listed == {"motions": g, "rename": ""}


def test_an_old_grant_covers_none_of_the_operations_that_run_others():
    build()
    Delegation(id="D-old", grantor="carla", delegate="claude-conn", status="active", expires_at=fake_chain.now() + DAY,
               scope={"operations": ["task.create", "task.run", "shell.execute", "extension.call"]})
    calls = (("task_create", dict(name="t", steps=[MOTION])), ("task_run", dict(task="t")), ("shell", dict(expr="1")),
             ("extension_call", dict(extension="assistant", function="vote", args={})))
    for name, args in calls:
        assert "does not cover" in denied(name, "claude-conn", on_behalf_of="carla", **args), name


# ---- The shell


def test_a_shell_expression_under_a_grant_calls_operations_as_the_delegate_for_the_member():
    build()
    g = grant("admin", "vote")
    p = ok("proposal_create", "bob", title="Park")["proposal"]
    out = as_carla("shell", expr=f'ops.proposal_vote(proposal="{p}", choice="yes")')["result"]
    assert out["ok"] and (Vote[f"{p}:carla"].voter, Vote[f"{p}:carla"].cast_by) == ("carla", "claude-conn")
    [e] = events("proposal_vote")
    assert (e.user, e.by, e.origin, e.grant) == ("carla", "claude-conn", "shell", g)


def test_a_shell_expression_under_a_grant_cannot_reach_an_operation_outside_its_groups():
    build()
    grant("admin", "vote")
    out = as_carla("shell", expr='ops.profile_update(nickname="Carla II")')["result"]
    assert not out["ok"] and "does not cover" in out["error"] and User["carla"].nickname == "Carla"


def test_a_shell_expression_cannot_claim_to_act_for_someone_else():
    build()
    grant("admin", "vote")
    grant("vote", grantor="bob")
    p = ok("proposal_create", "dana", title="Park")["proposal"]
    out = as_carla("shell", expr=f'ops.proposal_vote(proposal="{p}", choice="no", on_behalf_of="bob")')["result"]
    assert out["ok"] and Vote[f"{p}:bob"] is None and Vote[f"{p}:carla"].choice == "no"


def test_a_shell_expression_under_a_grant_files_a_call_that_asks_each_time_and_runs_nothing():
    build()
    g = grant("admin", "payout")
    out = as_carla("shell", expr='ops.payment_account_add(network="ICP", address="aaaaa-bbbbb", symbol="REALM")')["result"]
    assert out["ok"] and out["pending"].startswith("AC-") and PaymentAccount.count() == 0
    [e] = events("payment_account_add")
    assert (e.user, e.by, e.origin, e.grant, e.result) == ("carla", "claude-conn", "shell", g, {"pending": out["pending"]})
    assert PendingAction[out["pending"]].grantor == "carla"
    ok("action_approve", "carla", action=out["pending"], hash=PendingAction[out["pending"]].hash)
    assert PaymentAccount.count() == 1


def test_a_shell_expression_of_the_member_alone_runs_as_before():
    build()
    out = ok("shell", "carla", expr='ops.profile_update(nickname="Carla II")')["result"]
    assert out["ok"] and User["carla"].nickname == "Carla II"
    [e] = events("profile_update")
    assert (e.user, e.by, e.origin, e.grant) == ("carla", "carla", "shell", None)


def test_a_governed_call_from_the_shell_or_a_task_is_a_proposal_tied_to_the_grant():
    """The department would decide it alone for carla, but a delegate's governed call is always a proposal."""
    finance_for_carla()
    world.profile("console", *CONSOLE)
    world.person("carla", "console")
    g = grant("admin", "money")
    transfer = dict(fund="ROADS", to="buildco", amount=10)
    out = as_carla("shell", expr="ops.transfer_create(fund='ROADS', to='buildco', amount=10)")["result"]
    assert out["ok"] and out["requires_vote"] and Proposal[out["proposal"]].filed_under == g
    made("pay", [{"op": "transfer_create", "args": transfer}])
    assert as_carla("task_run", task="pay")["status"] == "completed"
    assert [p.filed_under for p in rows(Proposal)] == [g, g] and Transfer.count() == 0


# ---- Extension calls


def extension(function, caller="claude-conn", **args):
    """What the extension's ``function`` replies when ``caller`` runs it: for alice, if it is her delegate."""
    extra = {"on_behalf_of": "alice"} if caller == "claude-conn" else {}
    out = call("extension_call", caller, extension="assistant", function=function, args=args, **extra)
    assert out["ok"], out
    return out["result"]


def test_an_extension_call_under_a_grant_calls_its_operations_as_the_delegate_for_the_member():
    build()
    g = grant("vote", grantor="alice")
    p = ok("proposal_create", "bob", title="Park")["proposal"]
    out = call("extension_call", "claude-conn", on_behalf_of="alice", extension="assistant", function="vote",
               args={"proposal": p})
    assert out["ok"] and out["result"]["ok"]
    assert (Vote[f"{p}:alice"].voter, Vote[f"{p}:alice"].cast_by) == ("alice", "claude-conn")
    [e] = events("proposal_vote")
    assert (e.user, e.by, e.origin, e.grant) == ("alice", "claude-conn", "extension", g)
    [outer] = events("extension_call")
    assert (outer.user, outer.by, outer.origin, outer.grant) == ("alice", "claude-conn", "user", g)


def test_an_extension_cannot_reach_an_operation_outside_the_groups_of_the_grant():
    build()
    grant("vote", grantor="alice")
    out = extension("rename", nickname="Alice II")
    assert not out["ok"] and "does not cover" in out["error"] and User["alice"].nickname == "Alice"


def test_an_extension_cannot_reach_an_operation_it_did_not_declare_even_in_the_groups_of_the_grant():
    build()
    grant("vote", "propose", "identity", grantor="alice")
    for out in (extension("motion", title="Park"), extension("prove_identity")):
        assert not out["ok"] and "not declared by this extension" in out["error"], out
    assert Proposal.count() == 0 and PendingAction.count() == 0


def test_an_extension_call_that_asks_each_time_is_filed_and_runs_nothing():
    build()
    g = grant("payout", grantor="alice")
    out = extension("add_account", address="aaaaa-bbbbb")
    assert out["ok"] and out["pending"].startswith("AC-") and PaymentAccount.count() == 0
    [e] = events("payment_account_add")
    assert (e.user, e.by, e.origin, e.grant) == ("alice", "claude-conn", "extension", g)
    ok("action_approve", "alice", action=out["pending"], hash=PendingAction[out["pending"]].hash)
    assert PaymentAccount.count() == 1


def test_an_extension_cannot_claim_to_act_for_someone_else():
    build()
    grant("vote", grantor="alice")
    grant("vote", grantor="bob")
    p = ok("proposal_create", "dana", title="Park")["proposal"]
    assert extension("vote_for", proposal=p, who="bob")["ok"]
    assert Vote[f"{p}:bob"] is None and Vote[f"{p}:alice"].choice == "no"


def test_an_extension_a_member_calls_acts_for_that_member_whoever_it_names():
    """Bob holds Alice's grants. An extension he calls has none to lend: it votes for him, and files nothing for her."""
    build()
    ok("delegation_accept", "bob", delegation=grant("vote", "payout", grantor="alice", delegate="bob"))
    p = ok("proposal_create", "dana", title="Park")["proposal"]
    assert extension("vote_for", "bob", proposal=p, who="alice")["ok"]
    assert Vote[f"{p}:alice"] is None and Vote[f"{p}:bob"].choice == "no"
    assert extension("add_account_for", "bob", address="evil", who="alice")["ok"]
    assert PendingAction.count() == 0 and {a.owner for a in rows(PaymentAccount)} == {"User:bob"}
    [e] = events("payment_account_add")
    assert (e.user, e.by, e.origin, e.grant) == ("bob", "bob", "extension", None)


def test_an_extension_call_of_the_member_alone_runs_as_before():
    build()
    p = ok("proposal_create", "bob", title="Park")["proposal"]
    assert ok("extension_call", "alice", extension="assistant", function="vote", args={"proposal": p})["result"]["ok"]
    [e] = events("proposal_vote")
    assert (e.user, e.by, e.origin, e.grant) == ("alice", "alice", "extension", None)


def test_every_grant_by_groups_reaches_extension_call_for_as_long_as_the_grant_may_last(monkeypatch):
    build()
    spec = op.OPS["extension_call"]
    assert poa.group_of("extension_call") is None
    g = Delegation[grant("profile", grantor="alice", days=200)]
    later(100 * DAY)
    assert poa.covered(g, "extension_call", spec, "extension.call")
    ok("delegation_revoke", "alice", delegation=g.id)
    g = Delegation[grant("*", grantor="alice", days=90)]
    monkeypatch.setitem(poa.EVERYTHING, "longest", 30 * DAY)
    later(31 * DAY)
    assert not poa.covered(g, "extension_call", spec, "extension.call")


def test_no_group_lists_extension_call_and_the_admin_group_lists_the_tasks_and_the_shell():
    build()
    groups = {g["id"]: {o["operation"] for o in g["operations"]} for g in ok("delegation_groups", "carla")["groups"]}
    assert "extension_call" not in set().union(*groups.values())
    assert groups["admin"] == {"shell", "task_add_schedule", "task_cancel", "task_create", "task_delete", "task_edit",
                               "task_list", "task_logs", "task_reschedule", "task_run", "task_schedule"}
