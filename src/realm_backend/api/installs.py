"""The install task of a layered codex (docs/codices/layered-codex.md §9.2).

``codex_install`` with a change list (GGG's ``Code { url; checksum }``), or with a release from a sync source (GGG's
``CodexSource.Release``, the "Propose install" of §11.4), starts an ``InstallJob`` and returns. A timer then runs one
step per message:

1. ``fetch_change_list``: the change list by HTTPS outcall, kept under the proposal's checksum. For a release there is
   none to fetch: the job reads the records of the sync sources and builds it from the one that vouches for the
   release (``build_release_list``), which leaves the realm's own layer unchanged;
2. ``check_change_list``: its rules, by itself and against this realm (core.change_list.apply);
3. ``fetch_chapters``: first, for a change list that brings a release, the records of the sync sources are read
   (core.records) and the one that vouches for it chosen; then each chapter not stored yet, one per message, from the
   capital of the sync source that vouches for its layer or from its URL or a mirror, kept under its checksum;
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
change list and of every chapter come from the capital, by checksum, as do the layers from above (the quarter reads no
records), and the switch is at the time the capital gave.
A quarter that is ready after that time (``confirm``) switches only if the capital still runs the codex.
"""

import json

from api import codex, links, quarters
from core import chain
from core import change_list
from core import layered_codex as store
from core import legislation
from core import records
from core.db import JSON_MAX, Conflict, Fail, each, find, first, next_id, view
from core.op import GENERATOR, audit, op
from ggg import ChapterSource, InstallJob, LayeredCodex, Proposal, Quarter

STEPS = ("fetch_change_list", "check_change_list", "fetch_chapters", "check_sources", "build", "switch")
WAIT = "wait"  # staged: the pending codex waits for its proposal's effective_at, which switches it
TIMEOUT = 24 * 3600
TIMED_OUT = "not done within 24 hours"
STEP_LIMIT = 15 * 60  # seconds a step may take, outcall included, before the job is given up
REUSED = ("status", "chapters", "proposal", "failures", "activates_at", "installed_at", "previous")  # of a row kept
MAX_CHANGE_LIST = 64 * 1024
MAX_CHAPTER = store.MAX_CHAPTER
HEX = "0123456789abcdef"
LEFT = "this realm is no longer a quarter of the capital that announced the codex"
MIXED = ("an install names a release (source and version) or a change list (url and checksum), not both: the realm "
         "installs the release and never reads the change list, so a vote would be told one install and the realm run "
         "another")
NO_OWN_LAYER = ("this realm has no layer of its own yet: its first layered codex names it, with a change list; a "
                "release is installed after that")
# A quarter stages what its capital announces: the change list, by its checksum, and the layers the capital staged. A
# release built from a record has no change list to announce, so a federation installs a release with one.
FEDERATED = "a federation installs a release with a change list: its quarters cannot stage one built from a record"
NO_ROUTE = ("no URL, and no realm vouches for it: a release that a stand-in record vouches for is installed with a change "
            "list that gives its chapters' URLs")


def job_of(proposal):
    """The newest install job of ``proposal``, if any."""
    found = None
    for job in each(InstallJob, "proposal", proposal):
        found = job
    return found


def release_of(args):
    """The release ``{source, version}`` that ``args`` name, if this realm can build its change list: one it could keep
    as a layer from above, in a realm that has a layer of its own (which the list keeps unchanged) and is no
    federation. Asked when the install is filed and again when it starts."""
    source, version = args["source"], args.get("version")
    if not isinstance(version, str) or not 0 < len(version) <= records.VERSION_MAX:
        raise Fail(f"version is 1 to {records.VERSION_MAX} characters")
    change_list.source_name(source)
    links.check_from_above(source, version)
    if store.own_source() is None:
        raise Fail(NO_OWN_LAYER)
    if quarters.federated() or first(Quarter, "status", "active") is not None:
        raise Conflict(FEDERATED)
    return {"source": source, "version": version}


