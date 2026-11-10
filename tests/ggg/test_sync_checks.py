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
from ggg import FoundRelease, Proposal, Quarter, Realm, SyncSource
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
'''


def entry(version, effective_from, as_version=None):
    """Release ``version`` of arcadia as a record lists it, in force from ``effective_from``; ``as_version`` lists the
    same chapters under another version."""
    return {**arcadia.layer(version), "version": as_version or version, "published": 5,
            "effective_from": effective_from}


def source(*releases, publisher="arcadia"):
    """The capital ``SRC``, which accepts this realm, lists ``releases`` as the record of ``publisher``, serves their
    chapters and takes the follower's reports."""
    return peer(SRC, realm_info={"ok": True, "sync_sources": []}, link_notify={"ok": True, "status": "active"},
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
    capital = source(entry("1.0.0", 6))
    link()
    assert status()["check"] == {"interval": 24, "due": fake_chain.now() + DAY}
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


def test_a_quarter_checks_nothing_and_calls_no_other_realm():
    Realm["1"].is_quarter, Realm["1"].is_capital = True, False
    assert "a quarter calls no other realm" in denied("codex_check_now", "ctl")
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
    assert checked()["compliance"] == stale_since(6)
    peer(SRC, legislation_releases=rejects("Canister is stopped"), follower_report={"ok": True})
    check_now()
    assert checked()["compliance"] == "unknown" and checked()["checked_at"] == fake_chain.now()
    install(listing({"town/water": WATER.encode() + b"\n"}, realm="town", replaces=active_id(),
                    url=WEB + "changes/water-2.json"))
    fire_due()
    assert checked()["compliance"] == "unknown"


def test_the_flag_is_judged_against_each_record():
    record = {"releases": [entry("1.0.0", 6), entry("2.0.0", 100)]}
    assert compliance.judged(record, {"arcadia": "2.0.0"}, 50) == "compliant"
    assert compliance.judged(record, {"arcadia": "1.0.0"}, 50) == "compliant"  # 2.0.0 is not in effect yet
    assert compliance.judged(record, {"arcadia": "1.0.0"}, 100) == stale_since(100)
    assert compliance.judged(record, {}, 100) == stale_since(6)  # the earliest release in effect it misses
    assert compliance.judged(record, {"arcadia": "0.9.0"}, 7) == stale_since(6)  # a version the record does not list
    assert compliance.judged({"releases": []}, {}, 100) == "compliant"


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
    assert "no capital serves it and the release names no URL" in job()["error"]
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

