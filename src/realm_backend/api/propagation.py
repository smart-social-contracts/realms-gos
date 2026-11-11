"""A new codex across a realm's own quarters (docs/codices/layered-codex.md §9.5, realms-gos#501).

One realm-wide vote adopts the codex; its switch time T is the proposal's ``effective_at``, or the acceptance. The
capital's install job (api.installs) stages it as for any realm, and then tells each active quarter: the codex's
checksum, the change list's checksum, T and the layers from above it staged (``codex.stage``). A quarter stages the same
codex with the same job, taking every byte and those layers from its capital (it has no sync source of its own), and
nothing changes while it does. At T it switches by itself, from a task schedule that
survives an upgrade (``tasks.rearm``). A quarter that is not staged by T keeps its codex and is judged non-compliant
by the capital; one that finishes later switches then, if the capital still runs that codex (api.installs.confirm).
The switch cannot be atomic across canisters: calls between quarters carry the sender's codex checksum
(quarters.ask), which the receivers record.

A capital that drops a staged codex (its proposal was stopped, its job failed) tells the quarters (``codex.unstage``),
and one that reverts tells them to restore the codex it restored (``codex.revert``), which each keeps in its history:
nothing is downloaded and no ``on_codex_activate`` runs. A quarter that reports it runs a codex the realm voted out is
told again, so one that was down catches up at its next report.
A quarter a message does not reach, because it is down or upgrading, is not told again: it keeps its codex and reads
non-compliant. One that was told and is paused by an upgrade waits for an admin's ``codex_install_resume``, like any
install job.
"""

import json

from api import codex as runtime
from api import installs, links, quarters, tasks
from core import chain, compliance, legislation
from core import layered_codex as store
from core.db import JSON_MAX, Conflict, Fail, each, find, next_id
from core.op import op
from ggg import FederationMessage, InstallJob, LayeredCodex, Quarter, Task, TaskSchedule

# The federation_message topics of this module (core.legislation lists them: no chapter, hook or forbid runs on a message
# that carries one). A local holder of quarter.sync can relay any other topic to the quarters, but never these: only the
# capital's install and revert send them.
STAGE, UNSTAGE, REVERT = OWN = legislation.PROPAGATION
# Seconds the capital gives the quarters that switched at T to say so before it judges the others: they report
# within moments, and a flag raised in between would be a flag withdrawn at once.
JUDGE_AFTER = 60


# ---- The capital: tell the quarters.


def announce(job):
    """The capital has staged the codex of ``job`` (install step 5): every active quarter is told what to stage and
    when it switches, with the layers from above the capital staged from its sync sources' records, which a quarter
    has none of. In the no-``effective_at`` case T is the acceptance, which is the moment the job started.

    Both ends keep the message in a ``FederationMessage``, whose body holds at most ``JSON_MAX`` characters: a body
    above it would be lost in the quarter with no trace, and the quarter would drift. So a capital with quarters
    refuses to stage a codex it cannot announce, and its install fails with the reason."""
    row = LayeredCodex[job.staged["codex"]]
    body = {"codex": row.id, "checksum": job.change_list["checksum"], "at": row.activates_at or job.started_at,
            "proposal": job.proposal, "layers": for_quarters(job.staged["layers"])}
    size = len(json.dumps(body))
    if size > JSON_MAX and audience():
        raise Fail(f"the layers from above are too big to announce to the quarters: {size} characters, at most "
                   f"{JSON_MAX}")
    tell_all(STAGE, body)


def for_quarters(layers):
    """``layers`` as a quarter needs them: the order of the record that vouches for a release is cut to the layers the
    new codex runs, among which the quarter places the release in its own record (links.relay_release). The record
    may list up to a thousand layers; a codex runs a few."""
    vouching = layers["vouching"]
    if vouching is None:
        return layers
    running = {layer["source"] for relay in layers["relays"] for layer in relay["layers"]}
    return {**layers, "vouching": {**vouching, "order": [source for source in vouching["order"] if source in running]}}


def reverted(target):
    """The realm's codex was restored to ``target`` (api.codex.restore): a capital tells its quarters to restore it too.
    A quarter, and a capital, then see what the realm runs: the one reports it, the other judges its quarters."""
    tell_all(REVERT, {"codex": target.id})
    chain.set_timer(0, after_switch)


def correct(quarter):
    """A quarter that reports it runs a codex the realm voted out, and so missed the revert, is told again to restore
    the one in force: it was down, or upgrading, when the capital told it."""
    current = runtime.active()
    if current is None or quarter.codex_id in ("", current.id):
        return
    running = LayeredCodex[quarter.codex_id]
    if running is not None and running.status == "reverted":
        chain.set_timer(0, lambda cid=quarter.id, codex_id=current.id: tell(cid, REVERT, {"codex": codex_id}))