def named_change(args):
    """What the job of ``codex_install``'s ``args`` installs, as the job keeps it: ``{release}``, or ``{url, checksum}``
    of a change list."""
    if change_list.names_release(args):
        return {"release": {"source": args["source"], "version": args.get("version")}}
    return {"url": args.get("url", ""), "checksum": args["checksum"]}


def start(proposal, args):
    """A new job for ``args``: a change list at ``url`` with its ``checksum`` (``sha256:<hex>``), or a release's
    ``source`` and ``version``; its first step and its 24-hour limit armed. Refused while another job runs or is
    paused."""
    if not proposal and change_list.names_both(args):  # a vote already carried one stored before it was refused: it runs
        raise Fail(MIXED)
    if change_list.names_release(args):
        release_of(args)
    else:
        checksum, url = args["checksum"], args.get("url", "")
        if not (isinstance(checksum, str) and checksum.startswith("sha256:") and len(checksum) == 71
                and all(ch in HEX for ch in checksum[7:])):
            raise Fail("checksum is sha256:<64 hex digits>")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise Fail("the change list is fetched over https")
    return launch(store.start_job(proposal, named_change(args)))


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
    """``codex_install`` with a change list ``url`` and its ``checksum``, or a release. For the scheduled proposal whose
    job staged the codex, this is the switch at its time; otherwise a job starts and the call returns ``job``."""
    job = job_of(c.proposal) if c.proposal else None
    if job is None:
        return {"job": start(c.proposal, args).id}
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
    if action.get("op") != "codex_install" or not ("checksum" in args or change_list.names_release(args)):
        return
    try:
        start(proposal.id, args)
    except Fail as e:
        InstallJob(id=next_id(InstallJob, "J"), proposal=proposal.id, change_list=named_change(args), status="failed",
                   error=str(e), started_at=chain.now(), finished_at=chain.now())


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
    """Step 1: the change list's bytes, unless they are stored already; for a release, the list built."""
    if "release" in job.change_list:
        yield from build_release_list(job)
        return "check_change_list" if InstallJob[job.id].status == "running" else None
    url, checksum = job.change_list["url"], job.change_list["checksum"]
    if ChapterSource[checksum] is None:
        if announced(job):
            if not (yield from fetch_from_capital(job, checksum, MAX_CHANGE_LIST)):
                return None
        else:
            status, body = yield from store.fetched(url, MAX_CHANGE_LIST)
            job = InstallJob[job.id]
            if job.status != "running":
                return None
            receive_change_list(job, status, body)
    return "check_change_list"


def receive_change_list(job, status, body):
    """The reply to step 1's outcall: the bytes the proposal's checksum names, stored under it."""
    url = job.change_list["url"]
    text = store.decoded(url, status, body, MAX_CHANGE_LIST)
    try:
        store.store_chapter(job.change_list["checksum"], text)
    except Fail as e:
        raise Fail(f"the change list at {url}: {e}")


def build_release_list(job):
    """Step 1 of a release install: the records of the sync sources read, and the change list built from the one that
    vouches for the release (core.change_list.of_release), staged with the layers they give, so that step 3 reads no
    record again. A generator."""
    release = release_of(job.change_list["release"])
    found = yield from records.read_active_records()
    job = InstallJob[job.id]
    if job.status != "running":
        return
    current = codex.active()
    vouching = change_list.vouching(found, release)
    job.staged = {**job.staged, "listed": change_list.of_release(store.own_source(), current.id if current else None,
                                                                   vouching["release"])}
    stage_layers(job, found)


def check_change_list(job):
    """Step 2: the change list's rules, by itself and against this realm (core.change_list.parse and apply)."""
    prepared(job)
    return "fetch_chapters"


