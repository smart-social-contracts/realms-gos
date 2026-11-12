"""The scheduled check of the sync sources, "Propose install", compliance and follower reports (docs/codices/
layered-codex.md §11; realms-gos#492), in process. The sync source's capital is a scripted peer whose record lists
releases of ``arcadia`` (tests/fixtures/legislation); the realm is ``town``, with a layer of its own.
tests/deployed/test_sync_checks.py runs the same between real realms."""

import hashlib
import json

import fake_chain
import pytest
import world
from api import quarters, setup, sync_checks
from core import change_list, compliance, legislation, op
from fake_chain import peer, rejects
from fixtures import legislation as arcadia
from ggg import FoundRelease, LayeredCodex, Notification, Proposal, Quarter, Realm, SyncSource
from kit import denied, fire_timers, later, ok
from test_installs import FOUR, WEB, active_id, fire_due, job, run_job, status
from test_installs import change_list as listing

SRC, ME = "src", fake_chain.SELF
HOUR, DAY = 3600, 86400
POINTER = "https://legislation.example.test/arcadia/record.json"
WATER = 'from realms.legislation import above\n\n\nclass Water(above("water")):\n    pass\n'
# The town's own legislation makes every proposal wait 15 days and refuses one titled "blocked": neither applies to a
# release from above, whose date the source's legislation set.
STATUTORY_WAIT = '''
from realms.legislation import F, above, forbid

class Water(above("water")):
    no_blocked = forbid("proposal_create", F.title == "blocked", legislation="the town's own rule")

    def check_proposal_create(self, args):
        if args.effective_at is None or args.effective_at < self.now() + 15 * 86400:
            return self.deny("a proposal takes effect 15 days after it is filed, at the earliest")

    def on_proposal_create(self, event):
        self.ggg.notification_send(recipient="User:bob", topic="filed", title="a proposal was filed",
                                   body=event.result["proposal"])
'''


def entry(version, effective_from, as_version=None, published=5):
    """Release ``version`` of arcadia as a record lists it, published at ``published`` and in force from
    ``effective_from``; ``as_version`` lists the same chapters under another version."""
    return {**arcadia.layer(version), "version": as_version or version, "published": published,
            "effective_from": effective_from}


def source(*releases, publisher="arcadia", cid=SRC):
    """The capital ``cid``, which accepts this realm, lists ``releases`` as the record of ``publisher``, serves their
    chapters and takes the follower's reports."""
    return peer(cid, realm_info={"ok": True, "sync_sources": []}, link_notify={"ok": True, "status": "active"},
                legislation_releases={"ok": True, "releases": list(releases), "next": None, "source": publisher},
                legislation_chapter=lambda a: {"ok": True, "text": arcadia.chapter(a["chapter"]).decode()},
                follower_report={"ok": True, "follower": ME})


def own_layer(text=WATER):
    """The town's own layer, installed with a change list: the realm has its source id from then on."""
    install(listing({"town/water": text.encode()}, realm="town", url=WEB + "changes/water.json"))
    assert job()["status"] == "done", job()


def install(args):
    ok("codex_install", "ctl", **args)
    run_job()


def link():
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Arcadia")
    assert SyncSource[SRC].status == "active"


def propose(version, title="Install arcadia", votes="yes", **extra):
    """A member's "Propose install" of arcadia ``version``, voted ``votes`` by four members, and the install it runs."""
    p = ok("proposal_create", "alice", title=title,
           action={"op": "codex_install", "args": {"source": "arcadia", "version": version}}, **extra)["proposal"]
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=p, choice=votes)
    run_job()
    return Proposal[p]


def check_now():
    """A forced check, and the timers due with it: the scheduled check, a day away, stays."""
    ok("codex_check_now", "ctl")
    fire_due()


def checked():
    """What the last check found for the sync source, as codex_status shows it."""
    return next(s for s in status()["sync_sources"] if s["source"] == SRC)


def running():
    return {c["version"] for k, c in status()["codex"]["chapters"].items() if k.startswith("arcadia/")}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def stale_since(at):
    return {"NonCompliant": {"since": at, "stale": ["arcadia"]}}


@pytest.fixture(autouse=True)
def realm():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    yield
    legislation.load(None)


# ---- When the realm checks


