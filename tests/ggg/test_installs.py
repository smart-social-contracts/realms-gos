"""The install task of a layered codex (docs/codices/layered-codex.md §9.2), in process: the job runs step by step on
the fake chain's timers, its outcalls answered from ``fake_chain.serve``. The realm is ``arcadia`` installing its own
layer from tests/fixtures/legislation. tests/deployed/test_installs.py runs the same on the real wasm."""

import hashlib
import json

import fake_chain
import pytest
import world
from api import installs, setup
from core import layered_codex as store
from core import legislation
from core import op
from fixtures import legislation as arcadia
from ggg import ChapterSource, InstallJob, LayeredCodex, Proposal
from kit import call, denied, fire_timers, later, ok

TRAPPING_REALM_CHAPTER = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_codex_activate(self, event):
        self.ggg.t_trap_after_call(codex=event.args["codex"])
'''


@op.op("treasury.manage")
def t_trap_after_call(c, args):
    """A hook's effect that calls another canister and then fails: the message traps after its await."""
    yield from fake_chain.call("elsewhere", "ping")
    raise ValueError("the effect broke after its call")

HOUR = 3600
WEB = "https://legislation.example.test/"
LIST_URL = WEB + "changes/arcadia/first.json"
FOUR = ("alice", "bob", "dana", "carla")  # more than half of the seven members: enough to carry a vote
REALM_CHAPTER = '''
from realms.legislation import above

class Realm(above("realm")):
    def on_codex_activate(self, event):
        self.ggg.notification_send(recipient="User:alice", topic="codex", title="activated", body=event.args["codex"])
