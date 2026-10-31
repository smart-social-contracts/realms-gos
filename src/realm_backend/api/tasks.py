import json

from core import auth
from core import chain
from core.db import PAGE, PAGE_MAX, Conflict, Fail, Forbidden, find, page, view
from ggg import Task, TaskExecution, TaskSchedule, TaskStep
from core.op import dispatch, op, ops

BUSY = ("running", "waiting")
# The toolkit's TaskSchedule declares no indexed field, so the enabled schedules are kept in this field index.
ENABLED = (TaskSchedule.get_full_type_name(), "enabled", "1")


def creator(task):
    return json.loads(task.metadata)["creator"]


def owned(c, args):
    """``task``, if you created it or hold ``all``: its steps run as its creator."""
    task = find(Task, args["task"])
    if c.user != creator(task) and "all" not in auth.permissions(c.user):
        raise Forbidden(f"task '{task.name}' is not yours")
    return task


def children(task, relation):
    """Ids of the task's ``relation`` rows, oldest first, read from the relation's index without loading the rows."""
    return sorted(int(i) for i in task.db().reverse_index_get(task._type, task._id, relation))


def enable(schedule, on):
    schedule.disabled = not on
    db = schedule.db()
    (db.field_index_add if on else db.field_index_remove)(*ENABLED, schedule._id)


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
    ex = ex or TaskExecution(name=f"{task.name}#{len(children(task, 'executions')) + 1}", task=task, status="running",
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
    else:
        enable(s, False)
    if s.task.status not in BUSY + ("cancelled",):
        yield from execute(s.task)


def rearm(start=1):
    """Timers do not survive an upgrade: set them again for the enabled schedules, a page per message."""
    ids = sorted(i for i in map(int, TaskSchedule.db().field_index_get(*ENABLED)) if i >= start)
    for i in ids[:PAGE]:
        arm(TaskSchedule.load(str(i)))
    if len(ids) > PAGE:
        chain.set_timer(0, lambda n=ids[PAGE]: rearm(n))


def stop(task):
    for s in task.schedules:
        enable(s, False)
    task.status = "cancelled"


@op("task.create")
def task_create(c, args):
    """Create task ``name`` of ``steps``: ``[{op, args, wait}]``, each run as you, ``wait`` seconds before the next.
    Returns ``task``."""
    steps = checked(args["steps"])
    if Task[args["name"]]:
        raise Conflict(f"task '{args['name']}' exists")
    task = Task(name=args["name"], metadata=json.dumps({"creator": c.user}), status="pending")
    define(task, steps)
    return {"task": task.name}


@op("task.edit")
def task_edit(c, args):
    """Replace the steps of your ``task`` (any task if you hold all) with ``steps`` (``[{op, args, wait}]``), unless
    it is running or waiting."""
    task, steps = owned(c, args), checked(args["steps"])
    if task.status in BUSY:
        raise Conflict(f"task '{task.name}' is {task.status}")
    for s in task.steps:
        s.delete()
    define(task, steps)


@op("task.delete")
def task_delete(c, args):
    """Cancel and delete your ``task`` (any task if you hold all) with its steps, schedules and run history, at most
    100 runs per call. Returns ``remaining``, the runs still to delete: call again until it is 0 and the task is gone."""
    task = owned(c, args)
    stop(task)
    runs = children(task, "executions")
    for i in runs[:PAGE]:
        TaskExecution.load(str(i)).delete()
    if len(runs) > PAGE:
        return {"remaining": len(runs) - PAGE}
    for row in list(task.steps) + list(task.schedules):
        row.delete()
    task.delete()
    return {"remaining": 0}


@op("task.run")
def task_run(c, args):
    """Run your ``task`` (any task if you hold all) now, steps as its creator; a cancelled task does not run again.
    Returns ``execution`` and ``status`` (completed, failed or waiting)."""
    task = owned(c, args)
    if task.status in BUSY + ("cancelled",):
        raise Conflict(f"task '{task.name}' is {task.status}")
    ex = yield from execute(task)
    return {"execution": ex.name, "status": task.status}


@op("task.schedule")
def task_schedule(c, args):
    """Run your ``task`` (any task if you hold all; not a cancelled one) at ``run_at`` (default now), then every
    ``repeat_every`` seconds if set. Returns ``schedule``."""
    task = owned(c, args)
    if task.status == "cancelled":
        raise Conflict(f"task '{task.name}' is cancelled")
    s = TaskSchedule(name=f"{task.name}@{len(children(task, 'schedules')) + 1}", task=task, last_run_at=0,
                     run_at=int(args.get("run_at") or chain.now()), repeat_every=int(args.get("repeat_every", 0)))
    enable(s, True)
    arm(s)
    return {"schedule": s.name}


@op("task.cancel")
def task_cancel(c, args):
    """Cancel your ``task`` (any task if you hold all) for good: disable its schedules and stop a waiting run before
    its next step."""
    stop(owned(c, args))


def summary(task):
    return {"name": task.name, "status": task.status, "creator": creator(task),
            "steps": [{**json.loads(s.action), "wait": s.run_next_after, "status": s.status} for s in ordered(task)],
            "schedules": [view(s, "name", "run_at", "repeat_every", "last_run_at", "disabled") for s in task.schedules]}


@op("task.view", read=True)
def task_list(c, args):
    """Tasks with their status, creator, steps and schedules, ``count`` rows at a time (default 100, at most 500) from
    row ``from`` (default 1). Returns ``tasks`` and ``next``, the ``from`` of the next page (null at the end)."""
    rows, nxt = page(Task, args)
    return {"tasks": [summary(t) for t in rows], "next": nxt}


@op("task.view", read=True)
def task_logs(c, args):
    """The runs of ``task``, newest first: ``count`` (default 10, at most 500) from run ``from`` (default the newest)
    down. Returns ``runs`` and ``next``, the ``from`` of the older page (null at the end)."""
    top, count = int(args.get("from") or 0), min(int(args.get("count") or 10), PAGE_MAX)
    ids = [i for i in reversed(children(find(Task, args["task"]), "executions")) if not top or i <= top]
    return {"runs": [view(TaskExecution.load(str(i)), "name", "status", "started_at", "completed_at", "result")
                     for i in ids[:count]], "next": ids[count] if len(ids) > count else None}