def test_the_realm_checks_from_its_first_sync_source_on_every_interval_the_admins_set():
    assert status()["check"] == {"interval": 24, "due": 0}  # no sync source, no check
    assert ok("codex_check_config", "ctl", interval=6) == {"ok": True, "interval": 6, "due": 0}  # kept for later
    assert fake_chain.state["timers"] == []
    capital = source(entry("1.0.0", 6))
    link()
    assert status()["check"] == {"interval": 6, "due": fake_chain.now() + 6 * HOUR}
    due = fake_chain.now() + 2 * HOUR
    assert ok("codex_check_config", "ctl", interval=2) == {"ok": True, "interval": 2, "due": due}
    for bad in (0, 169, "2", 1.5):
        assert "interval is a whole number of hours, from 1 to 168" in denied("codex_check_config", "ctl", interval=bad)
    assert "needs realm.configure" in denied("codex_check_config", "alice", interval=3)
    assert "needs realm.configure" in denied("codex_check_now", "alice")

    later(2 * HOUR)
    fire_timers()  # the check set for 24 hours was replaced: only the one set for 2 hours runs
    assert len(capital.sent("legislation_releases")) == 1 and checked()["checked_at"] == fake_chain.now()
    assert status()["check"]["due"] == fake_chain.now() + 2 * HOUR  # and it set the next


def test_a_forced_check_runs_at_once_and_the_interval_counts_from_it():
    capital = source(entry("1.0.0", 6))
    link()
    later(HOUR)
    assert ok("codex_check_now", "ctl") == {"ok": True}
    assert capital.sent("legislation_releases") == []  # in the next message
    fire_due()
    assert len(capital.sent("legislation_releases")) == 1
    assert status()["check"]["due"] == fake_chain.now() + DAY


def test_the_next_check_is_set_again_at_the_realms_start_for_its_time():
    capital = source(entry("1.0.0", 6))
    link()
    due = status()["check"]["due"]
    fake_chain.state["timers"].clear()  # an upgrade drops the timers
    later(HOUR)
    setup.start()
    assert [secs for secs, _ in fake_chain.state["timers"]] == [due - fake_chain.now()]
    fire_timers()
    assert len(capital.sent("legislation_releases")) == 1


def test_a_realm_that_had_sync_sources_before_it_had_checks_starts_them_at_its_start():
    SyncSource(id=SRC, name="Arcadia", status="active")
    setup.start()
    assert status()["check"]["due"] == fake_chain.now() + DAY and len(fake_chain.state["timers"]) == 1


def test_a_quarter_checks_nothing_and_calls_no_other_realm():
    Realm["1"].is_quarter, Realm["1"].is_capital = True, False
    assert "a quarter calls no other realm" in denied("codex_check_now", "ctl")
    assert "a quarter calls no other realm" in denied("codex_check_config", "ctl", interval=2)
    capital = source(entry("1.0.0", 6))
    SyncSource(id=SRC, name="Arcadia", status="active")
    op.run(sync_checks.check())
    assert capital.sent("legislation_releases") == []


def test_the_check_operations_are_reserved():
    for name in ("codex_check_config", "codex_check_now"):
        assert legislation.is_reserved(name, {}) and name in sync_checks.__dict__


# ---- Compliance


def test_compliance_follows_the_record_and_only_an_install_or_a_revert_changes_it():
    own_layer()
    source(entry("1.0.0", 6))
    link()
    check_now()  # the realm runs no arcadia: in effect since 6, it is behind
    assert checked()["compliance"] == stale_since(6)
    assert checked()["installable"] == [{"source": "arcadia", "version": "1.0.0", "effective_from": 6}]

    assert propose("1.0.0").status == "executed" and running() == {"1.0.0"}
    fire_due()  # judged again right after the install
    assert checked()["compliance"] == "compliant" and checked()["installable"] == []
    first = active_id()

    soon = fake_chain.now() + DAY
    source(entry("1.0.0", 6), entry("2.0.0", soon))
    check_now()  # published, not yet in effect
    assert checked()["compliance"] == "compliant"
    assert checked()["installable"] == [{"source": "arcadia", "version": "2.0.0", "effective_from": soon}]

    later(2 * DAY)
    check_now()  # past its effective date, the older pin is behind
    assert checked()["compliance"] == stale_since(soon)

    later_still = fake_chain.now() + DAY
    source(entry("1.0.0", 6), entry("2.0.0", soon), entry("2.0.0", later_still, as_version="2.1.0"))
    check_now()  # the record only moves forward: a check never clears the flag
    assert checked()["compliance"] == stale_since(soon)

    assert propose("2.0.0").status == "executed" and running() == {"2.0.0"}
    fire_due()
    assert checked()["compliance"] == "compliant"  # the install clears it at once

    revert = ok("proposal_create", "alice", title="back", action={"op": "codex_revert", "args": {"codex": first}})
    for voter in FOUR:
        ok("proposal_vote", voter, proposal=revert["proposal"], choice="yes")
    assert running() == {"1.0.0"}
    fire_due()
    assert checked()["compliance"] == stale_since(soon)  # a revert below a release in effect


