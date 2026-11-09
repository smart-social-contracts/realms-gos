"""The install task of a layered codex (docs/codices/layered-codex.md §9.2).

``codex_install`` with a change list (GGG's ``Code { url; checksum }``) starts an ``InstallJob`` and returns. A timer
then runs one step per message:

1. ``fetch_change_list``: the change list by HTTPS outcall, kept under the proposal's checksum;
2. ``check_change_list``: its rules, by itself and against this realm (core.change_list.apply);
3. ``fetch_chapters``: each chapter not stored yet, one per message, from its URL or a mirror, kept under its checksum;
4. ``check_sources``: the chapters against the records of the sync sources (core.change_list.vouch);
5. ``build``: the layer paths (``plan``), every chapter loaded and checked in a fresh sandbox, the Cedar its forbids
   become validated by the realm's engine (core.legislation.build), then the codex row, ``pending``;
6. ``switch``: the new codex active, the previous one kept, the runtime loaded (api.codex.switch); the realm
   domain's ``on_codex_activate`` hooks run in the message after it.

A proposal with an ``effective_at`` stages at acceptance (steps 1 to 5, ``prepare``) and its scheduled execution at
that time is the switch: a job not done by then fails it, and the old codex keeps running. Without one, the job
runs on acceptance and switches as soon as it is built. A failure at any step records its reason, drops the staged
codex and leaves the active codex unchanged. Every step can run twice: the fetches skip what is stored, and the
switch checks the active codex first. One job at a time, and a staged job holds that place until its switch time
(stopping its proposal frees it; a revert is not an install and stays allowed): one caught by an upgrade waits
``paused`` for an admin's ``codex_install_resume``; one not done in 24 hours fails, as does a step that has not
finished 15 minutes after it was armed (a step that ran out of instructions leaves no trace of its own).
``codex_status`` shows the job, its step and its error.

A quarter stages the codex its capital announced (api.propagation) with the same job, in staging mode: the bytes of the
change list and of every chapter come from the capital, by checksum, and the switch is at the time the capital gave.
A quarter that is ready after that time (``confirm``) switches only if the capital still runs the codex.
"""

import json

from api import codex, quarters
from core import chain
from core import change_list
from core import layered_codex as store
from core import legislation
from core.db import JSON_MAX, Conflict, Fail, each, find, next_id, view
from core.op import GENERATOR, op
from ggg import ChapterSource, InstallJob, LayeredCodex, Proposal

STEPS = ("fetch_change_list", "check_change_list", "fetch_chapters", "check_sources", "build", "switch")
WAIT = "wait"  # staged: the pending codex waits for its proposal's effective_at, which switches it
TIMEOUT = 24 * 3600
TIMED_OUT = "not done within 24 hours"
STEP_LIMIT = 15 * 60  # seconds a step may take, outcall included, before the job is given up
REUSED = ("status", "chapters", "proposal", "failures", "activates_at", "installed_at", "previous")  # of a row kept
MAX_CHANGE_LIST = 64 * 1024
MAX_CHAPTER = store.MAX_CHAPTER
# The replica counts a reply's headers and its framing against max_response_bytes, not only its body: room for them,
# so that a chapter of exactly 256 KB arrives. The body itself is held to the limit (``decoded``).
HEADROOM = 8 * 1024
HEX = "0123456789abcdef"
LEFT = "this realm is no longer a quarter of the capital that announced the codex"


def relays():
    """The records of the realm's sync sources (§8), in the shape ``plan`` takes: none until the realm has links
    (realms-gos#491 reads them)."""
    return []


def job_of(proposal):
    """The newest install job of ``proposal``, if any."""
    found = None
    for job in each(InstallJob, "proposal", proposal):
        found = job
    return found


def start(proposal, url, checksum):
    """A new job for the change list at ``url`` with ``checksum`` (``sha256:<hex>``), its first step and its
    24-hour limit armed. Refused while another job runs or is paused."""
    if not (isinstance(checksum, str) and checksum.startswith("sha256:") and len(checksum) == 71
            and all(ch in HEX for ch in checksum[7:])):
        raise Fail("checksum is sha256:<64 hex digits>")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise Fail("the change list is fetched over https")
    return launch(store.start_job(proposal, {"url": url, "checksum": checksum}))