def fetch_chapters(job):
    """Step 3: the records are read first, in the first message (``read_records``), unless the job has its layers
    already (a quarter's, from its capital); then the first chapter of the change list not stored yet, from the capital
    of the sync source that vouches for its layer, its URL or a mirror; the step runs again, in the next message, until
    none is missing."""
    listed, _, _ = prepared(job)
    if "layers" not in job.staged:
        yield from read_records(job, listed)
        return "fetch_chapters" if InstallJob[job.id].status == "running" else None
    for entry in listed["set"]:
        if ChapterSource[entry["sha256"]] is None:
            yield from fetch_chapter(job, entry)
            return "fetch_chapters" if InstallJob[job.id].status == "running" else None
    return "check_sources"


def read_records(job, listed):
    """Read the records of the sync sources, when the change list installs a release, and stage what step 4 and 5
    check against (``stage_layers``). A release this realm could not keep as a layer from above is refused first, before
    anything is read. A generator."""
    release = listed["release"]
    if release is not None:
        links.check_from_above(release["source"], release["version"])
    found = (yield from records.read_active_records()) if release is not None else []
    job = InstallJob[job.id]
    if job.status != "running":
        return
    stage_layers(job, found)


def stage_layers(job, found):
    """Stage the layers the new codex has (core.change_list.layers_for) from the ``found`` records: in the order the
    active codex runs them, who vouches for each, and the record that vouches for the release. Then the change list is
    checked against them at once, so that a release no record vouches for fails before any chapter is fetched."""
    listed, realm, _ = prepared(job)
    staged = job.staged
    staged["layers"] = change_list.layers_for(installed_layers(codex.active(), realm), found, listed["release"])
    job.staged = staged
    vouched(job)


def fetch_chapter(job, entry):
    """The bytes of ``entry``, stored in the message that receives them: in a quarter, from the capital that announced
    the codex; else from the capital of the sync source that vouches for the layer, when a realm does, else from the
    first of its URLs that serves the right ones. A capital that serves other bytes than the record names is noted
    (``refused_by_capital``) before the URLs are tried. A generator."""
    if announced(job):
        if (yield from fetch_from_capital(job, entry["sha256"], MAX_CHAPTER)):
            stage_fetched(InstallJob[job.id], entry, {"route": "capital"})
        return
    refusals = []
    capital = vouching_capital(job, entry)
    if capital is not None:
        try:
            reply = yield from records.ask_realm(capital, "legislation_chapter", {"chapter": entry["sha256"]})
            job = InstallJob[job.id]
            if job.status != "running":
                return
            receive_from_capital(job, entry, capital, reply.get("text"))
            return
        except Fail as e:
            refusals.append(str(e))
    for index, url in enumerate(entry["urls"]):
        if InstallJob[job.id].status != "running":  # stopped while an earlier route was being tried
            return
        try:
            status, body = yield from store.fetched(url, MAX_CHAPTER)
            job = InstallJob[job.id]
            if job.status != "running":
                return
            receive_chapter(job, entry, index, status, body)
            return
        except Fail as e:
            refusals.append(str(e))
    raise Fail(f"{entry['chapter']}: " + ("; ".join(refusals) or NO_ROUTE))


def vouching_capital(job, entry):
    """The capital to ask for the chapter ``entry``: the sync source that vouches for the release it is a part of, when
    that is a realm. None for the realm's own chapters and for a stand-in record, which have only their URLs."""
    vouching = job.staged["layers"]["vouching"]  # fetch_chapters has staged the layers before it fetches
    if vouching is None or entry["source"] != vouching["release"]["source"]:
        return None
    return vouching["key"] if records.is_realm_key(vouching["key"]) else None


def receive_from_capital(job, entry, capital, text):
    """The capital's answer for one chapter: the size and the checksum the change list gives, or a refusal."""
    if not isinstance(text, str):
        raise Fail(f"{capital}: no text for {entry['sha256']}")
    if entry["size"] is not None and len(text.encode()) != entry["size"]:
        raise refused_by_capital(capital, entry, f"{len(text.encode())} bytes, the change list says {entry['size']}")
    try:
        store.store_chapter(entry["sha256"], text)
    except Fail as e:
        raise refused_by_capital(capital, entry, str(e))
    stage_fetched(job, entry, {"route": "capital"})


