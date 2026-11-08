import json

import fake_chain
import ggg_methods as g
from api import setup, tasks
import world
from core import op
from ggg import Event, Notification, Task, TaskExecution, TaskSchedule, UserProfile
from kit import call, denied, fire_timers, later, ok

NS = 1_000_000_000

DAY = 86400
CHECK = [{"op": "notification_send", "args": {"recipient": "Department:Finance", "title": "Check overdue invoices"}}]


def test_story_a_daily_invoice_check():
    world.build()
    ok("task_create", "ctl", name="invoice-check", steps=CHECK)
    ok("task_add_schedule", "ctl", task="invoice-check", run_at=fake_chain.now() + 3600, repeat_every=DAY)
    assert fake_chain.state["timers"][0][0] == 3600
    for _ in range(4):
        later(DAY)
        fire_timers()
    assert Notification.count() == 4 and len(fake_chain.state["timers"]) == 1
    runs = ok("task_logs", "ctl", task="invoice-check", count=3)["runs"]
    assert [r["name"] for r in runs] == ["invoice-check#4", "invoice-check#3", "invoice-check#2"]
    assert {r["status"] for r in runs} == {"completed"}
    listed = ok("task_list", "ctl")
    assert listed["next"] is None
    assert listed["tasks"][0]["steps"][0]["op"] == "notification_send"
    assert listed["tasks"][0]["schedules"][0]["repeat_every"] == DAY


def test_steps_run_as_the_creator_and_wait_between():
    world.build()
    world.profile("task-runner", "task.create", "task.run", "task.view", "notification.send")
    world.person("carla", "task-runner")
    steps = [{"op": "notification_send", "args": {"recipient": "User:alice", "title": "Roadworks start"}, "wait": 3600},
             {"op": "governance_update", "args": {"voting_quorum": 0}}]
    ok("task_create", "carla", name="roadworks", steps=steps)
    out = ok("task_run", "carla", task="roadworks")
    assert out["status"] == "waiting" and Notification.count() == 1
    assert Event[str(Event.max_id() - 1)].origin == "task" and Event[str(Event.max_id() - 1)].user == "carla"
    later(3600)
    fire_timers()
    assert Task["roadworks"].status == "failed"
    run = ok("task_logs", "carla", task="roadworks")["runs"][0]
    assert run["status"] == "failed" and "governance.update" in run["result"]


def test_cancel_edit_and_delete():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    assert "unknown operation" in call("task_edit", "ctl", task="payroll", steps=[{"op": "nope"}])["error"]
    ok("task_edit", "ctl", task="payroll", steps=CHECK + CHECK)
    ok("task_run", "ctl", task="payroll")
    assert Notification.count() == 2
    ok("task_add_schedule", "ctl", task="payroll", repeat_every=30 * DAY)
    ok("task_cancel", "ctl", task="payroll")
    fire_timers()
    assert Notification.count() == 2 and TaskSchedule["payroll@1"].disabled
    assert "exists" in call("task_create", "ctl", name="payroll", steps=[])["error"]
    ok("task_delete", "ctl", task="payroll")
    assert Task["payroll"] is None and TaskSchedule["payroll@1"] is None


def test_a_cancelled_task_does_not_run_again():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_cancel", "ctl", task="payroll")
    assert "cancelled" in denied("task_run", "ctl", task="payroll")
    assert "cancelled" in denied("task_add_schedule", "ctl", task="payroll")
    assert Task["payroll"].status == "cancelled" and Notification.count() == 0