def launch(job):
    """The job's first step in the next message, and its 24-hour limit."""
    job.step = STEPS[0]
    arm(job.id)
    watch(job.id, TIMEOUT)
    return job


def began(job):
    """When the 24 hours of ``job`` began: when it started, or, for a quarter's job that waited for its switch time, when it
    left the wait: the limit covers the work, not the wait."""
    return job.staged.get("left_wait", job.started_at)


def leave_wait(job):
    """A quarter's job whose switch time has come goes on to ask its capital (``confirm``), with 24 hours from now."""
    staged = job.staged
    staged["left_wait"] = chain.now()
    job.staged = staged
    job.step = "confirm"
    arm(job.id)
    watch(job.id, TIMEOUT)


def announced(job):
    """Whether ``job`` stages a codex that a capital announced to this quarter (api.propagation)."""
    return "announced" in job.staged


def announced_by_my_capital(job):
    """Whether the capital that announced the codex of ``job`` is the capital this realm has now: one that seceded, or
    joined another federation, since is not a quarter of the realm that decided it."""
    return quarters.federated() and quarters.capital() == job.staged["announced"]["capital"]


def arm(job_id):
    """The job's current step in the next message, and a watch on it: a step that traps (the instruction limit)
    leaves nothing behind, not even a note that it started, so the mark is made here, in the message before."""
    job = InstallJob[job_id]
    staged, name = job.staged, job.step
    stamp = staged["armed"] = staged.get("armed", 0) + 1
    job.staged = staged
    chain.set_timer(0, lambda: step(job_id))
    chain.set_timer(STEP_LIMIT, lambda: unfinished(job_id, name, stamp))


def watch(job_id, delay):
    chain.set_timer(delay, lambda: expire(job_id))


def begin(c, args):
    """``codex_install`` with a change list ``url`` and its ``checksum``. For the scheduled proposal whose job staged
    the codex, this is the switch at its time; otherwise a job starts and the call returns ``job``."""
    job = job_of(c.proposal) if c.proposal else None
    if job is None:
        return {"job": start(c.proposal, args["url"], args["checksum"]).id}
    if job.status == "done":
        return {"codex": job.staged.get("codex"), "job": job.id}
    if job.status == "running" and job.step in (WAIT, "switch"):
        switch(job)
        return {"codex": job.staged["codex"], "job": job.id}
    if job.status != "failed":
        fail(job, f"not done at the switch time: it was {job.status} in {job.step}")
    raise Fail(f"the install {job.id} failed in {job.step}: {job.error}")


def switch(job):
    """The switch of a staged job at its time: the job fails with the reason when it cannot (``finish``)."""
    try:
        finish(job)
    except Fail as e:
        fail(InstallJob[job.id], str(e))
        raise


def prepare(proposal):
    """An accepted install proposal that waits for its ``effective_at`` stages now, steps 1 to 5, so that its
    scheduled execution is the switch and nothing else (§9.2 step 6). Nothing to do ahead for any other action.
    A job that cannot start (another install is running) is recorded as failed, so the switch says why."""
    action = proposal.action or {}
    args = action.get("args") or {}
    if action.get("op") != "codex_install" or "checksum" not in args:
        return
    try:
        start(proposal.id, args.get("url", ""), args["checksum"])
    except Fail as e:
        InstallJob(id=next_id(InstallJob, "J"), proposal=proposal.id, change_list={"url": args.get("url", ""),
                   "checksum": args["checksum"]}, status="failed", error=str(e), started_at=chain.now(),
                   finished_at=chain.now())


def discard(proposal):
    """The scheduled install ``proposal`` was stopped: its job ends and the codex it staged goes."""
    job = job_of(proposal.id)
    if job is not None and job.status in ("running", "paused"):
        fail(job, f"{proposal.id} was stopped")