def test_a_record_that_cannot_be_read_is_unknown_and_an_install_leaves_it_so():
    own_layer()
    source(entry("1.0.0", 6))
    link()
    check_now()
    assert checked()["compliance"] == stale_since(6) and checked()["error"] == ""
    peer(SRC, legislation_releases=rejects("Canister is stopped"), follower_report={"ok": True})
    check_now()
    assert checked()["compliance"] == "unknown" and checked()["checked_at"] == fake_chain.now()
    assert "Canister is stopped" in checked()["error"]
    install(listing({"town/water": WATER.encode() + b"\n"}, realm="town", replaces=active_id(),
                    url=WEB + "changes/water-2.json"))
    fire_due()
    assert checked()["compliance"] == "unknown"


def test_each_source_is_checked_on_its_own_and_one_that_fails_is_unknown_with_the_reason():
    own_layer()
    many = [{**entry("1.0.0", 6), "source": f"far/l{n}"} for n in range(17)]  # more layers than a chain can have
    broken = peer("broken", realm_info={"ok": True, "sync_sources": []}, link_notify={"ok": True, "status": "active"},
                  legislation_releases={"ok": True, "releases": many, "next": None, "source": "far"})
    ok("sync_source_add", "ctl", source={"Realm": "broken"}, name="Broken")  # checked first: its link came first
    capital = source(entry("1.0.0", 6))
    link()
    check_now()
    found = {s["source"]: s for s in status()["sync_sources"]}
    assert broken.sent("legislation_releases") and found["broken"]["compliance"] == "unknown"
    assert "a record lists at most 16 layers" in found["broken"]["error"]
    assert found[SRC]["compliance"] == stale_since(6) and found[SRC]["error"] == ""  # still checked and judged
    assert capital.sent("follower_report")


def test_an_unexpected_error_in_one_sources_check_leaves_the_others_checked(monkeypatch):
    own_layer()
    source(entry("1.0.0", 6))
    link()
    source(entry("1.0.0", 6), cid="other")
    ok("sync_source_add", "ctl", source={"Realm": "other"}, name="Other")
    judge = sync_checks.judge

    def broken_for_src(link, record):
        if link.id == SRC:
            raise KeyError("a bug")
        judge(link, record)

    monkeypatch.setattr(sync_checks, "judge", broken_for_src)
    check_now()
    found = {s["source"]: s for s in status()["sync_sources"]}
    assert (found[SRC]["compliance"], found[SRC]["error"]) == ("unknown", "'a bug'")
    assert (found["other"]["compliance"], found["other"]["error"]) == (stale_since(6), "")


def runs(version, published=None, by="arcadia"):
    """What the realm runs of arcadia: release ``version``, which its publisher published at ``published`` (None for a
    layer installed before that was kept), as the record of ``by`` vouched for it."""
    return {"arcadia": {"version": version, "published": published, "vouched_by": by}}


def listed(version, published, effective_from=None):
    """Release ``version`` as a record lists it, published at ``published``, in force from ``effective_from`` (the time
    it was published, if none): the fixture's chapters under that version."""
    effective_from = published if effective_from is None else effective_from
    return entry("1.0.0", effective_from, as_version=version, published=published)