def test_only_the_creator_acts_on_a_task():
    world.build()
    world.profile("task-admin", "task.create", "task.edit", "task.run", "task.schedule", "task.cancel",
                  "task.delete", "task.view", "notification.send")
    world.person("carla", "task-admin")
    world.person("mallory", "task-admin")
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_create", "carla", name="roadworks", steps=CHECK)
    grab = [{"op": "governance_update", "args": {"voting_quorum": 0}}]
    assert "not yours" in denied("task_edit", "mallory", task="payroll", steps=grab)
    for name in ("task_run", "task_add_schedule", "task_cancel", "task_delete"):
        assert "not yours" in denied(name, "mallory", task="roadworks")
    assert "notification_send" in Task["payroll"].steps[0].action
    ok("task_edit", "carla", task="roadworks", steps=CHECK + CHECK)
    ok("task_edit", "ctl", task="roadworks", steps=CHECK)
    ok("task_run", "carla", task="roadworks")
    assert Notification.count() == 1


def test_task_list_pages():
    world.build()
    for name in ("a", "b", "c"):
        ok("task_create", "ctl", name=name, steps=CHECK)
    first = ok("task_list", "ctl", count=2)
    second = ok("task_list", "ctl", count=2, **{"from": first["next"]})
    assert [t["name"] for t in first["tasks"] + second["tasks"]] == ["a", "b", "c"] and second["next"] is None


def test_task_logs_pages_newest_first():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    for _ in range(3):
        ok("task_run", "ctl", task="payroll")
    first = ok("task_logs", "ctl", task="payroll", count=2)
    second = ok("task_logs", "ctl", task="payroll", count=2, **{"from": first["next"]})
    assert [r["name"] for r in first["runs"] + second["runs"]] == ["payroll#3", "payroll#2", "payroll#1"]
    assert second["next"] is None


def test_task_delete_in_batches(monkeypatch):
    monkeypatch.setattr(tasks, "PAGE", 2)
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    for _ in range(3):
        ok("task_run", "ctl", task="payroll")
    ok("task_add_schedule", "ctl", task="payroll", run_at=fake_chain.now() + DAY)
    assert ok("task_delete", "ctl", task="payroll")["remaining"] == 1
    assert TaskExecution.count() == 1 and Task["payroll"].status == "cancelled"
    assert "cancelled" in denied("task_run", "ctl", task="payroll")
    assert ok("task_delete", "ctl", task="payroll")["remaining"] == 0
    assert Task["payroll"] is None and TaskExecution.count() == 0 and TaskSchedule.count() == 0


def test_rearm_after_upgrade():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_add_schedule", "ctl", task="payroll", run_at=fake_chain.now() + DAY)
    fake_chain.state["timers"] = []
    setup.start()
    assert len(fake_chain.state["timers"]) == 1


def test_rearm_sets_enabled_schedules_a_page_at_a_time(monkeypatch):
    monkeypatch.setattr(tasks, "PAGE", 2)
    world.build()
    for name in ("a", "b", "c", "d", "e"):
        ok("task_create", "ctl", name=name, steps=CHECK)
    ok("task_add_schedule", "ctl", task="a")
    fire_timers()
    for i, name in enumerate(("b", "c", "d", "e"), 1):
        ok("task_add_schedule", "ctl", task=name, run_at=fake_chain.now() + 100 * i)
    ok("task_cancel", "ctl", task="c")
    assert TaskSchedule["a@1"].disabled
    fake_chain.state["timers"] = []
    setup.start()
    assert [s for s, _ in fake_chain.state["timers"]] == [100, 300, 0]
    rest = fake_chain.state["timers"].pop()[1]
    rest()
    assert [s for s, _ in fake_chain.state["timers"]] == [100, 300, 400]


def test_task_permissions():
    world.build()
    ok("task_create", "ctl", name="t", steps=CHECK)
    for name, args in [("task_create", {"name": "x", "steps": CHECK}), ("task_edit", {"task": "t", "steps": CHECK}),
                       ("task_delete", {"task": "t"}), ("task_run", {"task": "t"}), ("task_add_schedule", {"task": "t"}),
                       ("task_cancel", {"task": "t"}), ("task_list", {}), ("task_logs", {"task": "t"})]:
        denied(name, "alice", **args)


# --- GGG's tasks: one operation at a time ----------------------------------------------------------

LAPSE = {"recipient": "User:alice", "title": "Your registration has lapsed"}