def pause_after_upgrade():
    """Timers do not survive an upgrade: the job one was running waits, paused, for an admin's
    ``codex_install_resume``, which runs the step it stopped in again. A staged job has no timer and keeps waiting
    for its switch."""
    job = store.live_job()
    if job is not None and job.status == "running" and job.step != WAIT:
        job.status = "paused"


@op("codex.install", returns="job")
def codex_install_resume(c, args):
    """Resume the install ``job`` an upgrade paused: the step it stopped in runs again, for the change list the vote
    approved; nothing else can change. Returns ``job`` and ``step``."""
    job = find(InstallJob, args["job"])
    if job.status != "paused":
        raise Conflict(f"{job.id} is {job.status}, not paused")
    job.status = "running"
    arm(job.id)
    watch(job.id, max(began(job) + TIMEOUT - chain.now(), 0))
    return {"job": job.id, "step": job.step}


def step(job_id):
    """One step of the job in its own message, then the timer for the next. A generator: steps 1 and 3 make
    outcalls, and the job may be stopped while one is out, so a step reads its job again after it."""
    job = InstallJob[job_id]
    if job is None or job.status != "running" or job.step == WAIT:
        return
    if chain.now() - began(job) > TIMEOUT:
        fail(job, TIMED_OUT)
        return
    try:
        out = RUN[job.step](job)
        following = (yield from out) if isinstance(out, GENERATOR) else out  # the fetching steps are generators
    except Fail as e:
        fail(InstallJob[job_id], str(e))
        return
    except Exception as e:  # noqa: BLE001  anything else is a bug in a step: the job records it instead of hanging
        fail(InstallJob[job_id], f"{type(e).__name__}: {e}")
        return
    job = InstallJob[job_id]
    if following is None or job.status != "running":
        return
    job.step = following
    if following != WAIT:
        arm(job_id)


def expire(job_id):
    """The 24 hours are over: a job still at work fails. A staged job waits for its switch as before."""
    job = InstallJob[job_id]
    if job is not None and job.status == "running" and job.step != WAIT and chain.now() - began(job) >= TIMEOUT:
        fail(job, TIMED_OUT)


def unfinished(job_id, name, stamp):
    """The step armed as ``stamp`` had STEP_LIMIT to finish: a job still running it has lost it."""
    job = InstallJob[job_id]
    if job is not None and job.status == "running" and job.step == name and job.staged.get("armed") == stamp:
        fail(job, f"step {name} did not complete (likely the instruction limit)")


def fail(job, reason):
    """The job ends here: its reason is recorded, the codex it staged is dropped and the active codex is untouched.
    A job that has ended keeps its reason: the late reply to an outcall it made changes nothing."""
    if job.status not in ("running", "paused"):
        return
    from api import propagation  # it imports this module

    if announced(job):
        propagation.unschedule(job)
    waiting = LayeredCodex[job.staged["codex"]] if job.staged.get("codex") else None
    if waiting is not None and waiting.status == "pending":
        codex_id = waiting.id
        if job.staged.get("was"):  # the row this legislation had before the install: back to what it was
            for field, value in job.staged["was"].items():
                setattr(waiting, field, value)
        else:
            waiting.delete()
        propagation.withdraw(codex_id)
    job.staged, job.status, job.error, job.finished_at = {}, "failed", reason[:500], chain.now()


# ---- The steps. Each returns the next step's name, or None when the job is over.


def fetch_change_list(job):
    """Step 1: the change list's bytes, unless they are stored already."""
    url, checksum = job.change_list["url"], job.change_list["checksum"]
    if ChapterSource[checksum] is None:
        if announced(job):
            if not (yield from fetch_from_capital(job, checksum, MAX_CHANGE_LIST)):
                return None
        else:
            status, body = yield from fetched(url, MAX_CHANGE_LIST)
            job = InstallJob[job.id]
            if job.status != "running":
                return None
            receive_change_list(job, status, body)
    return "check_change_list"


