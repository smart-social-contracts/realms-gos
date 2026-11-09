"""A new codex across a realm's own quarters (docs/codices/layered-codex.md §9.5, realms-gos#501).

One realm-wide vote adopts the codex; its switch time T is the proposal's ``effective_at``, or the acceptance. The
capital's install job (api.installs) stages it as for any realm, and then tells each active quarter: the codex's
checksum, the change list's checksum and T (``codex.stage``). A quarter stages the same codex with the same job, taking
every byte from its capital, and nothing changes while it does. At T it switches by itself, from a task schedule that
survives an upgrade (``tasks.rearm``). A quarter that is not staged by T keeps its codex and is judged non-compliant
by the capital; one that finishes later switches then, if the capital still runs that codex (api.installs.confirm).
The switch cannot be atomic across canisters: calls between quarters carry the sender's codex checksum
(quarters.ask), which the receivers record.

A capital that drops a staged codex (its proposal was stopped, its job failed) tells the quarters (``codex.unstage``).
A quarter a message does not reach, because it is down or upgrading, is not told again: it keeps its codex and reads
non-compliant. One that was told and is paused by an upgrade waits for an admin's ``codex_install_resume``, like any
install job.
"""

import json

from api import codex as runtime
from api import installs, links, quarters, tasks
from core import chain, compliance
from core import layered_codex as store
from core.db import Conflict, Fail, each, find, next_id
from core.op import op
from ggg import FederationMessage, InstallJob, LayeredCodex, Quarter, Task, TaskSchedule

STAGE, UNSTAGE = "codex.stage", "codex.unstage"  # the federation_message topics
# Seconds the capital gives the quarters that switched at T to say so before it judges the others: they report
# within moments, and a flag raised in between would be a flag withdrawn at once.
JUDGE_AFTER = 60


# ---- The capital: tell the quarters.


def announce(job):
    """The capital has staged the codex of ``job`` (install step 5): every active quarter is told what to stage and
    when it switches. In the no-``effective_at`` case T is the acceptance, which is the moment the job started."""
    row = LayeredCodex[job.staged["codex"]]
    tell_all(STAGE, {"codex": row.id, "checksum": job.change_list["checksum"],
                     "at": row.activates_at or job.started_at, "proposal": job.proposal})


def withdraw(codex_id):
    """The capital dropped the staged codex ``codex_id``: the quarters that staged it drop it too."""
    tell_all(UNSTAGE, {"codex": codex_id})


def tell_all(topic, body):
    """``body`` to every active quarter, each call in a message of its own: one that cannot be reached holds up no
    other, and the record of the message says which did not answer. Nothing to do on a quarter, which has none."""
    if quarters.federated():
        return
    for q in each(Quarter, "status", "active"):
        chain.set_timer(0, lambda cid=q.id: tell(cid, topic, body))


def tell(cid, topic, body):
    """One ``federation_message`` to the quarter ``cid``, recorded with its reply. A generator."""
    try:
        reply = yield from quarters.ask(cid, "federation_message", {"topic": topic, "body": body})
    except Exception as e:  # noqa: BLE001  a quarter that is down, stopped or upgrading: its reply is the record
        reply = {"ok": False, "error": str(e)[:300]}
    FederationMessage(id=next_id(FederationMessage, "FM"), topic=topic, source=chain.self_id(), body=body,
                      response={cid: reply}, codex=quarters.codex()["id"], compliance=quarters.own_compliance())


# ---- A quarter: stage what the capital announced, and switch at T.


def staging(codex_id):
    """The job that stages ``codex_id`` for the capital, if one is running or paused."""
    live = store.live_job()
    return live if live is not None and live.staged.get("announced", {}).get("codex") == codex_id else None


def on_stage(c, body):
    """From the capital: stage the codex ``codex`` (its change list is stored under ``checksum`` there) and switch to
    it at ``at``. Asking again for a codex being staged, or already active, changes nothing; a newer announcement
    replaces a staging in progress; another install running here refuses it."""
    quarters.from_capital(c)
    codex_id, checksum, at = body.get("codex"), body.get("checksum"), body.get("at")
    if not (links.pinned(codex_id) and links.pinned(checksum) and isinstance(at, int) and at >= 0):
        raise Fail("a codex to stage is {codex, checksum: sha256:<64 hex digits>, at: seconds}")
    current = runtime.active()
    if current is not None and current.id == codex_id:
        return {"staged": "active"}
    running = staging(codex_id)
    if running is not None:
        return {"staged": "running", "job": running.id}
    live = store.live_job()
    if live is not None:
        if not installs.announced(live):
            raise Conflict("an install is running")
        installs.fail(live, f"the capital announced {codex_id}, which replaces it")
    job = store.start_job(str(body.get("proposal") or ""), {"url": "", "checksum": checksum})
    job.staged = {"announced": {"codex": codex_id, "at": at}}
    if at > chain.now():
        schedule(job, at)
    installs.launch(job)
    return {"staged": "started", "job": job.id}