def clerk():
    """Carla may schedule tasks and send notifications."""
    world.build()
    world.profile("clerk", "task.schedule", "notification.send")
    world.person("carla", "clerk")


def lapse(at, who="carla", **more):
    return ok("task_schedule", who, op="notification_send", args=LAPSE, at=at, **more)["task"]


def as_ggg(name):
    fake_chain.state["caller"] = "ctl"
    page = g.ggg_task_list({"cursor": None, "limit": None, "op": None, "status": None})["Ok"]["items"]
    return next(t for t in page if t["id"] == name)


def test_story_a_member_schedules_a_task_that_runs_once_with_their_authority():
    clerk()
    at = fake_chain.now() + 3600
    name = lapse(at, reason="the registration lapses")
    assert name == "TASK-1" and Task[name].status == "pending" and Notification.count() == 0
    assert (tasks.creator(Task[name]), TaskSchedule[f"{name}@1"].run_at, TaskSchedule[f"{name}@1"].repeat_every) == ("carla", at, 0)
    later(3600)
    fire_timers()
    assert Notification.count() == 1 and Task[name].status == "completed"
    event = Event[str(Event.max_id())]
    assert (event.op, event.origin, event.user) == ("notification_send", "task", "carla")
    task = as_ggg(name)
    assert (task["op"], task["status"]) == ("notification_send", {"Run": None})
    assert (task["at"], task["reason"]) == (at * NS, "the registration lapses")
    assert task["scheduled_by"] == {"kind": "User", "id": "carla"} and task["event"] == event.id
    assert task["args"] == {"Map": [("recipient", {"Text": "User:alice"}), ("title", {"Text": LAPSE["title"]})]}
    later(3600)
    fire_timers()
    assert Notification.count() == 1  # it runs once


def test_a_task_is_refused_at_its_time_by_the_guard_of_that_day_and_ends_failed():
    clerk()
    name = lapse(fake_chain.now() + 3600)
    UserProfile["clerk"].allowed_to = ["task.schedule"]  # Carla may no longer send notifications
    later(3600)
    fire_timers()
    assert Task[name].status == "failed" and Notification.count() == 0
    run = ok("task_logs", "ctl", task=name)["runs"][0]
    assert run["status"] == "failed" and "notification.send" in run["result"]
    task = as_ggg(name)
    assert (task["status"], task["event"]) == ({"Failed": None}, None)


def test_a_task_runs_as_the_user_who_scheduled_it_not_as_the_realm():
    clerk()
    world.person("alice", "clerk")
    mine = lapse(fake_chain.now() + 10, who="alice")
    args = {"voting_quorum": 0}  # carla may not change the voting rules: nobody who schedules this task may
    other = ok("task_schedule", "carla", op="governance_update", args=args, at=fake_chain.now() + 10)["task"]
    later(10)
    fire_timers()
    assert (Task[mine].status, Task[other].status) == ("completed", "failed")
    assert Event[str(Event.max_id())].user == "alice"


def test_task_schedule_needs_the_permission_a_known_operation_and_object_arguments():
    clerk()
    at = fake_chain.now() + 10
    assert "needs task.schedule" in denied("task_schedule", "alice", op="notification_send", args=LAPSE, at=at)
    assert "unknown operation" in denied("task_schedule", "carla", op="no_such_op", args={}, at=at)
    assert call("task_schedule", "carla", op="notification_send", args=[1], at=at)["kind"] == "invalid_argument"
    assert Task.count() == 0


def test_a_task_is_scheduled_by_a_user_calling_for_themselves_and_by_no_codex_extension_or_replay():
    clerk()
    args = json.dumps({"op": "notification_send", "args": LAPSE, "at": fake_chain.now() + 10})
    callers = (("codex", fake_chain.self_id()), ("extension", "carla"), ("replay", "carla"), ("task", "carla"))
    for origin, signer in callers:
        out = json.loads(op.run(op.dispatch("task_schedule", args, origin=origin, signer=signer,
                                            declared=("task_schedule",))))
        assert out["kind"] == "invalid_argument" and f"not by a {origin}" in out["error"], origin
    assert Task.count() == 0 and fake_chain.state["timers"] == []
    assert lapse(fake_chain.now() + 10) == "TASK-1"