def refused_by_capital(capital, entry, why):
    """Note in the audit log that a sync source's capital served other bytes than its record names, and return the
    Fail that sends the install on to the chapter's URLs."""
    audit("install_chapter_refused", "", capital, "install",
          {"source": capital, "chapter": entry["chapter"], "sha256": entry["sha256"]}, {"error": why})
    return Fail(f"{capital}: {why}")


def receive_chapter(job, entry, index, status, body):
    """The reply to one of step 3's outcalls: the size and the checksum the change list gives, or a refusal.
    The bytes are stored now, and where and when they came from is noted for the codex row (§8)."""
    url = entry["urls"][index]
    text = store.decoded(url, status, body, MAX_CHAPTER)
    if len(body) != entry["size"]:
        raise Fail(f"{url}: {len(body)} bytes, the change list says {entry['size']}")
    try:
        store.store_chapter(entry["sha256"], text)
    except Fail as e:
        raise Fail(f"{url}: {e}")
    stage_fetched(job, entry, {"route": "https", "url": index})


def stage_fetched(job, entry, came):
    """Note how and when the bytes of ``entry`` arrived: ``came`` is its route, and for https the index of its URL."""
    staged = job.staged
    staged.setdefault("fetched", {})[entry["sha256"]] = {**came, "at": chain.now()}
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

    if "release" not in job.change_list:  # a release is installed only where no quarter stages it (``release_of``)
        propagation.announce(job)
    if when > chain.now():
        return WAIT
    return "confirm" if announced(job) else "switch"


def confirm(job):
    """A quarter asks the capital which codex it requires now (``quarter_sync``: the one in force, or a pending one once
    due), at the switch time and after it, and goes on to the switch only if that is the codex it staged: one the
    capital has left, withdrawn, reverted or replaced by a newer, is never activated. A newer codex the capital
    announced is staged by its own job. A capital that does not answer leaves the quarter on its codex and the job
    paused, for an admin's ``codex_install_resume`` to ask again. A generator."""
    if not announced_by_my_capital(job):
        raise Fail(LEFT)
    try:
        reply = yield from quarters.ask(quarters.capital(), "quarter_sync", {})  # an empty report: no change to its record
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


def vouching_of(row):
    """The record that vouches for the release the install of ``row`` brings, or None: a job begun before the layers
    were staged brings none."""
    job = store.live_job()
    staged = job.staged if job is not None and job.staged.get("codex") == row.id else {}
    return staged.get("layers", {}).get("vouching")


def check_relayed(row):
    """Before the switch to ``row`` changes anything: refuse the release its install brings if the realm cannot keep it
    as a layer from above any more (api.links.check_from_above). A publish can come between the install's checks and
    its switch, and a switch that fails after the runtime has loaded the codex leaves the two apart."""
    vouching = vouching_of(row)
    if vouching is not None:
        links.check_from_above(vouching["release"]["source"], vouching["release"]["version"])


def keep_relayed(row):
    """At the switch to ``row``: if its install brought a release, this realm's record lists it, as the record that
    vouches for it lists it (api.links.relay_release)."""
    vouching = vouching_of(row)
    if vouching is not None:
        links.relay_release(vouching["release"], vouching["order"])


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


def prepared(job):
    """The stored change list, parsed, or the one a release install built, applied to this realm: ``(change list, the
    realm's source id, its own layer after the change)``. Steps 2 to 5 start here; it reads only stored rows, so it
    answers the same every time."""
    if "release" in job.change_list:
        listed = job.staged["listed"]
    else:
        listed = change_list.parse(ChapterSource[job.change_list["checksum"]].source)
    current, realm = codex.active(), store.own_source()
    realm, own = change_list.apply(listed, realm, current.id if current else None, own_layer(current, realm))
    return listed, realm, own


def vouched(job):
    """``prepared`` and then the layers the job staged: the plan of the new codex, who vouches for each chapter, the
    change list."""
    listed, realm, own = prepared(job)
    if "layers" not in job.staged:  # begun before they were staged: no record was read, and a release finds none
        stage_layers(job, [])
    layers = job.staged["layers"]
    planned, vouched_by = change_list.vouch(listed, realm, layers["relays"], own, layers["vouchers"])
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