def receive_change_list(job, status, body):
    """The reply to step 1's outcall: the bytes the proposal's checksum names, stored under it."""
    url = job.change_list["url"]
    text = decoded(url, status, body, MAX_CHANGE_LIST)
    try:
        store.store_chapter(job.change_list["checksum"], text)
    except Fail as e:
        raise Fail(f"the change list at {url}: {e}")


def check_change_list(job):
    """Step 2: the change list's rules, by itself and against this realm (core.change_list.parse and apply)."""
    prepared(job)
    return "fetch_chapters"


def fetch_chapters(job):
    """Step 3: the first chapter of the change list not stored yet, from its URL or a mirror; the step runs again,
    in the next message, until none is missing."""
    listed, _, _ = prepared(job)
    for entry in listed["set"]:
        if ChapterSource[entry["sha256"]] is None:
            yield from fetch_chapter(job, entry)
            return "fetch_chapters" if InstallJob[job.id].status == "running" else None
    return "check_sources"


def fetch_chapter(job, entry):
    """The bytes of ``entry`` from the first of its URLs that serves the right ones, or from the capital in a quarter,
    stored in the message that receives them. A generator."""
    if announced(job):
        if (yield from fetch_from_capital(job, entry["sha256"], MAX_CHAPTER)):
            note_fetched(InstallJob[job.id], entry["sha256"], {})
        return
    refusals = []
    for index, url in enumerate(entry["urls"]):
        if InstallJob[job.id].status != "running":  # stopped while an earlier URL was being tried
            return
        try:
            status, body = yield from fetched(url, MAX_CHAPTER)
            job = InstallJob[job.id]
            if job.status != "running":
                return
            receive_chapter(job, entry, index, status, body)
            return
        except Fail as e:
            refusals.append(str(e))
    raise Fail(f"{entry['chapter']}: " + "; ".join(refusals))


def receive_chapter(job, entry, index, status, body):
    """The reply to one of step 3's outcalls: the size and the checksum the change list gives, or a refusal.
    The bytes are stored now, and where and when they came from is noted for the codex row (§8)."""
    url = entry["urls"][index]
    text = decoded(url, status, body, MAX_CHAPTER)
    if len(body) != entry["size"]:
        raise Fail(f"{url}: {len(body)} bytes, the change list says {entry['size']}")
    try:
        store.store_chapter(entry["sha256"], text)
    except Fail as e:
        raise Fail(f"{url}: {e}")
    note_fetched(job, entry["sha256"], {"url": index})


def note_fetched(job, sha256, where):
    """Remember when the bytes of ``sha256`` came, and from which of the chapter's URLs (``where``), for the codex row."""
    staged = job.staged
    staged.setdefault("fetched", {})[sha256] = {**where, "at": chain.now()}
    job.staged = staged


def fetch_from_capital(job, sha256, most):
    """The text the capital stores under ``sha256``, of at most ``most`` bytes, kept here under it. False when the job
    was stopped while the call was out. A generator."""
    if not announced_by_my_capital(job):
        raise Fail(LEFT)
    reply = yield from quarters.ask(quarters.capital(), "legislation_chapter", {"chapter": sha256})
    if InstallJob[job.id].status != "running":
        return False
    if not reply.get("ok"):
        raise Fail(f"the capital does not serve {sha256}: {reply.get('error')}")
    text = reply["text"]
    if len(text.encode()) > most:
        raise Fail(f"{sha256} from the capital is above {most // 1024} KB")
    try:
        store.store_chapter(sha256, text)
    except Fail as e:
        raise Fail(f"{sha256} from the capital: {e}")
    return True


def check_sources(job):
    """Step 4: the change list against the records that vouch for its chapters (core.change_list.vouch), and every
    chapter of the new codex has its bytes stored: a layer from above that the records moved on without this
    change list is caught here."""
    planned, _, _ = vouched(job)
    missing = [chapter for chapter, item in change_list.named(planned) if ChapterSource[item["sha256"]] is None]
    if missing:
        raise Fail(f"no bytes stored for {', '.join(missing)}: a layer from above is installed as a release")
    return "build"


