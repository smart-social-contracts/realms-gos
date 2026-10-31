import fake_chain
from core import op
from api import setup, tasks
import world
from ggg import Event, Notification, Task, TaskExecution, TaskSchedule
from kit import call, denied, later, ok

DAY = 86400
CHECK = [{"op": "notification_send", "args": {"recipient": "Department:Finance", "title": "Check overdue invoices"}}]


def fire_timers():
    timers, fake_chain.state["timers"] = fake_chain.state["timers"], []
    for _, cb in timers:
        out = cb()
        if out is not None:
            op.run(out)


def test_story_a_daily_invoice_check():
    world.build()
    ok("task_create", "ctl", name="invoice-check", steps=CHECK)
    ok("task_schedule", "ctl", task="invoice-check", run_at=fake_chain.now() + 3600, repeat_every=DAY)
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
             {"op": "governance_update", "args": {"quorum_percent": 0}}]
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
    ok("task_schedule", "ctl", task="payroll", repeat_every=30 * DAY)
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
    assert "cancelled" in denied("task_schedule", "ctl", task="payroll")
    assert Task["payroll"].status == "cancelled" and Notification.count() == 0


def test_only_the_creator_acts_on_a_task():
    world.build()
    world.profile("task-admin", "task.create", "task.edit", "task.run", "task.schedule", "task.cancel",
                  "task.delete", "task.view", "notification.send")
    world.person("carla", "task-admin")
    world.person("mallory", "task-admin")
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_create", "carla", name="roadworks", steps=CHECK)
    grab = [{"op": "governance_update", "args": {"quorum_percent": 0}}]
    assert "not yours" in denied("task_edit", "mallory", task="payroll", steps=grab)
    for name in ("task_run", "task_schedule", "task_cancel", "task_delete"):
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
    ok("task_schedule", "ctl", task="payroll", run_at=fake_chain.now() + DAY)
    assert ok("task_delete", "ctl", task="payroll")["remaining"] == 1
    assert TaskExecution.count() == 1 and Task["payroll"].status == "cancelled"
    assert "cancelled" in denied("task_run", "ctl", task="payroll")
    assert ok("task_delete", "ctl", task="payroll")["remaining"] == 0
    assert Task["payroll"] is None and TaskExecution.count() == 0 and TaskSchedule.count() == 0


def test_rearm_after_upgrade():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_schedule", "ctl", task="payroll", run_at=fake_chain.now() + DAY)
    fake_chain.state["timers"] = []
    setup.start()
    assert len(fake_chain.state["timers"]) == 1


def test_rearm_sets_enabled_schedules_a_page_at_a_time(monkeypatch):
    monkeypatch.setattr(tasks, "PAGE", 2)
    world.build()
    for name in ("a", "b", "c", "d", "e"):
        ok("task_create", "ctl", name=name, steps=CHECK)
    ok("task_schedule", "ctl", task="a")
    fire_timers()
    for i, name in enumerate(("b", "c", "d", "e"), 1):
        ok("task_schedule", "ctl", task=name, run_at=fake_chain.now() + 100 * i)
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
                       ("task_delete", {"task": "t"}), ("task_run", {"task": "t"}), ("task_schedule", {"task": "t"}),
                       ("task_cancel", {"task": "t"}), ("task_list", {}), ("task_logs", {"task": "t"})]:
        denied(name, "alice", **args)