def test_the_flag_is_judged_against_each_record():
    record = {"source": "arcadia", "releases": [entry("1.0.0", 6), entry("2.0.0", 100)]}  # both published at 5
    assert compliance.judged(record, runs("2.0.0"), 50) == "compliant"
    assert compliance.judged(record, runs("1.0.0"), 50) == "compliant"  # 2.0.0 is not in effect yet
    assert compliance.judged(record, runs("1.0.0"), 100) == stale_since(100)
    assert compliance.judged(record, {}, 100) == stale_since(6)  # the earliest release in effect it misses
    assert compliance.judged({"releases": []}, {}, 100) == "compliant"
    # a version the record does not list is placed by the time it was published: 1.0.0 came after 0.9.0 (at 3)
    assert compliance.judged(record, runs("0.9.0", 3), 7) == stale_since(6)
    assert compliance.judged(record, runs("0.9.0", 5), 7) == "compliant"  # published together: a tie places nothing
    assert compliance.judged(record, runs("0.9.0", 6), 7) == "compliant"  # published after both
    # and one the realm kept no time for cannot be placed: never behind
    assert compliance.judged(record, runs("0.9.0"), 7) == "compliant"


def test_a_version_the_record_does_not_list_is_never_a_reason_to_catch_up_to_an_older_release():
    """The realm runs 2.6.0 or 9.9.9 and the record lists 2.3.0 and 2.4.0: compliant, with a note, and nothing older
    offered as catching up. Running 2.3.0 is behind 2.4.0, running 2.4.0 is compliant (§11.6)."""
    record = {"source": "arcadia", "releases": [listed("2.3.0", 100), listed("2.4.0", 200)]}
    two_four = {"source": "arcadia", "version": "2.4.0", "effective_from": 200}

    def judge(running):
        return (compliance.judged(record, running, 1000), compliance.installable(record, running),
                compliance.notes(record, running))

    assert judge(runs("2.3.0", 100)) == (stale_since(200), [two_four], [])
    assert judge(runs("2.4.0", 200)) == ("compliant", [], [])
    assert judge(runs("2.3.0")) == (stale_since(200), [two_four], [])  # listed: placed by the record, no time needed
    unlisted = {"source": "arcadia", "kind": "unlisted", "runs": "2.6.0", "lists": "2.4.0"}
    assert judge(runs("2.6.0")) == ("compliant", [], [unlisted])  # no time kept: cannot be placed
    assert judge(runs("2.6.0", 300, by="elsewhere")) == ("compliant", [], [unlisted])  # not this source's to go back on
    assert judge(runs("9.9.9", 300, by="elsewhere")) == ("compliant", [], [{**unlisted, "runs": "9.9.9"}])
    assert judge(runs("9.9.9")) == ("compliant", [], [{**unlisted, "runs": "9.9.9"}])
    # published between the two: 2.4.0 came after what the realm runs, so it is behind it
    assert judge(runs("2.3.5", 150)) == (stale_since(200), [two_four], [])
    # published together with the newest it lists: a tie, which places nothing: a note, never behind, never a step back
    assert judge(runs("2.6.0", 200)) == ("compliant", [], [unlisted])


def test_effective_dates_do_not_order_releases_but_the_times_they_were_published_do():
    """The reviewer's two cases (§11.6). Releases of one publication take effect together: a relay that moves from 2.5.0
    to 2.6.0 is the realm catching up, not the source going back. And an urgent 2.5.1, published after 2.6.0, can take
    effect before it: a relay that goes back to 2.6.0 is a source going back, not the realm being behind."""
    # 2.5.0 took effect at 1000 too
    together = {"source": "arcadia", "releases": [listed("2.6.0", 300, effective_from=1000)]}
    six = {"source": "arcadia", "version": "2.6.0", "effective_from": 1000}
    running = runs("2.5.0", 250)
    assert compliance.judged(together, running, 500) == "compliant"  # 2.6.0 is not in effect yet
    assert compliance.installable(together, running) == [six]
    assert compliance.notes(together, running) == []  # no step back, no note: it is newer
    assert compliance.judged(together, running, 2000) == stale_since(1000)  # in effect: behind, offered as catching up
    assert compliance.installable(together, running) == [six]

    urgent = runs("2.5.1", 400)  # published after 2.6.0 (300), in force at 50, before it (1000)
    back = {"source": "arcadia", "releases": [listed("2.6.0", 300, effective_from=1000)]}
    assert compliance.judged(back, urgent, 2000) == "compliant"  # by effective date 2.6.0 would be newer: behind
    assert compliance.notes(back, urgent) == [
        {"source": "arcadia", "kind": "went_back", "runs": "2.5.1", "lists": "2.6.0"}]
    assert compliance.installable(back, urgent) == [
        {"source": "arcadia", "version": "2.6.0", "effective_from": 1000, "step_back": True}]
    # published at the same time, in force at different times: a tie, a note only
    tie = runs("2.5.1", 300)
    assert compliance.judged(back, tie, 2000) == "compliant"
    assert compliance.installable(back, tie) == []
    assert compliance.notes(back, tie) == [{"source": "arcadia", "kind": "unlisted", "runs": "2.5.1", "lists": "2.6.0"}]