def build(job):
    """Step 5: every chapter of the new codex loaded and checked in a fresh sandbox, top to bottom, then its row,
    ``pending`` with the time it switches at. The row's id is the legislation's checksum: a codex that is the active
    one again is refused, and one the realm ran before takes its old row."""
    planned, vouched_by, listed = vouched(job)
    sources = {item["sha256"]: ChapterSource[item["sha256"]].source for _, item in change_list.named(planned)}
    out = legislation.build(planned, sources)
    if out["errors"]:
        raise Fail(out["errors"][0])
    entries = entries_of(job, planned, vouched_by, listed)
    codex_id = store.codex_id(planned.layers, planned.paths, entries)
    if announced(job) and codex_id != job.staged["announced"]["codex"]:
        raise Fail(f"the change list builds {codex_id}, not the {job.staged['announced']['codex']} the capital announced")
    current = codex.active()
    if current is not None and current.id == codex_id:
        raise Conflict(f"{codex_id} is already the active codex")
    if len(json.dumps(entries)) > JSON_MAX:
        raise Fail(f"a codex of {len(entries)} chapters with these URLs is more than the realm stores per codex")
    when = activates_at_of(job)
    row = LayeredCodex[codex_id]
    was = view(row, *REUSED) if row is not None else None  # a legislation the realm ran before keeps its row
    if row is None:
        row = LayeredCodex(id=codex_id)
    row.layers, row.layer_paths, row.chapters = planned.layers, planned.paths, entries
    row.status, row.activates_at, row.proposal, row.failures = "pending", when, job.proposal, {}
    job.staged = {**job.staged, "codex": codex_id, "was": was, "replaces": listed["replaces"]}
    from api import propagation  # it imports this module

    propagation.announce(job)
    if when > chain.now():
        return WAIT
    return "confirm" if announced(job) else "switch"


def confirm(job):
    """A quarter asks the capital which codex it requires now (``quarter_sync``: the one in force, or a pending one once
    due), at the switch time and after it, and goes on to the switch only if that is the codex it staged: one the capital has left, withdrawn, reverted or replaced by a newer, is never
    activated. A newer codex the capital announced is staged by its own job. A capital that does not answer leaves the
    quarter on its codex and the job paused, for an admin's ``codex_install_resume`` to ask again. A generator."""
    if not announced_by_my_capital(job):
        raise Fail(LEFT)
    try:
        reply = yield from quarters.ask(quarters.capital(), "quarter_sync", {})  # no report: nothing of this quarter changes
        if not reply.get("ok"):
            raise Fail(f"the capital refused: {reply.get('error')}")
    except Exception as e:  # noqa: BLE001  rejected, trapped or refused: the codex is not confirmed
        job = InstallJob[job.id]
        if job.status == "running":
            job.status, job.error = "paused", f"the capital did not confirm the codex: {e}"[:500]
        return None
    job = InstallJob[job.id]
    if job.status != "running":
        return None
    job.error = ""
    required, staged = reply["codex"]["id"], job.staged["announced"]["codex"]
    if required != staged:
        raise Fail(f"the capital requires {required or 'no codex'}, not {staged}: a codex it has left is not switched to")
    return "switch"


def finish(job):
    """Step 6, the switch: the pending codex becomes active and the previous one is kept, in this message. The
    active codex is checked first: already this one, nothing happens; another than the change list replaces, the
    install is stale and fails. The ``on_codex_activate`` hooks run in the message after (api.codex.switch): a hook
    never fails the switch (§9.3), and one that traps takes nothing of it with it."""
    waiting = LayeredCodex[job.staged["codex"]] if job.staged.get("codex") else None
    if waiting is None:
        raise Fail("nothing is staged")
    current = codex.active()
    if current is None or current.id != waiting.id:
        current_id = current.id if current else None
        if current_id != job.staged.get("replaces"):
            raise Fail(f"stale: the change list replaces {job.staged.get('replaces') or 'no codex'}, "
                       f"the active codex is {current_id or 'none'}")
        if waiting.status != "pending":
            raise Conflict(f"{waiting.id} is {waiting.status}, not pending")
        codex.switch(waiting)
    job.step, job.status, job.finished_at = "switch", "done", chain.now()