def withdraw(codex_id):
    """The capital dropped the staged codex ``codex_id``: the quarters that staged it drop it too."""
    tell_all(UNSTAGE, {"codex": codex_id})


def tell_all(topic, body, recorded=None):
    """``body`` to every active quarter, each call in a message of its own: one that cannot be reached holds up no
    other, and the record of the message says which did not answer. ``recorded(reply)``, when given, is a reply as the
    record keeps it."""
    for q in audience():
        chain.set_timer(0, lambda cid=q.id: tell(cid, topic, body, recorded))


def audience():
    """The quarters a capital tells: the active ones. None on a quarter, which has no quarters of its own."""
    return [] if quarters.federated() else list(each(Quarter, "status", "active"))


def tell(cid, topic, body, recorded=None):
    """One ``federation_message`` to the quarter ``cid``, recorded with its reply. A generator."""
    try:
        reply = yield from quarters.ask(cid, "federation_message", {"topic": topic, "body": body})
    except Exception as e:  # noqa: BLE001  a quarter that is down, stopped or upgrading: its reply is the record
        reply = {"ok": False, "error": str(e)[:300]}
    if recorded:
        reply = recorded(reply)
    FederationMessage(id=next_id(FederationMessage, "FM"), topic=topic, source=chain.self_id(), body=body,
                      response={cid: reply}, codex=quarters.codex()["id"], compliance=quarters.own_compliance())


# ---- A quarter: stage what the capital announced, and switch at T.


def staging(codex_id):
    """The job that stages ``codex_id`` for the capital, if one is running or paused."""
    live = store.live_job()
    return live if live is not None and live.staged.get("announced", {}).get("codex") == codex_id else None


def on_stage(c, body):
    """From the capital: stage the codex ``codex`` (its change list is stored under ``checksum`` there) and switch to
    it at ``at``, with the ``layers`` from above the capital staged: the capital is the quarter's authority for them, and
    the codex id the quarter builds must still be the one announced. Asking again for a codex being staged for the same
    time, or already active, changes nothing; a newer announcement, or the same codex for another time, replaces a
    staging in progress; another install running here refuses it."""
    quarters.from_capital(c)
    codex_id, checksum, at, layers = body.get("codex"), body.get("checksum"), body.get("at"), body.get("layers")
    if not (links.pinned(codex_id) and links.pinned(checksum) and isinstance(at, int) and at >= 0):
        raise Fail("a codex to stage is {codex, checksum: sha256:<64 hex digits>, at: seconds}")
    if not (layers is None or staged_layers(layers)):
        raise Fail("layers are {relays: [...], vouchers: {...}, vouching: {...} or null}")
    current = runtime.active()
    if current is not None and current.id == codex_id:
        return {"staged": "active"}
    running = staging(codex_id)
    if running is not None:
        if running.staged["announced"]["at"] == at:
            return {"staged": "running", "job": running.id}
        installs.fail(running, "the capital announced it again for another time")
    live = store.live_job()
    if live is not None:
        if not installs.announced(live):
            raise Conflict("an install is running")
        installs.fail(live, f"the capital announced {codex_id}, which replaces it")
    # The job names no proposal: the capital's id could be the id of one of this realm's own, which would then switch it.
    job = store.start_job("", {"url": "", "checksum": checksum})
    job.staged = {"announced": {"codex": codex_id, "at": at, "capital": c.by,
                                "proposal": str(body.get("proposal") or "")}}
    if layers is not None:  # a capital on the previous release sends none, and the job reads this realm's records
        job.staged = {**job.staged, "layers": layers}
    if at > chain.now():
        schedule(job, at)
    installs.launch(job)
    return {"staged": "started", "job": job.id}


def staged_layers(layers):
    """Whether ``layers`` has the shape of the layers an install job stages (core.change_list.layers_for)."""
    return (isinstance(layers, dict) and isinstance(layers.get("relays"), list)
            and isinstance(layers.get("vouchers"), dict)
            and (layers.get("vouching") is None or isinstance(layers["vouching"], dict)))


def on_unstage(c, body):
    """From the capital: drop the codex ``codex`` if it is still being staged here. One already switched to stays."""
    quarters.from_capital(c)
    job = staging(body.get("codex"))
    if job is not None:
        installs.fail(job, "the capital withdrew it")
    return {"unstaged": job is not None}


def on_revert(c, body):
    """From the capital: it restored the earlier codex ``codex``, and so does this quarter, at once, to the copy it keeps
    in its history (nothing is downloaded). No ``on_codex_activate`` runs. A codex this quarter never ran is refused,
    and the capital records the refusal."""
    quarters.from_capital(c)
    codex_id = body.get("codex")
    if not (isinstance(codex_id, str) and codex_id):
        raise Fail("a revert to restore names the codex")  # without one, restoring would take the previous codex
    current = runtime.active()
    if current is not None and current.id == codex_id:
        return {"reverted": "active"}
    return {"reverted": "restored", **runtime.restore(runtime.restoring({"codex": codex_id}))}