def test_a_source_that_went_back_is_followed_never_behind_and_its_older_release_is_a_step_back():
    """The record lists only 2.4.0 of a layer the source vouched for as 2.6.0, published later: the source went back.
    The flag stays compliant; the note says so; "Propose install" offers 2.4.0, marked as a step back (§11.6)."""
    record = {"source": "arcadia", "releases": [listed("2.4.0", 200)]}
    running = runs("2.6.0", 300)
    assert compliance.judged(record, running, 1000) == "compliant"
    assert compliance.notes(record, running) == [
        {"source": "arcadia", "kind": "went_back", "runs": "2.6.0", "lists": "2.4.0"}]
    assert compliance.installable(record, running) == [
        {"source": "arcadia", "version": "2.4.0", "effective_from": 200, "step_back": True}]
    # the same record, when another source vouched for what the realm runs: the version is only one it does not list
    assert compliance.notes({**record, "source": "elsewhere"}, running) == [
        {"source": "arcadia", "kind": "unlisted", "runs": "2.6.0", "lists": "2.4.0"}]
    assert compliance.installable({**record, "source": "elsewhere"}, running) == []


def test_an_installed_release_keeps_the_time_it_was_published_and_a_source_going_back_is_a_note():
    own_layer()
    source(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=20))
    link()
    check_now()
    assert checked()["compliance"] == stale_since(6)
    assert propose("2.0.0").status == "executed" and running() == {"2.0.0"}
    entries = status()["codex"]["chapters"]
    assert {c["published"] for k, c in entries.items() if k.startswith("arcadia/")} == {20}
    assert all("published" not in c and "effective_from" not in c for k, c in entries.items() if k.startswith("town/"))
    fire_due()
    assert checked()["compliance"] == "compliant" and checked()["installable"] == [] and checked()["notes"] == []

    source(entry("1.0.0", 6, published=5))  # the source went back: it now relays 1.0.0 only, published before 2.0.0
    check_now()
    found = checked()
    assert found["compliance"] == "compliant"  # followed, never behind
    assert found["notes"] == [{"source": "arcadia", "kind": "went_back", "runs": "2.0.0", "lists": "1.0.0"}]
    assert found["installable"] == [{"source": "arcadia", "version": "1.0.0", "effective_from": 6, "step_back": True}]

    source(entry("1.0.0", 6, published=20))  # published together with what the realm runs: a tie, a note only
    check_now()
    found = checked()
    assert found["compliance"] == "compliant" and found["installable"] == []
    assert found["notes"] == [{"source": "arcadia", "kind": "unlisted", "runs": "2.0.0", "lists": "1.0.0"}]

    # an own-layer install keeps the time of the layer from above it leaves as it is
    install(listing({"town/water": WATER.encode() + b"\n"}, realm="town", replaces=active_id(),
                    url=WEB + "changes/water-2.json"))
    assert {c["published"] for k, c in status()["codex"]["chapters"].items() if k.startswith("arcadia/")} == {20}


