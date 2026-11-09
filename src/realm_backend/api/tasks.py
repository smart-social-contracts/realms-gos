import json

from core import auth
from core import chain
from core.db import PAGE, PAGE_MAX, Conflict, Fail, Forbidden, find, next_id, page, view
from ggg import Delegation, Event, Task, TaskExecution, TaskSchedule, TaskStep
from core.op import dispatch, on_behalf, op, ops, redacted
from core.ratio import upgrade_call

BUSY = ("running", "waiting")
# A task the realm created itself, such as the run of a scheduled proposal, runs as the realm and is locked: its only
# authority is the vote that scheduled it, so nobody changes, runs or removes one by hand. Its creator, the realm, makes
# it the realm's own (``own``); its name starts with OWN, which task_create refuses, so nobody can take the name first.
OWN = "proposal "
# The tasks task_schedule creates are named "TASK-<n>"; task_create refuses such names, so a name chosen first cannot clash.
SCHEDULED = "TASK"
# GGG's status of a task, from the toolkit's: scheduled, then run, failed or cancelled.
STATUS = {"pending": "scheduled", "running": "scheduled", "waiting": "scheduled", "completed": "run", "failed": "failed",
          "cancelled": "cancelled"}
# The toolkit's TaskSchedule declares no indexed field, so the enabled schedules are kept in this field index.
ENABLED = (TaskSchedule.get_full_type_name(), "enabled", "1")


def meta(task):
    """What the realm keeps about a task: its ``creator``, the ``grant`` its steps were written under (a delegate's
    power of attorney, "" for the member's own) and the ``event`` of the last operation it ran."""
    return json.loads(task.metadata)


def creator(task):
    return meta(task)["creator"]


def tie(task):
    """The id of the power of attorney the steps of ``task`` run under, "" for a member's own. A task made before grants
    were recorded has none."""
    return meta(task).get("grant", "")


def scheduled(task):
    """Whether ``task`` is one task_schedule made: GGG's Task, which runs once."""
    return task.name.startswith(f"{SCHEDULED}-")


def own(task):
    return creator(task) == chain.self_id()


def owned(c, args):
    """``task``, if you created it or hold ``all``: its steps run as its creator. Any task the realm created is locked,
    even to an admin."""
    task = find(Task, args["task"])
    if own(task):
        raise Forbidden(f"task '{task.name}' is the realm's own: only the vote that scheduled it governs it")
    if c.user != creator(task) and "all" not in auth.permissions(c.user):
        raise Forbidden(f"task '{task.name}' is not yours")
    if c.delegated and tie(task) != c.grant:  # else it could run what the member wrote, with the member's own authority
        raise Forbidden(f"task '{task.name}' was not made under this power of attorney")
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


def define(task, steps, reason=None):
    """Give ``task`` its ``steps``; ``reason`` says why they were scheduled."""
    # The toolkit's TaskStep has no field for an operation call; ic_python_db keeps the extra attribute.
    for name, args, wait in steps:
        call = {"op": name, "args": args, **({"reason": reason} if reason else {})}
        TaskStep(task=task, run_next_after=wait, action=json.dumps(call))


def standing(grant_id):
    """The power of attorney ``grant_id``, which the steps of a task run under (None for the member's own). Forbidden once
    it was revoked, replaced or has expired: a proposal may outlast its grant because a vote takes days, but a task
    would keep acting with nobody's consent."""
    if not grant_id:
        return None
    grant = find(Delegation, grant_id)
    if grant.status != "active" or not auth.in_force(grant, chain.now()):
        what = "been revoked or replaced" if grant.status == "revoked" else "expired"
        raise Forbidden(f"the power of attorney {grant.id} the task was made under has {what}")
    return grant


def step_reply(task, call):
    """The reply of one step, as ``dispatch`` gives it: its operation run as the task's creator. A step a delegate wrote
    runs as that delegate for the creator, under the grant it was written under, so it can do what the grant covers
    directly and no more."""
    info = meta(task)
    try:
        grant = standing(info.get("grant"))
    except Forbidden as e:
        unschedule(task)  # the grant is gone for good: nothing fires again
        return json.dumps({"ok": False, "error": str(e), "kind": e.kind})
    user = info["creator"]
    by = grant.delegate if grant else user
    return (yield from dispatch(call["op"], json.dumps(on_behalf(user, by, call["args"])), origin="task", signer=by,
                                bound=grant))


def execute(task, start=0, ex=None):
    """Run the steps from ``start`` through ``dispatch`` as the task's creator, pausing where a step waits."""
    ex = ex or TaskExecution(name=f"{task.name}#{len(children(task, 'executions')) + 1}", task=task, status="running",
                             started_at=chain.now(), result="")
    task.status, steps, out = "running", ordered(task), {}
    for i in range(start, len(steps)):
        call = upgrade_call(json.loads(steps[i].action))  # a step stored by the previous release has its arguments
        out = json.loads((yield from step_reply(task, call)))
        steps[i].status = "completed" if out["ok"] else "failed"
        if not out["ok"]:
            return finish(task, ex, "failed", out)
        if not ops()[call["op"]]["read"]:
            task.metadata = json.dumps({**meta(task), "event": str(Event.max_id())})  # the step's own audit event
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
    # A schedule moved to a later time (task_reschedule) leaves its first timer behind: that one is too early.
    if s is None or s.disabled or chain.now() < s.run_at:
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


def unschedule(task):
    for s in task.schedules:
        enable(s, False)


def stop(task):
    unschedule(task)
    task.status = "cancelled"


