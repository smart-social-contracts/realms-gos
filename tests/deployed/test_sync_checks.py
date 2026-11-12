"""Scheduled checks of the sync sources, "Propose install", compliance and follower reports on the real wasm
(docs/codices/layered-codex.md §11; realms-gos#492): realms of one wasm on one PocketIC instance. ``source`` relays
releases of ``arcadia`` (tests/fixtures/legislation) and publishes a layer of its own; ``town`` follows it, with a layer
of its own whose legislation makes every proposal wait 15 working days. tests/ggg/test_sync_checks.py runs the same
rules in process.

The realms are built once for the module and the tests below use them in order.
"""

import hashlib
import json

import pytest
from network import Network
from test_installs import Site, active_id, chapter_text, settled, status
from test_release_installs import BOTH, PUBLISH, entry, link, source_with
from test_sync_sources import RELAY, STORE

HOUR = 3600
# The town's own legislation: 15 working days on the town's calendar before any proposal takes effect, and no
# proposal titled "blocked". Neither applies to a release from above (§11.4).
STATUTORY_WAIT = '''
from realms.legislation import F, above, forbid, working_days

class Water(above("water")):
    no_blocked = forbid("proposal_create", F.title == "blocked", legislation="the town's own rule")

    def check_proposal_create(self, args):
        earliest = self.calendar.add(self.local_day(self.now()), working_days(15))
        if args.effective_at is None or self.local_day(args.effective_at) < earliest:
            return self.deny("a proposal takes effect at the earliest 15 working days after it is filed")
'''
# The PocketIC clock starts in 2021: the calendar covers the years the count reaches.
CALENDAR = json.dumps({"weekend": ["sat", "sun"], "years": {
    "2021": [{"date": "2021-12-25", "name": {"en": "Christmas"}}],
    "2022": [{"date": "2022-01-01", "name": {"en": "New Year"}}]}})


def relay(realm, version, effective_from, published=5):
    """The record of ``realm`` relays release ``version`` of arcadia, published at ``published``, in force from
    ``effective_from``."""
    texts = [chapter_text(d, version).decode() for d in BOTH]
    release = {**entry(version), "effective_from": effective_from, "published": published}
    out = realm.shell(STORE % {"texts": texts} + RELAY % {"release": release, "order": ["arcadia", "x/source"]})
    assert "Traceback" not in out, out


def checked(realm):
    """What the last check found for the realm's one sync source."""
    found = status(realm)["sync_sources"]
    assert len(found) == 1, found
    return found[0]


def check_now(net, realm):
    """A forced check, run to its end. Returns what it found."""
    before = checked(realm)["checked_at"]
    realm.ok("codex_check_now", "ctl")
    net.wait_until(lambda: checked(realm)["checked_at"] > before, rounds=2)
    return checked(realm)


def running(realm):
    return {c["version"] for k, c in status(realm)["codex"]["chapters"].items() if k.startswith("arcadia/")}


def file_install(realm, version, title="Install arcadia"):
    """Alice presses "Propose install": a proposal whose action installs release ``version`` of arcadia."""
    action = {"op": "codex_install", "args": {"source": "arcadia", "version": version}}
    return realm.ok("proposal_create", "alice", title=title, action=action)["proposal"]


def vote(realm, proposal, choice):
    for member in realm.members:
        if realm.proposal(proposal)["status"] != "voting":
            break
        realm.ok("proposal_vote", member, proposal=proposal, choice=choice)
    return realm.proposal(proposal)["status"]


def installed(net, realm, proposal):
    """The install ``proposal`` carried, run to its end; then the flags, judged again in the messages after it."""
    assert vote(realm, proposal, "yes") == "executed"
    job = settled(net, realm)
    assert (job["status"], job["error"]) == ("done", ""), job
    net.wait(1)


def installable(*arcadia_releases):
    """What the town could install from the source: the arcadia releases given, then the source's own layer, which
    it publishes in force from 2030 (``PUBLISH``)."""
    own = {"source": "x/source", "version": "1.0.0", "effective_from": PUBLISH["effective_from"]}
    return [{"source": "arcadia", "version": v, "effective_from": at} for v, at in arcadia_releases] + [own]