def test_a_codex_installed_before_the_time_was_kept_finds_it_among_the_releases_a_check_found():
    """Every codex installed before `published` was kept has none on its chapters: the realm looks for the release among
    those a check read in a record (``FoundRelease``), so that a relay moving forward is still behind on an old
    install; one no check ever found cannot be placed, and is a note (§11.6)."""
    own_layer()
    source(entry("1.0.0", 6, published=5))
    link()
    check_now()  # logs 1.0.0 as found
    assert propose("1.0.0").status == "executed" and running() == {"1.0.0"}
    fire_due()
    row = LayeredCodex[active_id()]
    row.chapters = {k: {f: v for f, v in c.items() if f != "published"} for k, c in row.chapters.items()}  # an old install
    assert all("published" not in c for c in LayeredCodex[active_id()].chapters.values())

    source(entry("2.0.0", 50, published=20))  # the relay moved forward: it lists 2.0.0 only, which was published later
    check_now()
    found = checked()
    assert found["compliance"] == stale_since(50) and found["notes"] == []
    assert found["installable"] == [{"source": "arcadia", "version": "2.0.0", "effective_from": 50}]  # not a step back

    FoundRelease["arcadia@1.0.0"].delete()  # a release no check ever found: nothing to place it by
    check_now()
    found = checked()
    assert found["compliance"] == "compliant" and found["installable"] == []
    assert found["notes"] == [{"source": "arcadia", "kind": "unlisted", "runs": "1.0.0", "lists": "2.0.0"}]


def filed(version, source="arcadia"):
    """What ``proposal_get`` says of the release change of a proposal a member files by hand to install release ``version``
    of ``source``: no vote, and no help from the codex page's button."""
    action = {"op": "codex_install", "args": {"source": source, "version": version}}
    pid = ok("proposal_create", "alice", title=f"Install {source} {version}", action=action)["proposal"]
    return ok("proposal_get", "alice", query=True, proposal=pid).get("release_change")


def runs_releases(*releases):
    """The realm follows a source that lists ``releases``, runs the last of them (installed by a vote), and has checked."""
    own_layer()
    source(*releases)
    link()
    check_now()
    assert propose(releases[-1]["version"]).status == "executed" and running() == {releases[-1]["version"]}
    fire_due()


def test_a_release_filed_by_hand_that_is_older_than_what_the_realm_runs_is_marked_a_step_back():
    """The realm runs arcadia 2.0.0 and the record lists 1.0.0 and 2.0.0: nothing offers 1.0.0 (only newer releases are
    installable) and no note is made, but a member can file its install by hand, and every reader of ``proposal_get``
    is told it takes the realm back (§11.6)."""
    runs_releases(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=20))
    assert checked()["installable"] == [] and checked()["notes"] == []  # the rule is as it was
    assert filed("1.0.0") == {"source": "arcadia", "version": "1.0.0", "runs": "2.0.0", "published": 5,
                              "runs_published": 20, "order": "older", "step_back": True}


def test_a_downgrade_along_the_publishers_own_record_is_marked_too():
    """The realm runs 2.6.0 and the record lists 2.3.0 and 2.6.0: filing 2.3.0 by hand is a step back."""
    runs_releases(listed("2.3.0", 100), listed("2.6.0", 300))
    assert checked()["installable"] == [] and checked()["notes"] == []
    change = filed("2.3.0")
    assert (change["runs"], change["order"], change["step_back"]) == ("2.6.0", "older", True)


def test_a_release_published_together_with_what_the_realm_runs_is_a_tie_and_not_marked():
    runs_releases(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=5))
    change = filed("1.0.0")
    assert (change["order"], change["step_back"], change["published"], change["runs_published"]) == ("tied", False, 5, 5)


def test_a_time_that_is_not_known_places_nothing_and_is_not_marked():
    runs_releases(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=20))
    # a release no check ever found (the proposed one) cannot be placed
    change = filed("9.9.9")
    assert (change["order"], change["step_back"], change["published"], change["runs_published"]) == ("unknown", False, None, 20)
    # nor can an old install that kept no time and whose release no check found
    row = LayeredCodex[active_id()]
    row.chapters = {k: {f: v for f, v in c.items() if f != "published"} for k, c in row.chapters.items()}
    FoundRelease["arcadia@2.0.0"].delete()
    change = filed("1.0.0")
    assert (change["order"], change["step_back"], change["published"], change["runs_published"]) == ("unknown", False, 5, None)


def test_an_old_install_that_kept_no_time_is_placed_by_the_release_a_check_found():
    runs_releases(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=20))
    row = LayeredCodex[active_id()]
    row.chapters = {k: {f: v for f, v in c.items() if f != "published"} for k, c in row.chapters.items()}
    change = filed("1.0.0")
    assert (change["order"], change["step_back"], change["runs_published"]) == ("older", True, 20)