def on_unstage(c, body):
    """From the capital: drop the codex ``codex`` if it is still being staged here. One already switched to stays."""
    quarters.from_capital(c)
    job = staging(body.get("codex"))
    if job is not None:
        installs.fail(job, "the capital withdrew it")
    return {"unstaged": job is not None}


def schedule(job, at):
    """Run ``codex_switch_staged`` for ``job`` at T as a one-step task of the realm's task scheduler, which arms its
    timer again after an upgrade (tasks.rearm). The step runs as the realm itself: the capital's vote is the
    authority. The task's name starts with ``tasks.OWN``, which no member can create."""
    name = f"{tasks.OWN}codex {job.id}"
    task = Task(name=name, metadata=json.dumps({"creator": chain.self_id()}), status="pending")
    tasks.define(task, [("codex_switch_staged", {"job": job.id}, 0)])
    when = TaskSchedule(name=f"{name}@1", task=task, last_run_at=0, run_at=at, repeat_every=0)
    tasks.enable(when, True)
    tasks.arm(when)


@op(None, public=True, returns="job")
def codex_switch_staged(c, args):
    """Switch to the codex this quarter staged in the install ``job``, when its time T has come. Anyone may ask: the
    capital's vote decided it and the time cannot move; the realm's scheduler calls it at T, and asking again is
    harmless. A job not staged by T stays as it is: it keeps staging, and switches when it is ready. Returns the
    job's ``status`` and ``step``."""
    job = find(InstallJob, args["job"])
    announced = job.staged.get("announced")
    if announced is None:
        raise Fail(f"{job.id} stages no codex of a capital")
    if chain.now() < announced["at"]:
        raise Conflict(f"{job.id} switches at {announced['at']}")
    if job.status == "running" and job.step == installs.WAIT:
        installs.switch(job)
    return {"job": job.id, "status": job.status, "step": job.step}


def after_switch():
    """The message after a layered codex was switched to: a quarter reports what it runs, so that its capital's flag for
    it follows at once; the capital judges its quarters when the others have had time to say they switched. A
    generator."""
    if quarters.federated():
        try:
            yield from quarters.ask(quarters.capital(), "quarter_sync", quarters.report())
        except Exception:  # noqa: BLE001  it reports again at its next sync
            pass
    else:
        chain.set_timer(JUDGE_AFTER, judge_quarters)


# ---- The capital: judge the quarters.


def required():
    """The layered codex a quarter must run: the capital's active one, or its pending one once its time has come (the
    capital's own switch is a moment behind T, and a quarter that switched at T is not at fault). None when the
    capital runs no layered codex."""
    waiting = runtime.pending()
    if waiting is not None and 0 < waiting.activates_at <= chain.now():
        return waiting
    active = runtime.active()
    return active if isinstance(active, LayeredCodex) else None


def judge(quarter, reported):
    """The flag the capital gives ``quarter`` that reports it runs the codex ``reported`` ("" for none), as the JSON text
    a row keeps (core.compliance): compliant when it is the codex in force, otherwise non-compliant since that codex
    took effect (or since the quarter was first found behind), naming the layers that differ. Unchanged when the
    capital runs no layered codex or the report names no codex (``reported`` is None)."""
    target = required()
    if target is None or reported is None:
        return quarter.compliance
    if reported == target.id:
        return compliance.valid("compliant")
    before = compliance.of(quarter.compliance)
    since = before["NonCompliant"]["since"] if isinstance(before, dict) else target.activates_at or target.installed_at
    theirs = LayeredCodex[reported] if reported else None
    return compliance.valid({"NonCompliant": {"since": since, "stale": behind(target, theirs)}})


def behind(target, theirs):
    """The layers of ``target`` that a quarter running ``theirs`` does not run as they are: all of them when ``theirs``
    is None (another kind of codex)."""
    if theirs is None:
        return list(target.layers)
    return [layer for layer in target.layers if layer_of(target, layer) != layer_of(theirs, layer)]


def layer_of(codex, layer):
    return {chapter: entry["sha256"] for chapter, entry in codex.chapters.items() if entry["source"] == layer}


def judge_quarters():
    """On the capital, a moment after it switched: every active quarter that has reported is judged against the codex now
    in force, so that one that did not switch with the others reads non-compliant since T without waiting for its next
    report."""
    for q in each(Quarter, "status", "active"):
        if q.last_sync:
            q.compliance = judge(q, q.codex_id)