def task_name(job):
    return f"{tasks.OWN}codex {job.id}"


def schedule(job, at):
    """Run ``codex_switch_staged`` for ``job`` at T as a one-step task of the realm's task scheduler, which arms its
    timer again after an upgrade (tasks.rearm). The step runs as the realm itself: the capital's vote is the
    authority. The task's name starts with ``tasks.OWN``, the realm's own tasks, which no member can create (the word
    is "proposal" because a vote schedules the other kind; this one is no proposal's)."""
    name = task_name(job)
    task = Task(name=name, metadata=json.dumps({"creator": chain.self_id()}), status="pending")
    tasks.define(task, [("codex_switch_staged", {"job": job.id}, 0)])
    when = TaskSchedule(name=f"{name}@1", task=task, last_run_at=0, run_at=at, repeat_every=0)
    tasks.enable(when, True)
    tasks.arm(when)


def unschedule(job):
    """``job`` ended without the switch (it failed, or a newer announcement replaced it): its task does not fire."""
    task = Task[task_name(job)]
    if task is not None:
        tasks.stop(task)


@op(None, public=True, returns="job")
def codex_switch_staged(c, args):
    """Switch to the codex this quarter staged in the install ``job``, when its time T has come, once the capital has
    confirmed it still requires it (api.installs.confirm). Anyone may ask: the capital's vote decided it and the time
    cannot move; the realm's scheduler calls it at T, and asking again is harmless. A job not staged by T stays as it
    is: it keeps staging, and switches when it is ready. Returns the job's ``status`` and ``step``."""
    job = find(InstallJob, args["job"])
    announced = job.staged.get("announced")
    if announced is None:
        raise Fail(f"{job.id} stages no codex of a capital")
    if chain.now() < announced["at"]:
        raise Conflict(f"{job.id} switches at {announced['at']}")
    if job.status == "running" and job.step == installs.WAIT:
        if installs.announced_by_my_capital(job):
            installs.leave_wait(job)
        else:
            installs.fail(job, installs.LEFT)
    return {"job": job.id, "status": job.status, "step": job.step}


def after_switch():
    """The message after the realm's codex changed, by a switch or a revert: a quarter reports what it runs, so that its
    capital's flag for it follows at once; the capital judges its quarters when the others have had time to say they
    changed. A generator."""
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


def in_force():
    """The codex the capital's quarters must run, as the directory names a codex (``id``, ``version``): ``required``, or
    the capital's own codex when it runs no layered one. What ``quarter_sync`` answers, and a quarter confirms its
    switch against."""
    target = required()
    return {"id": target.id, "version": target.id} if target is not None else quarters.codex()


def judge(quarter, reported, target):
    """The flag the capital gives ``quarter`` that reports it runs the codex ``reported`` ("" for none), as the JSON text
    a row keeps (core.compliance), against ``target``, the codex it must run (``required``): compliant when it is that
    codex, otherwise non-compliant since it took effect (or since the quarter was first found behind), naming the
    layers that differ. Unchanged when the capital runs no layered codex or the report names no codex (``reported`` is
    None)."""
    if target is None or reported is None:
        return quarter.compliance
    if reported == target.id:
        return compliance.valid("compliant")
    before = compliance.of(quarter.compliance)
    since = before["NonCompliant"]["since"] if isinstance(before, dict) else took_effect(target)
    theirs = LayeredCodex[reported] if reported else None
    return compliance.valid({"NonCompliant": {"since": since, "stale": behind(target, theirs)}})


def took_effect(target):
    """When ``target`` took effect: its time T while it is pending, otherwise when the realm put it in force (the
    switch, or the revert that restored it)."""
    return target.activates_at if target.status == "pending" else store.in_force_since() or target.installed_at


def behind(target, theirs):
    """The layers of ``target`` that a quarter running ``theirs`` does not run as they are: all of them when ``theirs``
    is None (another kind of codex)."""
    if theirs is None:
        return list(target.layers)
    return [layer for layer in target.layers if layer_of(target, layer) != layer_of(theirs, layer)]


def layer_of(codex, layer):
    return {chapter: entry["sha256"] for chapter, entry in codex.chapters.items() if entry["source"] == layer}


def judge_quarters():
    """On the capital, a moment after its codex changed: every active quarter that has reported is judged against the
    codex now in force, so that one that did not change with the others reads non-compliant since the change without
    waiting for its next report. It is a timer: an upgrade of the capital within the minute loses it, and the flags
    then follow the quarters' own reports."""
    target = required()
    for q in each(Quarter, "status", "active"):
        if q.last_sync:
            q.compliance = judge(q, q.codex_id, target)