def installed_layers(current, realm):
    """The layers from above that the active codex runs, top to bottom, as it runs them: each ``{source, version,
    published, chapters, calendar, vouched_by}``, the release it installed, when its publisher published it (None for a
    layer installed before that was kept) and the source whose record vouched for it. None under a legacy codex or
    none."""
    if not isinstance(current, LayeredCodex):
        return []
    layers = {}
    for chapter, entry in current.chapters.items():
        source, _, domain = chapter.rpartition("/")
        if source == realm:
            continue
        layer = layers.setdefault(source, {"source": source, "version": entry["version"], "chapters": {},
                                           "calendar": None, "vouched_by": entry["vouched_by"],
                                           "published": entry.get("published")})
        if domain == change_list.CALENDAR:
            layer["calendar"] = entry["sha256"]
        else:
            layer["chapters"][domain] = entry["sha256"]
    return [layers[source] for source in current.layers if source in layers]


def activates_at_of(job):
    """When the codex switches: its proposal's ``effective_at``, or 0 for as soon as it is built; in a quarter, the time
    the capital announced."""
    if announced(job):
        return job.staged["announced"]["at"]
    proposal = Proposal[job.proposal] if job.proposal else None
    return proposal.effective_at if proposal is not None else 0


def entries_of(job, planned, vouched_by, listed):
    """What the codex row keeps per chapter (§8, §9.1): its source, version and checksum, who vouched for it, when the
    release it belongs to was published (``published``: compliance orders releases by it, §11.6), and how and when its
    bytes came: fetched by this job, as the active codex already had them, or found stored."""
    current = codex.active()
    before = current.chapters if isinstance(current, LayeredCodex) else {}
    urls = {entry["chapter"]: entry for entry in listed["set"]}
    published = published_times(job, before)
    fetched_now = job.staged.get("fetched", {})
    entries = {}
    for chapter, item in change_list.named(planned):
        sha256 = item["sha256"]
        if chapter in urls and sha256 in fetched_now:
            now = fetched_now[sha256]
            route = now.get("route", "https")  # a job begun before the route was noted fetched by URL alone
            came = {"route": route, "url": urls[chapter]["urls"][now["url"]] if route == "https" else "",
                    "fetched_at": now["at"], "size": stored_size(sha256)}
        elif chapter in before and before[chapter]["sha256"] == sha256:
            came = {k: before[chapter].get(k) for k in ("route", "url", "fetched_at", "size")}
        else:
            came = {"route": "stored", "url": "", "fetched_at": chain.now(), "size": stored_size(sha256)}
        entries[chapter] = {"source": item["source"], "version": item["version"], "sha256": sha256,
                            "vouched_by": vouched_by[chapter], **came}
        if published.get((item["source"], item["version"])) is not None:
            entries[chapter]["published"] = published[(item["source"], item["version"])]
    return entries


def published_times(job, before):
    """When the publisher published the release of each layer from above that the new codex runs, ``{(source, version):
    seconds}``: the release this job installs, as the record that vouches for it lists it (every record entry carries
    it, and a relay copies it), and the releases the active codex already runs, as it kept them. A layer installed
    before the time was kept has none: ``sync_checks.running_layers`` looks for it among the releases a check found, and
    compliance reads a version it cannot place as no reason to be behind (§11.6)."""
    times = {(entry["source"], entry["version"]): entry.get("published") for entry in before.values()}
    vouching = job.staged.get("layers", {}).get("vouching")
    if vouching is not None:
        release = vouching["release"]
        times[(release["source"], release["version"])] = release["published"]
    return times


def stored_size(sha256):
    """The size of the bytes stored under ``sha256``: the change list's when it gives one, which they were checked
    against, and for a chapter a realm served, the only size there is."""
    return len(ChapterSource[sha256].source.encode())