def test_a_task_runs_a_change_and_not_a_read():
    clerk()
    out = call("task_schedule", "carla", op="notification_list", args={}, at=fake_chain.now() + 10)
    assert out["kind"] == "invalid_argument" and "only reads" in out["error"] and Task.count() == 0


def test_the_task_list_shows_the_steps_without_their_private_arguments_and_secrets():
    clerk()
    world.profile("monitor", "realm.monitor")
    world.person("dana", "monitor")
    at = fake_chain.now() + 100
    ok("task_schedule", "carla", op="attribute_submit", at=at,
       args={"kind": "disability", "value": "65 %", "ref": "vault:certificate"})
    ok("task_schedule", "carla", op="private_data_update", args={"private_data": "0a0b"}, at=at)
    shown = [t["steps"][0]["args"] for t in ok("task_list", "dana")["tasks"]]
    assert shown == [{"ref": "vault:certificate"}, {}]
    assert "disability" not in json.dumps(ok("task_list", "ctl")) and "0a0b" not in json.dumps(ok("task_list", "ctl"))
    listed = [t["args"] for t in g.ggg_task_list({"cursor": None, "limit": None, "op": None, "status": None})["Ok"]["items"]]
    assert listed == [{"Map": [("ref", {"Text": "vault:certificate"})]}, {"Map": []}]
    assert "disability" in Task["TASK-1"].steps[0].action  # the task itself keeps what it will run


def test_a_named_task_that_has_run_can_still_be_cancelled():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_run", "ctl", task="payroll")
    assert Task["payroll"].status == "completed"
    ok("task_cancel", "ctl", task="payroll")
    assert Task["payroll"].status == "cancelled"


def test_a_task_scheduled_for_a_time_that_has_passed_runs_at_once():
    clerk()
    lapse(fake_chain.now() - 5)
    assert fake_chain.state["timers"][0][0] == 0
    fire_timers()
    assert Notification.count() == 1


def fire_earliest_timer():
    """Fire only the timer with the shortest delay, as the replica does when just that delay has passed."""
    fake_chain.state["timers"].sort(key=lambda timer: timer[0])
    _, callback = fake_chain.state["timers"].pop(0)
    out = callback()
    if out is not None:
        op.run(out)


def test_a_rescheduled_task_runs_at_its_new_time_and_not_at_the_old_one():
    clerk()
    now = fake_chain.now()
    name = lapse(now + 3600)
    assert ok("task_reschedule", "carla", task=name, at=now + 7200)["ok"]
    assert TaskSchedule[f"{name}@1"].run_at == now + 7200
    later(3600)
    fire_earliest_timer()  # the first timer is too early now
    assert Notification.count() == 0 and Task[name].status == "pending"
    later(3600)
    fire_timers()
    assert Notification.count() == 1 and Task[name].status == "completed"
    assert call("task_reschedule", "carla", task=name, at=now + 9000)["kind"] == "conflict"


def test_a_task_moves_earlier_and_still_runs_once():
    clerk()
    now = fake_chain.now()
    name = lapse(now + 7200)
    ok("task_reschedule", "carla", task=name, at=now + 600)
    later(7200)
    fire_timers()
    assert Notification.count() == 1


def test_only_the_scheduler_or_a_controller_moves_or_cancels_a_task():
    clerk()
    world.person("mallory", "clerk")
    name = lapse(fake_chain.now() + 100)
    for op_name, args in (("task_reschedule", {"at": fake_chain.now() + 5}), ("task_cancel", {})):
        assert "not yours" in denied(op_name, "mallory", task=name, **args)
    assert ok("task_reschedule", "ctl", task=name, at=fake_chain.now() + 5)["ok"]
    assert "needs task.schedule" in denied("task_cancel", "alice", task=name)