RUN = {"fetch_change_list": fetch_change_list, "check_change_list": check_change_list,
       "fetch_chapters": fetch_chapters, "check_sources": check_sources, "build": build, "confirm": confirm,
       "switch": finish}


# ---- What the steps share.


def fetched(url, most):
    """``(status, body)`` of a GET of ``url`` by HTTPS outcall, for a body of at most ``most`` bytes; a call that fails
    is a Fail naming the URL."""
    try:
        return (yield from chain.http_get(url, most + HEADROOM))
    except Exception as e:  # a failed call is a plain Exception, as chain.call raises it
        raise Fail(f"{url}: {e}")


def decoded(url, status, body, most):
    """The text of a reply: a 200 of at most ``most`` bytes of UTF-8."""
    if status != 200:
        raise Fail(f"{url}: HTTP {status}")
    if len(body) > most:
        raise Fail(f"{url}: {len(body)} bytes is above {most // 1024} KB")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise Fail(f"{url}: not UTF-8")


def prepared(job):
    """The stored change list, parsed and applied to this realm: ``(change list, the realm's source id, its own layer
    after the change)``. Steps 2 to 5 start here; it reads only stored rows, so it answers the same every time."""
    listed = change_list.parse(ChapterSource[job.change_list["checksum"]].source)
    current, realm = codex.active(), store.own_source()
    realm, own = change_list.apply(listed, realm, current.id if current else None, own_layer(current, realm))
    return listed, realm, own


def vouched(job):
    """``prepared`` and then the records: the plan of the new codex, who vouches for each chapter, the change list."""
    listed, realm, own = prepared(job)
    planned, vouched_by = change_list.vouch(listed, realm, relays(), own)
    return planned, vouched_by, listed


def own_layer(current, realm):
    """The realm's own layer in the active codex, in the shape ``plan`` takes: empty under a legacy codex or none."""
    chapters, calendar = {}, None
    if isinstance(current, LayeredCodex) and current.layers[-1] == realm:
        for chapter, entry in current.chapters.items():
            domain = chapter.rpartition("/")[2]
            if entry["source"] != realm:
                continue
            if domain == change_list.CALENDAR:
                calendar = entry["sha256"]
            else:
                chapters[domain] = entry["sha256"]
    return {"chapters": chapters, "calendar": calendar}


def activates_at_of(job):
    """When the codex switches: its proposal's ``effective_at``, or 0 for as soon as it is built; in a quarter, the time
    the capital announced."""
    if announced(job):
        return job.staged["announced"]["at"]
    proposal = Proposal[job.proposal] if job.proposal else None
    return proposal.effective_at if proposal is not None else 0


def entries_of(job, planned, vouched_by, listed):
    """What the codex row keeps per chapter (§8, §9.1): its source, version and checksum, who vouched for it, and
    how and when its bytes came: fetched by this job, as the active codex already had them, or found stored."""
    current = codex.active()
    before = current.chapters if isinstance(current, LayeredCodex) else {}
    urls = {entry["chapter"]: entry for entry in listed["set"]}
    fetched_now = job.staged.get("fetched", {})
    entries = {}
    for chapter, item in change_list.named(planned):
        sha256 = item["sha256"]
        if chapter in urls and sha256 in fetched_now:
            got = fetched_now[sha256]
            where = {"route": "https", "url": urls[chapter]["urls"][got["url"]]} if "url" in got else {
                "route": "capital", "url": ""}
            came = {**where, "fetched_at": got["at"], "size": urls[chapter]["size"]}
        elif chapter in before and before[chapter]["sha256"] == sha256:
            came = {k: before[chapter].get(k) for k in ("route", "url", "fetched_at", "size")}
        else:
            came = {"route": "stored", "url": "", "fetched_at": chain.now(),
                    "size": len(ChapterSource[sha256].source.encode())}
        entries[chapter] = {"source": item["source"], "version": item["version"], "sha256": sha256,
                            "vouched_by": vouched_by[chapter], **came}
    return entries