def test_a_forward_install_by_hand_is_not_marked_and_one_the_realm_has_run_is_the_same():
    runs_releases(entry("1.0.0", 6, published=5))
    source(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=20))
    check_now()  # 2.0.0 is found
    change = filed("2.0.0")
    assert (change["order"], change["step_back"], change["runs"]) == ("newer", False, "1.0.0")
    assert (filed("1.0.0")["order"], filed("1.0.0")["step_back"]) == ("same", False)  # the release the realm runs now


def test_a_proposal_that_installs_no_release_of_a_layer_the_realm_runs_says_nothing_of_one():
    runs_releases(entry("1.0.0", 6, published=5), entry("2.0.0", 50, published=20))
    assert filed("1.0.0", source="elsewhere") is None  # a layer the realm runs none of
    other = ok("proposal_create", "alice", title="Something else", action={"op": "governance_update", "args": {"voting_window": 0}})["proposal"]
    assert "release_change" not in ok("proposal_get", "alice", query=True, proposal=other)
    plain = ok("proposal_create", "alice", title="No action")["proposal"]
    assert "release_change" not in ok("proposal_get", "alice", query=True, proposal=plain)


def test_the_order_of_two_release_times():
    assert [compliance.order_of(*pair) for pair in ((5, 20), (20, 5), (5, 5), (None, 5), (5, None), (None, None))] == [
        "older", "newer", "tied", "unknown", "unknown", "unknown"]


def test_the_change_list_of_a_release_is_the_whole_release_with_its_calendar_and_without_the_realm_chapter():
    release = {**entry("1.0.0", 6), "chapters": {**entry("1.0.0", 6)["chapters"], "realm": "sha256:" + "c" * 64},
               "calendar": "sha256:" + "d" * 64}
    built = change_list.of_release("town", "sha256:" + "e" * 64, release)
    assert [(e["chapter"], e["sha256"], e["urls"], e["size"]) for e in built["set"]] == [
        ("arcadia/calendar", "sha256:" + "d" * 64, [], None),
        ("arcadia/procedure", release["chapters"]["procedure"], [], None),
        ("arcadia/tax", release["chapters"]["tax"], [], None)]
    assert (built["realm"], built["replaces"], built["remove"], built["release"]) == (
        "town", "sha256:" + "e" * 64, [], {"source": "arcadia", "version": "1.0.0"})
    # a follower's layer and the record's entry for it have one checksum, whatever the realm chapter
    assert change_list.layer_checksum(release) == change_list.layer_checksum({**release, "chapters": {
        d: sha for d, sha in release["chapters"].items() if d != "realm"}})


# ---- What the check keeps and tells


def test_the_source_receives_the_followers_report():
    own_layer()
    capital = source(entry("1.0.0", 6))
    link()
    propose("1.0.0")
    check_now()
    layer = {"version": "1.0.0", "checksum": change_list.layer_checksum(entry("1.0.0", 6))}  # found in its record
    assert capital.sent("follower_report")[-1] == {"layers": [layer], "compliance": "compliant",
                                                   "population": quarters.population()}
    # A realm publishes a release with no calendar as "" (the town relays arcadia now, to followers of its own), and
    # the follower reads it as None: both give the layer one checksum.
    published = ok("legislation_releases", "stranger")["releases"]
    assert [(r["source"], r["calendar"]) for r in published] == [("arcadia", "")]
    assert entry("1.0.0", 6)["calendar"] is None
    assert change_list.layer_checksum(published[0]) == layer["checksum"]
    Quarter(id="q1", status="active", population=40)
    check_now()
    assert capital.sent("follower_report")[-1]["population"] == quarters.population() + 40


def test_a_report_the_source_refuses_does_not_fail_the_check():
    source(entry("1.0.0", 6)).answer("follower_report", {"ok": False, "error": "not a follower"})
    link()
    check_now()
    assert checked()["checked_at"] == fake_chain.now()


def test_the_releases_a_check_finds_are_listed_once_with_when_they_were_found():
    source(entry("1.0.0", 6))
    link()
    check_now()
    first_found = fake_chain.now()
    later(HOUR)
    source(entry("1.0.0", 6), entry("2.0.0", 100))
    check_now()
    check_now()
    found = ok("codex_releases_found", "stranger")
    assert [(r["version"], r["found_in"], r["found_at"]) for r in found["releases"]] == [
        ("1.0.0", SRC, first_found), ("2.0.0", SRC, fake_chain.now())]
    assert found["releases"][1] == {"source": "arcadia", "version": "2.0.0", "published": 5, "effective_from": 100,
                                    "notes": {"en": "The property tax rate rises."}, "found_in": SRC,
                                    "found_at": fake_chain.now()}
    assert found["next"] is None and FoundRelease.count() == 2