'''
BROKEN_CHAPTER = "from realms.legislation import above\n\nclass Tax(above('procedure')):\n    pass\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def chapter_text(domain, version="1.0.0"):
    return arcadia.chapter(next(r for r in arcadia.releases() if r["version"] == version)["chapters"][domain])


def own_layer(version="1.0.0", realm_chapter=REALM_CHAPTER):
    """``{chapter id: bytes}`` of arcadia's own layer: its release's chapters and the realm domain's."""
    layer = {f"arcadia/{d}": chapter_text(d, version) for d in ("tax", "procedure")}
    if realm_chapter:
        layer["arcadia/realm"] = realm_chapter.encode()
    return layer


def change_list(chapters, realm="arcadia", replaces=None, url=LIST_URL, release=None, remove=(), serve=True):
    """A change list for ``chapters`` (``{chapter id: bytes}``), served with every chapter; returns the ``codex_install``
    arguments that name it."""
    entries = []
    for chapter, body in chapters.items():
        chapter_url = WEB + chapter + ".py"
        if serve:
            fake_chain.serve(chapter_url, body)
        entries.append({"chapter": chapter, "url": chapter_url, "mirrors": [], "sha256": sha(body), "size": len(body)})
    text = json.dumps({"kind": "realms-gos/legislation-change", "format": 1, "realm": realm, "replaces": replaces,
                       "set": entries, "remove": list(remove), "release": release, "summary": {"en": "test"}}).encode()
    fake_chain.serve(url, text)
    return {"url": url, "checksum": "sha256:" + sha(text)}


def expected_id(chapters, realm="arcadia"):
    paths = {c.rpartition("/")[2]: [realm] for c in chapters}
    entries = {c: {"sha256": "sha256:" + sha(body)} for c, body in chapters.items()}
    return store.codex_id([realm], dict(sorted(paths.items())), entries)


def fire_due():
    """Fire the timers due now, the job's steps, and leave the delayed ones (a proposal's time, the 24-hour watch)."""
    due, fake_chain.state["timers"] = [cb for secs, cb in fake_chain.state["timers"] if not secs], [
        (secs, cb) for secs, cb in fake_chain.state["timers"] if secs]
    for cb in due:
        out = cb()
        if out is not None:
            op.run(out)


def run_job(rounds=40):
    """Run the job's steps, one per round, until none is due."""
    for _ in range(rounds):
        if not any(not secs for secs, _ in fake_chain.state["timers"]):
            return
        fire_due()
    raise AssertionError("the job is still running")


def run_until(step, rounds=40):
    for _ in range(rounds):
        if job()["step"] == step:
            return
        fire_due()
    raise AssertionError(f"the job never reached {step}: {job()}")


def status():
    return ok("codex_status", "alice")


def job():
    return status()["job"]


def active_id():
    codex = status()["codex"]
    return codex["id"] if codex else None


@pytest.fixture
def realm():
    world.build()  # in setup: the founder's direct install, and the root department decides directly
    ok("governance_update", "ctl", voting_window=0)


def install(args, who="ctl"):
    out = ok("codex_install", who, **args)
    run_job()
    return out


def schedule(args, effective_in=HOUR):
    """A realm-wide proposal to install ``args``, accepted now, to take effect in ``effective_in`` seconds."""
    p = ok("proposal_create", "alice", title="Legislation", action={"op": "codex_install", "args": args},
           effective_at=fake_chain.now() + effective_in)["proposal"]
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=p, choice="yes")
    assert Proposal[p].status == "execution_scheduled"
    return p


# ---- the install


def test_a_realm_installs_its_own_layer_from_a_url_and_the_realm_hook_runs_at_the_switch(realm):
    layer = own_layer()
    out = install(change_list(layer))
    assert out == {"ok": True, "job": "J-1"}
    seen = status()
    assert seen["codex"]["id"] == expected_id(layer) and seen["codex"]["status"] == "active"
    assert seen["codex"]["layers"] == ["arcadia"] and seen["codex"]["layer_paths"] == {
        "procedure": ["arcadia"], "realm": ["arcadia"], "tax": ["arcadia"]}
    tax = seen["codex"]["chapters"]["arcadia/tax"]
    assert (tax["source"], tax["version"], tax["vouched_by"], tax["route"], tax["url"]) == (
        "arcadia", None, "arcadia", "https", WEB + "arcadia/tax.py")
    assert tax["sha256"] == "sha256:" + sha(layer["arcadia/tax"]) and tax["size"] == len(layer["arcadia/tax"])
    assert tax["fetched_at"] == fake_chain.now() and seen["codex"]["installed_at"] == fake_chain.now()
    assert seen["job"] == {"id": "J-1", "proposal": "", "step": "switch", "status": "done", "error": "",
                           "started_at": fake_chain.now(), "finished_at": fake_chain.now()}
    assert seen["pending"] is None and seen["healthy"]
    assert installs.own_source() == "arcadia"
    assert [n["title"] for n in ok("notification_list", "alice")["notifications"]] == ["activated"]
    assert ChapterSource.count() == 4  # the change list and three chapters
    assert [u for kind, u, _ in fake_chain.state["calls"] if kind == "https"] == [
        LIST_URL, WEB + "arcadia/tax.py", WEB + "arcadia/procedure.py", WEB + "arcadia/realm.py"]


def test_a_second_change_list_replaces_the_active_codex_and_skips_the_chapters_it_has(realm):
    first = own_layer()
    install(change_list(first))
    before = len(fake_chain.state["calls"])
    kept = {"arcadia/tax": chapter_text("tax", "2.0.0"), "arcadia/realm": first["arcadia/realm"]}
    install(change_list(kept, replaces=expected_id(first), url=WEB + "changes/second.json",
                        remove=["arcadia/procedure"]))
    seen = status()
    assert seen["codex"]["id"] == expected_id(kept) and seen["codex"]["previous"] == expected_id(first)
    assert sorted(seen["codex"]["chapters"]) == ["arcadia/realm", "arcadia/tax"]
    assert seen["codex"]["chapters"]["arcadia/realm"]["route"] == "https"  # as the first install fetched it
    assert [u for kind, u, _ in fake_chain.state["calls"][before:] if kind == "https"] == [
        WEB + "changes/second.json", WEB + "arcadia/tax.py"]  # procedure and realm were stored already
    assert [h["id"] for h in seen["history"]] == [expected_id(first), expected_id(kept)]
    assert ok("codex_revert", "ctl")["codex"] == expected_id(first)


def test_a_scheduled_install_stages_at_acceptance_and_switches_at_its_time_not_before(realm):
    layer = own_layer()
    args = change_list(layer)
    p = schedule(args)
    assert job()["status"] == "running" and job()["step"] == "fetch_change_list"  # staging started at acceptance
    run_job()
    seen = status()
    assert seen["codex"] is None and seen["job"]["step"] == "wait" and seen["job"]["status"] == "running"
    assert seen["pending"]["id"] == expected_id(layer) and seen["pending"]["status"] == "pending"
    assert seen["pending"]["activates_at"] == Proposal[p].effective_at and seen["pending"]["proposal"] == p
    assert installs.own_source() is None  # the realm's id is set when the codex is in force
    later(HOUR - 1)
    fire_due()  # nothing is due: the switch waits for its time
    assert active_id() is None and Proposal[p].status == "execution_scheduled"
    later(1)
    fire_timers()
    assert Proposal[p].status == "executed" and Proposal[p].result["codex"] == expected_id(layer)
    seen = status()
    assert seen["codex"]["id"] == expected_id(layer) and seen["pending"] is None
    assert seen["job"]["status"] == "done" and seen["job"]["step"] == "switch"
    assert ok("notification_list", "alice")["notifications"][-1]["title"] == "activated"


def test_a_second_install_while_one_runs_is_refused_and_so_is_a_legacy_one(realm):
    from test_codex import SOURCE, manifest

    args = change_list(own_layer())
    ok("codex_install", "ctl", **args)
    other = call("codex_install", "ctl", **change_list(own_layer(), url=WEB + "changes/other.json"))
    assert (other["kind"], other["error"]) == ("conflict", "an install is running")
    legacy = call("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert (legacy["kind"], legacy["error"]) == ("conflict", "an install is running")
    run_job()
    assert active_id() == expected_id(own_layer()) and InstallJob.count() == 1


# ---- failures: the codex is unchanged, the job says why, and the same change list installs once mended


def failing(args, reason, who="ctl"):
    install(args, who)
    seen = status()
    assert seen["job"]["status"] == "failed", seen["job"]
    assert reason in seen["job"]["error"], seen["job"]["error"]
    assert seen["pending"] is None
    return seen


def test_a_change_list_whose_bytes_are_not_the_proposals_checksum(realm):
    args = change_list(own_layer())
    fake_chain.serve(LIST_URL, b'{"kind": "realms-gos/legislation-change", "format": 1}')
    failing(args, f"the change list at {LIST_URL}: the text is sha256:")
    assert active_id() is None and ChapterSource.count() == 0


def test_a_change_list_nobody_serves_or_one_above_64_kb(realm):
    args = change_list(own_layer(), serve=True)
    del fake_chain.state["web"][LIST_URL]
    failing(args, f"{LIST_URL}: HTTP 404")
    fake_chain.serve(LIST_URL, b"x" * (64 * 1024 + 1))
    failing(args, f"{LIST_URL}: 65537 bytes is above 64 KB")
    assert active_id() is None


def test_a_change_list_for_another_realm_once_the_realms_id_is_set(realm):
    install(change_list(own_layer()))
    first = active_id()
    failing(change_list(own_layer("2.0.0"), realm="elsewhere", replaces=first, url=WEB + "changes/x.json"),
            "the change list is for elsewhere; this realm is arcadia")
    assert active_id() == first


def test_a_stale_replaces(realm):
    failing(change_list(own_layer(), replaces="sha256:" + "0" * 64), "stale change list")
    install(change_list(own_layer(), url=WEB + "changes/ok.json"))
    first = active_id()
    failing(change_list(own_layer("2.0.0"), url=WEB + "changes/stale.json"), "stale change list: it replaces no codex")
    assert active_id() == first


def test_a_release_from_a_source_the_realm_does_not_sync_from(realm):
    listed = {"eu/es/tax": chapter_text("tax")}
    failing(change_list(listed, release={"source": "eu/es", "version": "1.0.0"}), "no sync source relays eu/es")
    failing(change_list(listed, url=WEB + "changes/partial.json"), "installed whole, as a release")
    assert active_id() is None


def test_a_chapter_whose_bytes_changed_then_the_same_change_list_once_they_are_back(realm):
    layer = own_layer()
    args = change_list(layer)
    fake_chain.serve(WEB + "arcadia/tax.py", layer["arcadia/tax"] + b"\n# edited\n")
    failing(args, f"{WEB}arcadia/tax.py: {len(layer['arcadia/tax']) + 10} bytes, the change list says")
    fake_chain.serve(WEB + "arcadia/tax.py", layer["arcadia/tax"][:-1] + b"#")  # the size, not the checksum
    failing(args, f"{WEB}arcadia/tax.py: the text is sha256:")
    assert active_id() is None and ChapterSource.count() == 1  # the change list; the fetching stopped at tax
    fake_chain.serve(WEB + "arcadia/tax.py", layer["arcadia/tax"])
    install(args)
    assert active_id() == expected_id(layer) and InstallJob.count() == 3


def test_a_mirror_serves_what_the_first_url_does_not(realm):
    layer = own_layer()
    change_list(layer)
    listed = json.loads(fake_chain.state["web"][LIST_URL][1])
    listed["set"][0]["mirrors"] = [WEB + "mirror/tax.py"]
    text = json.dumps(listed).encode()
    fake_chain.serve(LIST_URL, text)
    fake_chain.serve(WEB + "mirror/tax.py", layer["arcadia/tax"])
    del fake_chain.state["web"][WEB + "arcadia/tax.py"]
    install({"url": LIST_URL, "checksum": "sha256:" + sha(text)})
    assert status()["codex"]["chapters"]["arcadia/tax"]["url"] == WEB + "mirror/tax.py"


def test_a_chapter_the_checks_refuse(realm):
    failing(change_list({**own_layer(), "arcadia/tax": BROKEN_CHAPTER.encode()}),
            "arcadia/tax.py:3: above('procedure') in the tax chapter: a chapter extends above(\"tax\")")
    assert active_id() is None and LayeredCodex.count() == 0


def test_a_change_list_that_changes_nothing_or_the_active_legislation_again(realm):
    layer = own_layer()
    install(change_list(layer))
    first = active_id()
    failing(change_list(layer, replaces=first, url=WEB + "changes/again.json"), f"{first} is already the active codex")
    assert active_id() == first and LayeredCodex.count() == 1


def test_a_codex_with_more_chapters_than_the_realm_stores_per_codex(realm, monkeypatch):
    monkeypatch.setattr(installs, "JSON_MAX", 500)
    failing(change_list(own_layer()), "more than the realm stores per codex")
    assert LayeredCodex.count() == 0


def test_a_job_still_running_at_the_switch_time_fails_the_proposal_and_never_finishes(realm):
    args = change_list(own_layer())
    p = schedule(args, effective_in=600)
    later(600)
    out = ok("proposal_run_scheduled", "bob", proposal=p)  # the switch, while the job has not run a step
    assert out["status"] == "failed" and "not done at the switch time: it was running in fetch_change_list" in out["result"]["error"]
    assert job()["status"] == "failed"
    run_job()  # the step that was armed finds the job failed and does nothing
    assert status()["pending"] is None and active_id() is None and LayeredCodex.count() == 0


def test_a_stopped_scheduled_install_leaves_no_pending_codex(realm):
    args = change_list(own_layer())
    p = schedule(args)
    run_job()
    assert status()["pending"] is not None
    ok("proposal_stop", "ctl", proposal=p)
    seen = status()
    assert seen["pending"] is None and seen["job"]["status"] == "failed" and seen["job"]["error"] == f"{p} was stopped"
    assert LayeredCodex.count() == 0
    later(HOUR)
    fire_timers()
    assert active_id() is None and Proposal[p].status == "withdrawn"


def test_a_scheduled_install_that_cannot_stage_says_so_at_its_time(realm):
    ok("codex_install", "ctl", **change_list(own_layer()))  # a job is running
    p = schedule(change_list(own_layer(), url=WEB + "changes/second.json"))
    blocked = installs.job_of(p)
    assert (blocked.status, blocked.error) == ("failed", "an install is running")
    run_job()
    later(HOUR)
    fire_timers()
    assert Proposal[p].status == "failed" and "an install is running" in Proposal[p].result["error"]
    assert active_id() == expected_id(own_layer())


def test_a_job_not_done_within_24_hours_fails(realm):
    args = change_list(own_layer())
    ok("codex_install", "ctl", **args)
    later(installs.TIMEOUT + 1)
    fire_timers()
    assert job()["status"] == "failed" and job()["error"] == "not done within 24 hours"
    assert active_id() is None
    install(args)  # again, in time
    assert active_id() == expected_id(own_layer())


# ---- an upgrade in the middle


def test_an_upgrade_pauses_the_job_and_only_an_admin_resumes_it_to_the_same_result(realm):
    layer = own_layer()
    args = change_list(layer)
    ok("codex_install", "ctl", **args)
    run_until("fetch_chapters")
    fake_chain.state["timers"] = []  # an upgrade loses the timers
    setup.start()
    assert job()["status"] == "paused" and job()["step"] == "fetch_chapters"
    assert "codex.install" in denied("codex_install_resume", "alice", job="J-1")
    assert call("codex_install_resume", "ctl", job="J-2")["kind"] == "not_found"
    out = ok("codex_install_resume", "ctl", job="J-1")
    assert out == {"ok": True, "job": "J-1", "step": "fetch_chapters"}
    assert call("codex_install_resume", "ctl", job="J-1")["kind"] == "conflict"
    run_job()
    assert active_id() == expected_id(layer) and job()["status"] == "done"


def test_an_upgrade_while_a_staged_codex_waits_for_its_time_changes_nothing(realm):
    p = schedule(change_list(own_layer()))
    run_job()
    fake_chain.state["timers"] = []
    setup.start()  # the proposal's schedule is armed again, the job stays as it is
    assert job()["status"] == "running" and job()["step"] == "wait"
    later(HOUR)
    fire_timers()
    assert Proposal[p].status == "executed" and active_id() == expected_id(own_layer())


def test_an_unexpected_exception_in_a_step_is_recorded_as_the_jobs_failure(realm, monkeypatch):
    def broken(job):
        raise TypeError("a bug in a step")

    monkeypatch.setitem(installs.RUN, "check_change_list", broken)
    ok("codex_install", "ctl", **change_list(own_layer()))
    run_job()
    assert (job()["status"], job()["step"], job()["error"]) == ("failed", "check_change_list", "TypeError: a bug in a step")
    assert active_id() is None


def test_a_step_that_never_finishes_is_given_up_after_its_time(realm):
    ok("codex_install", "ctl", **change_list(own_layer()))
    fire_due()  # step 1 done, step 2 armed: its watch is the delayed timer left
    armed = job()["step"]
    fake_chain.state["timers"] = [(secs, cb) for secs, cb in fake_chain.state["timers"] if secs]  # the step is lost
    fire_timers()  # its watch fires: the step was armed and never finished
    assert job()["status"] == "failed" and job()["error"] == f"step {armed} did not complete (likely the instruction limit)"
    assert active_id() is None


def test_a_watch_whose_step_finished_in_time_does_nothing(realm):
    ok("codex_install", "ctl", **change_list(own_layer()))
    run_job()  # every step done: the watches are the delayed timers left over
    assert job()["status"] == "done"
    later(installs.STEP_LIMIT)
    fire_timers()
    assert job()["status"] == "done" and active_id() == expected_id(own_layer())


def test_a_failed_install_of_legislation_the_realm_ran_before_gives_its_row_back_whole(realm, monkeypatch):
    first, second = own_layer(), {**own_layer(), "arcadia/tax": chapter_text("tax", "2.0.0")}
    install(change_list(first))
    install(change_list(second, replaces=expected_id(first), url=WEB + "changes/second.json"))
    ok("codex_revert", "ctl")  # back to the first: the second's row stays, reverted
    before = LayeredCodex[expected_id(second)]
    assert before.status == "reverted" and before.proposal == ""
    kept = dict((k, getattr(before, k)) for k in installs.REUSED)
    # Installing the second again, now with a URL of its own and a failure at the switch: the row is as it was.
    monkeypatch.setattr(installs, "activates_at_of", lambda job: 0)
    args = change_list({"arcadia/tax": second["arcadia/tax"]}, replaces=expected_id(first), url=WEB + "changes/third.json")
    ok("codex_install", "ctl", **args)
    run_until("switch")
    row = LayeredCodex[expected_id(second)]
    assert row.status == "pending" and row.chapters["arcadia/tax"]["route"] == "stored"  # the row was rewritten
    assert kept["chapters"]["arcadia/tax"]["route"] == "https"
    ok("codex_uninstall", "ctl")  # the active codex changes under the staged switch: it is stale
    fire_due()
    assert job()["status"] == "failed" and "stale" in job()["error"]
    row = LayeredCodex[expected_id(second)]
    assert {k: getattr(row, k) for k in installs.REUSED} == kept


def test_the_switch_stands_when_a_hook_traps_after_a_call_it_made(realm):
    layer = {**own_layer(realm_chapter=TRAPPING_REALM_CHAPTER)}
    ok("codex_install", "ctl", **change_list(layer))
    run_until("switch")
    with pytest.raises(fake_chain.Trap, match="the effect broke after its call"):
        fire_due()
    # The fake chain keeps every write of the trapped message; the real one keeps those made before the await, and
    # the switch writes all come before the hooks run (installs.finish).
    seen = status()
    assert seen["codex"]["id"] == expected_id(layer) and seen["job"]["status"] == "done"
    assert installs.own_source() == "arcadia"


def test_a_switch_whose_codex_cannot_load_leaves_the_active_codex_and_the_runtime_as_they_were(realm, monkeypatch):
    first = own_layer()
    install(change_list(first))
    args = change_list({**first, "arcadia/tax": chapter_text("tax", "2.0.0")}, replaces=expected_id(first),
                       url=WEB + "changes/second.json")
    spawn = legislation._spawn

    def failing_spawn(plan, sources, facts=None):
        if any(c["sha256"] == "sha256:" + sha(chapter_text("tax", "2.0.0")) for c in plan["chapters"]) and facts is None:
            raise legislation.Fail("no room")
        return spawn(plan, sources, facts)

    ok("codex_install", "ctl", **args)
    run_until("switch")  # through the build, which gets a real sandbox
    monkeypatch.setattr(legislation, "_spawn", failing_spawn)
    fire_due()
    seen = status()
    assert seen["codex"]["id"] == expected_id(first) and seen["codex"]["status"] == "active"
    assert seen["job"]["status"] == "failed" and seen["job"]["error"] == "no room"
    assert legislation._state["codex"] == expected_id(first) and legislation._state["box"] is not None
    assert LayeredCodex.count() == 1