def test_a_cancelled_task_never_runs_and_one_that_ran_cannot_be_cancelled():
    clerk()
    gone, done = lapse(fake_chain.now() + 100), lapse(fake_chain.now() + 100)
    ok("task_cancel", "carla", task=gone)
    assert as_ggg(gone)["status"] == {"Cancelled": None}
    assert call("task_reschedule", "carla", task=gone, at=fake_chain.now() + 5)["kind"] == "conflict"
    later(100)
    fire_timers()
    assert Notification.count() == 1 and Task[done].status == "completed"
    assert call("task_cancel", "carla", task=done)["kind"] == "conflict"
    assert as_ggg(done)["status"] == {"Run": None}


def test_task_list_is_for_the_monitor_and_filters_by_operation_and_status():
    clerk()
    world.profile("monitor", "realm.monitor")
    world.person("dana", "monitor")
    first, second = lapse(fake_chain.now() + 100), lapse(fake_chain.now() + 200)
    other = ok("task_schedule", "carla", op="governance_update", args={}, at=fake_chain.now() + 300)["task"]
    ok("task_cancel", "carla", task=second)
    assert "needs realm.monitor" in denied("task_list", "carla")

    def names(**args):
        return [t["name"] for t in ok("task_list", "dana", **args)["tasks"]]

    assert names() == [first, second, other]
    assert names(op="governance_update") == [other]
    assert names(status="scheduled") == [first, other] and names(status="cancelled") == [second]
    assert names(status="run") == []
    page = ok("task_list", "dana", count=2, op="notification_send")
    assert [t["name"] for t in page["tasks"]] == [first, second] and page["next"] == 3


def test_the_ggg_methods_schedule_reschedule_and_cancel():
    clerk()
    fake_chain.state["caller"] = "carla"
    at = fake_chain.now() + 100
    value = {"Map": [("recipient", {"Text": "User:alice"}), ("title", {"Text": "Lapsed"})]}
    made = op.run(g.ggg_task_schedule({"op": "notification_send", "args": value, "at": at * NS, "reason": None}))["Ok"]
    assert made["id"] == "TASK-1" and made["status"] == {"Scheduled": None} and made["reason"] is None
    assert made["event"] is None and made["scheduled_by"] == {"kind": "User", "id": "carla"} and made["at"] == at * NS
    moved = op.run(g.ggg_task_reschedule({"task": "TASK-1", "at": (at + 50) * NS}))["Ok"]
    assert moved["at"] == (at + 50) * NS
    assert op.run(g.ggg_task_cancel("TASK-1"))["Ok"]["status"] == {"Cancelled": None}
    assert op.run(g.ggg_task_cancel("TASK-9"))["Err"] == {"NotFound": None}
    assert list(op.run(g.ggg_task_schedule({"op": "no_such_op", "args": value, "at": at * NS, "reason": None}))["Err"]) \
        == ["InvalidArgument"]


def test_a_proposals_run_is_a_task_of_the_realm():
    from test_effective_at import scheduled_proposal

    world.build()
    p = scheduled_proposal()
    task = as_ggg(f"proposal {p}")
    assert (task["op"], task["status"], task["scheduled_by"]) == ("proposal_run_scheduled", {"Scheduled": None},
                                                                  {"kind": "Realm", "id": "1"})


def test_the_tasks_of_the_scheduler_are_armed_again_after_an_upgrade():
    clerk()
    name = lapse(fake_chain.now() + 100)
    fake_chain.state["timers"] = []
    setup.start()
    assert len(fake_chain.state["timers"]) == 1
    later(100)
    fire_timers()
    assert Task[name].status == "completed"


def test_task_names_that_task_schedule_gives_are_not_for_task_create():
    world.build()
    assert "named by task_schedule" in denied("task_create", "ctl", name="TASK-7", steps=[])
