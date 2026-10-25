import fake_chain
import op
import setup
import world
from ggg import Event, Notification, Task, TaskSchedule
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
    listed = ok("task_list", "ctl")["tasks"][0]
    assert listed["steps"][0]["op"] == "notification_send" and listed["schedules"][0]["repeat_every"] == DAY


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
    ok("task_schedule", "ctl", task="payroll", repeat_every=30 * DAY)
    ok("task_cancel", "ctl", task="payroll")
    fire_timers()
    assert Notification.count() == 0 and TaskSchedule["payroll@1"].disabled
    assert "unknown operation" in call("task_edit", "ctl", task="payroll", steps=[{"op": "nope"}])["error"]
    ok("task_edit", "ctl", task="payroll", steps=CHECK + CHECK)
    ok("task_run", "ctl", task="payroll")
    assert Notification.count() == 2
    assert "exists" in call("task_create", "ctl", name="payroll", steps=[])["error"]
    ok("task_delete", "ctl", task="payroll")
    assert Task["payroll"] is None and TaskSchedule["payroll@1"] is None


def test_rearm_after_upgrade():
    world.build()
    ok("task_create", "ctl", name="payroll", steps=CHECK)
    ok("task_schedule", "ctl", task="payroll", run_at=fake_chain.now() + DAY)
    fake_chain.state["timers"] = []
    setup.start()
    assert len(fake_chain.state["timers"]) == 1


def test_task_permissions():
    world.build()
    ok("task_create", "ctl", name="t", steps=CHECK)
    for name, args in [("task_create", {"name": "x", "steps": CHECK}), ("task_edit", {"task": "t", "steps": CHECK}),
                       ("task_delete", {"task": "t"}), ("task_run", {"task": "t"}), ("task_schedule", {"task": "t"}),
                       ("task_cancel", {"task": "t"}), ("task_list", {}), ("task_logs", {"task": "t"})]:
        denied(name, "alice", **args)