@op("task.create")
def task_create(c, args):
    """Create task ``name`` of ``steps``: ``[{op, args, wait}]``, each run as you, ``wait`` seconds before the next. A
    delegate's task runs as the delegate for you, under the grant it was made under, while that grant stands. Returns
    ``task``."""
    steps = checked(args["steps"])
    if args["name"].startswith(OWN):
        raise Forbidden(f"task names starting with '{OWN}' are the realm's own")
    if args["name"].startswith(f"{SCHEDULED}-"):
        raise Forbidden(f"task names starting with '{SCHEDULED}-' are named by task_schedule")
    if Task[args["name"]]:
        raise Conflict(f"task '{args['name']}' exists")
    task = Task(name=args["name"], metadata=json.dumps({"creator": c.user, "grant": c.grant}), status="pending")
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
    task.metadata = json.dumps({**meta(task), "grant": c.grant})  # the steps are now the editor's


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
def task_add_schedule(c, args):
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


def valid_schedule(args):
    if not isinstance(args.get("args") or {}, dict):
        raise Fail("args are the arguments of the operation, an object")
    checked([{"op": args["op"]}])
    if ops()[args["op"]]["read"]:
        raise Fail(f"{args['op']} only reads: a task runs a change")


@op("task.schedule", check=valid_schedule, returns="task")
def task_schedule(c, args):
    """Schedule the operation ``op`` with ``args`` once, at the time ``at`` (seconds; a time that has passed runs it at
    once), with an optional ``reason``. It runs as you, with the permissions you hold then: every guard and rule runs
    again, so it obeys the legislation in force at that time, and a task that is refused ends failed and changes
    nothing. Its operation's audit event, with origin task, is the task's ``event``. Only a user calling for themselves
    schedules: a codex, an extension or the replay of a proposal is refused, as the task would run as the canister or as
    someone who did not choose it. A delegate's task is tied to its grant: it runs only what the grant covers directly,
    and fails once the grant is revoked, replaced or has expired. Returns ``task``."""
    if c.origin != "user":
        raise Fail(f"a task is scheduled by a user calling for themselves, not by a {c.origin}")
    task = Task(name=next_id(Task, SCHEDULED), metadata=json.dumps({"creator": c.user, "grant": c.grant}),
                status="pending")
    define(task, checked([args]), args.get("reason"))
    when = TaskSchedule(name=f"{task.name}@1", task=task, last_run_at=0, run_at=int(args["at"]), repeat_every=0)
    enable(when, True)
    arm(when)
    return {"task": task.name}


@op("task.schedule", returns="task")
def task_reschedule(c, args):
    """Move the time of your scheduled ``task`` (any task if you hold all) to ``at`` (seconds). A task that has run,
    failed or been cancelled stays as it is, and so does one without a single schedule to move."""
    task = owned(c, args)
    schedules = list(task.schedules)
    if task.status != "pending" or len(schedules) != 1 or schedules[0].disabled:
        raise Conflict(f"task '{task.name}' does not wait for a single time ({STATUS[task.status]})")
    schedules[0].run_at = int(args["at"])
    arm(schedules[0])
    return {"task": task.name}


@op("task.schedule", returns="task")
def task_cancel(c, args):
    """Cancel your ``task`` (any task if you hold all) for good: disable its schedules and stop a waiting run before
    its next step. A task made by task_schedule that has already run or failed is a conflict: it stays as it ended."""
    task = owned(c, args)
    if scheduled(task) and task.status in ("completed", "failed"):
        raise Conflict(f"task '{task.name}' already ended: {STATUS[task.status]}")
    stop(task)
    return {"task": task.name}


def shown(step):
    """A step as the task list shows it: its operation's arguments as a log may show them, not the personal data or
    secrets they hold."""
    call = json.loads(step.action)
    return {**call, "args": redacted(call["op"], call.get("args") or {}), "wait": step.run_next_after, "status": step.status}


def summary(task):
    info = meta(task)
    return {"name": task.name, "status": task.status, "creator": info["creator"], "grant": info.get("grant", ""),
            "event": info.get("event"), "steps": [shown(s) for s in ordered(task)],
            "schedules": [view(s, "name", "run_at", "repeat_every", "last_run_at", "disabled") for s in task.schedules]}


def wanted(args):
    """The tasks task_list keeps: those with a step of the operation ``op`` and the GGG ``status``, if given."""
    def keep(task):
        return (not args.get("op") or any(json.loads(s.action)["op"] == args["op"] for s in task.steps)) \
            and (not args.get("status") or STATUS[task.status] == args["status"])
    return keep


@op("realm.monitor", read=True, returns="tasks")
def task_list(c, args):
    """Tasks with their status, creator, steps and schedules, ``count`` rows at a time (default 100, at most 500) from
    row ``from`` (default 1), optionally only those with a step of the operation ``op`` or with the ``status``
    scheduled, run, failed or cancelled; a filtered page may hold fewer. Returns ``tasks`` and ``next``, the ``from`` of
    the next page (null at the end)."""
    rows, nxt = page(Task, args, wanted(args))
    return {"tasks": [summary(t) for t in rows], "next": nxt}


@op("task.view", read=True)
def task_logs(c, args):
    """The runs of ``task``, newest first: ``count`` (default 10, at most 500) from run ``from`` (default the newest)
    down. Returns ``runs`` and ``next``, the ``from`` of the older page (null at the end)."""
    top, count = int(args.get("from") or 0), min(int(args.get("count") or 10), PAGE_MAX)
    ids = [i for i in reversed(children(find(Task, args["task"]), "executions")) if not top or i <= top]
    return {"runs": [view(TaskExecution.load(str(i)), "name", "status", "started_at", "completed_at", "result")
                     for i in ids[:count]], "next": ids[count] if len(ids) > count else None}
