import json

from core import chain
from core.db import Fail, each, find, view
from ggg import Task, TaskExecution, TaskSchedule, TaskStep
from core.op import dispatch, op, ops

BUSY = ("running", "waiting")


def creator(task):
    return json.loads(task.metadata)["creator"]


def ordered(task):
    return sorted(task.steps, key=lambda s: int(s._id))


def checked(steps):
    """Steps as ``(op, args, wait)``: an operation, its arguments and the seconds to wait before the next step."""
    for s in steps:
        if s.get("op") not in ops():
            raise Fail(f"unknown operation '{s.get('op')}'")
    return [(s["op"], s.get("args") or {}, int(s.get("wait", 0))) for s in steps]


def define(task, steps):
    # The toolkit's TaskStep has no field for an operation call; ic_python_db keeps the extra attribute.
    for name, args, wait in steps:
        TaskStep(task=task, run_next_after=wait, action=json.dumps({"op": name, "args": args}))


def execute(task, start=0, ex=None):
    """Run the steps from ``start`` through ``dispatch`` as the task's creator, pausing where a step waits."""
    ex = ex or TaskExecution(name=f"{task.name}#{len(task.executions) + 1}", task=task, status="running",
                             started_at=chain.now(), result="")
    task.status, steps, out = "running", ordered(task), {}
    for i in range(start, len(steps)):
        call = json.loads(steps[i].action)
        reply = yield from dispatch(call["op"], json.dumps(call["args"]), origin="task", signer=creator(task))
        out = json.loads(reply)
        steps[i].status = "completed" if out["ok"] else "failed"
        if not out["ok"]:
            return finish(task, ex, "failed", out)
        if steps[i].run_next_after and i + 1 < len(steps):
            task.status, task.step_to_execute = "waiting", i + 1
            chain.set_timer(steps[i].run_next_after, lambda name=task.name, n=i + 1, e=ex.name: resume(name, n, e))
            return ex
    return finish(task, ex, "completed", out)


def finish(task, ex, status, out):
    task.status, task.step_to_execute = status, 0
    ex.status, ex.completed_at, ex.result = status, chain.now(), json.dumps(out)[:5000]
    return ex


def resume(name, start, execution):
    task = Task[name]
    if task and task.status == "waiting":
        yield from execute(task, start, TaskExecution[execution])


def arm(schedule):
    chain.set_timer(max(schedule.run_at - chain.now(), 0), lambda name=schedule.name: fire(name))


def fire(name):
    s = TaskSchedule[name]
    if s is None or s.disabled:
        return
    s.last_run_at = chain.now()
    if s.repeat_every:
        s.run_at = s.last_run_at + s.repeat_every
        arm(s)
    if s.task.status not in BUSY + ("cancelled",):
        yield from execute(s.task)


def rearm():
    """Timers do not survive an upgrade: set them again for every active schedule."""
    for s in each(TaskSchedule):
        if not s.disabled:
            arm(s)


@op("task.create")
def task_create(c, args):
    """A task of ``steps``: ``[{op, args, wait}]``, each run as you."""
    steps = checked(args["steps"])
    if Task[args["name"]]:
        raise Fail(f"task '{args['name']}' exists")
    task = Task(name=args["name"], metadata=json.dumps({"creator": c.user}), status="pending")
    define(task, steps)
    return {"task": task.name}


@op("task.edit")
def task_edit(c, args):
    task, steps = find(Task, args["task"]), checked(args["steps"])
    if task.status in BUSY:
        raise Fail(f"task '{task.name}' is {task.status}")
    for s in task.steps:
        s.delete()
    define(task, steps)


@op("task.delete")
def task_delete(c, args):
    task = find(Task, args["task"])
    for row in list(task.steps) + list(task.schedules) + list(task.executions):
        row.delete()
    task.delete()


@op("task.run")
def task_run(c, args):
    task = find(Task, args["task"])
    if task.status in BUSY:
        raise Fail(f"task '{task.name}' is {task.status}")
    ex = yield from execute(task)
    return {"execution": ex.name, "status": task.status}


@op("task.schedule")
def task_schedule(c, args):
    """Run a task at ``run_at`` (default now), then every ``repeat_every`` seconds if set."""
    task = find(Task, args["task"])
    s = TaskSchedule(name=f"{task.name}@{len(task.schedules) + 1}", task=task, disabled=False, last_run_at=0,
                     run_at=int(args.get("run_at") or chain.now()), repeat_every=int(args.get("repeat_every", 0)))
    arm(s)
    return {"schedule": s.name}


@op("task.cancel")
def task_cancel(c, args):
    task = find(Task, args["task"])
    for s in task.schedules:
        s.disabled = True
    task.status = "cancelled"


def summary(task):
    return {"name": task.name, "status": task.status, "creator": creator(task),
            "steps": [{**json.loads(s.action), "wait": s.run_next_after, "status": s.status} for s in ordered(task)],
            "schedules": [view(s, "name", "run_at", "repeat_every", "last_run_at", "disabled") for s in task.schedules]}


@op("task.view", read=True)
def task_list(c, args):
    return {"tasks": [summary(t) for t in each(Task)]}


@op("task.view", read=True)
def task_logs(c, args):
    """A task's runs, newest first (``count``, default 10)."""
    runs = sorted(find(Task, args["task"]).executions, key=lambda e: int(e._id), reverse=True)
    return {"runs": [view(e, "name", "status", "started_at", "completed_at", "result")
                     for e in runs[:int(args.get("count") or 10)]]}