def layer_checksum(release):
    """§11.8's checksum of a layer, written out: what a source computes for an entry of its record."""
    layer = {"source": release["source"], "version": release["version"], "calendar": release["calendar"] or "",
             "chapters": {domain: sha for domain, sha in release["chapters"].items() if domain != "realm"}}
    text = json.dumps(layer, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def stale_since(at):
    return {"NonCompliant": {"since": at, "stale": ["arcadia"]}}


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def source(net):
    return source_with(net, "source", "1.0.0", "x/source")


@pytest.fixture(scope="module")
def town(net, source):
    """Follows ``source``, then installs its own layer: its guard holds from then on."""
    realm = net.realm("town", members=("alice", "bob"))
    link(realm, source)
    args = Site(net.web).change_list({"town/water": STATUTORY_WAIT.encode(), "town/calendar": CALENDAR.encode()},
                                     realm="town")
    realm.govern("codex_install", args)
    assert settled(net, realm)["status"] == "done"
    return realm


def test_an_interval_change_and_a_forced_check_work(net, source, town):
    assert status(town)["check"]["interval"] == 24
    town.ok("codex_check_config", "ctl", interval=1)
    assert status(town)["check"] == {"interval": 1, "due": town.now() + HOUR}
    refused = town.call("codex_check_config", {"interval": 169}, sender=net.people["ctl"])
    assert not refused["ok"] and "from 1 to 168" in refused["error"]

    found = check_now(net, town)  # the realm runs no arcadia: 1.0.0, in force since 6, is behind
    assert found["compliance"] == stale_since(6)
    assert found["installable"] == installable(("1.0.0", 6))
    assert status(town)["check"]["due"] >= found["checked_at"] + HOUR - 5  # the interval counts from it

    before = found["checked_at"]
    net.wait(HOUR + 5)  # the scheduled check runs on the new interval
    net.wait_until(lambda: checked(town)["checked_at"] > before, rounds=2)


def test_the_guard_and_the_forbid_of_the_followers_codex_do_not_apply_and_the_button_installs_the_release(
        net, source, town):
    own = town.call("proposal_create", {"title": "anything"}, sender=net.people["alice"])
    assert not own["ok"] and "15 working days" in own["error"]  # the town's own legislation, on its own proposals
    blocked = town.call("proposal_create", {"title": "blocked", "effective_at": town.now() + 60 * 86400},
                        sender=net.people["alice"])
    assert not blocked["ok"] and "the town's own rule" in blocked["error"]

    rejected = file_install(town, "1.0.0", title="blocked")
    assert vote(town, rejected, "no") == "rejected" and running(town) == set()  # the vote still decides

    installed(net, town, file_install(town, "1.0.0", title="blocked"))
    assert running(town) == {"1.0.0"} and status(town)["codex"]["layers"] == ["arcadia", "town"]
    assert {c: v["route"] for c, v in status(town)["codex"]["chapters"].items() if c.startswith("arcadia/")} == {
        "arcadia/tax": "capital", "arcadia/procedure": "capital"}
    assert checked(town)["compliance"] == "compliant"  # judged again right after the install


def test_past_its_effective_date_an_older_pin_is_non_compliant_installing_clears_it_and_a_revert_sets_it(
        net, source, town):
    first = active_id(town)
    due = town.now() + HOUR
    relay(source, "2.0.0", due)
    found = check_now(net, town)
    assert found["compliance"] == "compliant"  # published, not in force yet
    assert found["installable"] == installable(("2.0.0", due))

    net.wait(HOUR + 5)
    assert check_now(net, town)["compliance"] == stale_since(due)

    installed(net, town, file_install(town, "2.0.0"))
    assert running(town) == {"2.0.0"} and checked(town)["compliance"] == "compliant"

    town.govern("codex_revert", {"codex": first})
    net.wait(1)
    assert running(town) == {"1.0.0"} and checked(town)["compliance"] == stale_since(due)


def test_the_source_receives_the_report(net, source, town):
    installed(net, town, file_install(town, "2.0.0"))
    check_now(net, town)
    followers = source.ok("follower_list", "founder", query=True)["followers"]
    report = next(f for f in followers if f["id"] == town.cid)
    published = next(r for r in source.ok("legislation_releases", None, query=True)["releases"]
                     if r["source"] == "arcadia")
    assert published["version"] == "2.0.0" and published["calendar"] == ""  # a release with no calendar
    assert report["layers"] == [{"version": "2.0.0", "checksum": layer_checksum(published)}]  # found by its checksum
    assert report["compliance"] == "compliant"
    assert report["population"] == len(town.members) and report["reported_at"] > 0


def test_a_source_that_goes_back_is_followed_never_behind_and_its_older_release_is_a_step_back(net, source, town):
    """The town runs 2.0.0, whose publication time it kept with the layer; the source then relays 1.0.0 only, which was
    published earlier: the flag stays compliant, the note says the source went back, and "Propose install" of 1.0.0 is a
    step back (§11.6). Released together with 2.0.0 (a tie), 1.0.0 would be a note only."""
    releases = source.ok("legislation_releases", None, query=True)["releases"]
    current = next(r for r in releases if r["source"] == "arcadia")
    assert current["version"] == "2.0.0"
    chapters = status(town)["codex"]["chapters"]
    assert {c["published"] for k, c in chapters.items() if k.startswith("arcadia/")} == {current["published"]}
    assert all("published" not in c for k, c in chapters.items() if k.startswith("town/"))  # the town's own layer

    relay(source, "1.0.0", 6, published=current["published"] - 1)  # the source went back to an earlier release
    found = check_now(net, town)
    assert found["compliance"] == "compliant" and found["layer"] == "x/source"
    assert found["notes"] == [{"source": "arcadia", "kind": "went_back", "runs": "2.0.0", "lists": "1.0.0"}]
    assert found["installable"] == [{"source": "arcadia", "version": "1.0.0", "effective_from": 6, "step_back": True},
                                    installable()[-1]]

    relay(source, "1.0.0", 6, published=current["published"])  # published together: a tie, a note only
    found = check_now(net, town)
    assert found["compliance"] == "compliant" and found["installable"] == installable()
    assert found["notes"] == [{"source": "arcadia", "kind": "unlisted", "runs": "2.0.0", "lists": "1.0.0"}]

    relay(source, "2.0.0", current["effective_from"], published=current["published"])  # as it was, for the tests after
    found = check_now(net, town)
    assert found["compliance"] == "compliant" and found["notes"] == [] and found["installable"] == installable()


def test_an_unreadable_record_reads_unknown(net, source, town):
    source.stop()
    try:
        assert check_now(net, town)["compliance"] == "unknown"
    finally:
        source.start()
    assert check_now(net, town)["compliance"] == "compliant"


def test_what_the_checks_found_is_a_public_read_for_the_journal(net, source, town):
    found = town.ok("codex_releases_found", None, query=True)["releases"]
    assert [(r["source"], r["version"], r["found_in"]) for r in found] == [
        ("arcadia", "1.0.0", source.cid), ("x/source", "1.0.0", source.cid), ("arcadia", "2.0.0", source.cid)]
    assert all(r["found_at"] > 0 for r in found)