# ---- Propose install


def test_a_propose_install_runs_no_guard_or_forbid_of_the_codex_and_the_vote_decides():
    own_layer(STATUTORY_WAIT)
    source(entry("1.0.0", 6))
    link()
    assert "15 days" in denied("proposal_create", "alice", title="anything")  # the town's own guard
    assert "the town's own rule" in denied("proposal_create", "alice", title="blocked",
                                           effective_at=fake_chain.now() + 16 * DAY)

    rejected = propose("1.0.0", title="blocked", votes="no")
    assert rejected.status == "rejected" and running() == set()
    accepted = propose("1.0.0", title="blocked")
    assert accepted.status == "executed" and running() == {"1.0.0"}
    assert accepted.proposer == "alice"
    fire_due()  # a hook's effects run in the messages after the call
    told = [n.body for n in Notification.load_some(1, Notification.max_id() or 1) if n.topic == "filed"]
    assert rejected.id in told and accepted.id in told  # the town's hook ran on both proposal_creates


def test_a_propose_install_builds_the_whole_release_and_keeps_the_realms_own_layer():
    own_layer()
    capital = source(entry("1.0.0", 6))
    link()
    before = status()["codex"]["chapters"]["town/water"]
    propose("1.0.0")
    seen = status()["codex"]
    assert seen["layers"] == ["arcadia", "town"] and seen["chapters"]["town/water"] == before
    tax = seen["chapters"]["arcadia/tax"]
    assert (tax["route"], tax["vouched_by"], tax["size"]) == ("capital", "arcadia", len(arcadia.chapter(tax["sha256"])))
    assert len(capital.sent("legislation_releases")) == 1  # read once, in the step that builds the list


def test_a_propose_install_is_refused_where_the_realm_cannot_build_it():
    source(entry("1.0.0", 6))
    link()
    action = {"op": "codex_install", "args": {"source": "arcadia", "version": "1.0.0"}}
    assert "no layer of its own yet" in denied("proposal_create", "alice", title="x", action=action)
    own_layer()
    Quarter(id="q1", status="active")
    assert "its quarters cannot stage one built from a record" in denied("proposal_create", "alice", title="x",
                                                                         action=action)
    Quarter["q1"].delete()
    for bad, why in (({"version": ""}, "version is 1 to 64"), ({"source": "Not A Source"}, "Not A Source"),
                     ({"version": "1@2"}, "hold no '@'")):
        args = {**action["args"], **bad}
        assert why in denied("proposal_create", "alice", title="x", action={**action, "args": args})


def test_a_release_only_a_stand_in_record_vouches_for_is_refused_naming_the_chapter_and_the_way_out():
    own_layer()
    text = json.dumps({"source": "arcadia", "releases": [entry("1.0.0", 6)]})
    fake_chain.serve(POINTER, text.encode())
    ok("sync_source_add", "ctl", source={"Record": {"url": POINTER, "checksum": "sha256:" + sha(text)}},
       name="Arcadia")
    propose("1.0.0")
    assert job()["status"] == "failed" and running() == set()
    assert "arcadia/procedure: no URL, and no realm vouches for it" in job()["error"]
    assert "installed with a change list that gives its chapters' URLs" in job()["error"]


def test_a_release_the_record_does_not_list_fails_the_install():
    own_layer()
    source(entry("1.0.0", 6))
    link()
    propose("2.0.0")
    assert job()["status"] == "failed" and "relays arcadia 1.0.0, not 2.0.0" in job()["error"]


def test_a_scheduled_propose_install_stages_at_acceptance_and_switches_at_its_time():
    own_layer()
    source(entry("1.0.0", 6))
    link()
    p = propose("1.0.0", effective_at=fake_chain.now() + HOUR)
    assert p.status == "execution_scheduled" and status()["pending"] is not None and running() == set()
    later(HOUR)
    fire_timers()
    assert Proposal[p.id].status == "executed" and running() == {"1.0.0"}

