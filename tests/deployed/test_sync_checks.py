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
    # published after 1.0.0 (5): releases are ordered by that, not by when they take effect
    relay(source, "2.0.0", due, published=6)
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


def test_a_release_filed_by_hand_that_is_older_than_what_the_town_runs_is_marked_a_step_back(net, source, town):
    """The town runs 2.0.0 (published at 6); 1.0.0 (published at 5) is a release a check found, which nothing offers:
    a member files its install by hand, and ``proposal_get`` says it takes the town back, a newer one that it is
    newer, and a release no check found that it cannot be placed (§11.6)."""
    assert running(town) == {"2.0.0"}
    assert checked(town)["installable"] == installable() and checked(town)["notes"] == []  # nothing offers 1.0.0
    back = town.ok("proposal_get", "alice", query=True, proposal=file_install(town, "1.0.0"))["release_change"]
    assert back == {"source": "arcadia", "version": "1.0.0", "runs": "2.0.0", "published": 5, "runs_published": 6,
                    "order": "older", "step_back": True}
    unknown = town.ok("proposal_get", "alice", query=True, proposal=file_install(town, "9.9.9"))["release_change"]
    assert (unknown["order"], unknown["step_back"], unknown["published"]) == ("unknown", False, None)
    same = town.ok("proposal_get", "alice", query=True, proposal=file_install(town, "2.0.0"))["release_change"]
    assert (same["order"], same["step_back"]) == ("same", False)


def test_an_install_that_names_a_release_and_a_change_list_is_refused_where_it_is_filed(net, source, town):
    """The realm installs the release and never reads the change list, so a vote would be told one install and the realm
    would run another: the real wasm refuses the shape when it is filed, and a plain release is filed as it was."""
    pin = {"url": "https://legislation.example.test/changes/mixed.json", "checksum": "sha256:" + "a" * 64}
    mixed = {"source": "arcadia", "version": "1.0.0", **pin}
    action = {"op": "codex_install", "args": mixed}
    before = len(town.ok("proposal_list", "alice", query=True, count=500)["proposals"])
    refused = town.call("proposal_create", {"title": "Both", "action": action}, sender=net.people["alice"])
    assert not refused["ok"]
    assert "a release (source and version) or a change list (url and checksum), not both" in refused["error"]
    direct = town.call("codex_install", mixed, sender=net.people["ctl"])
    assert not direct["ok"] and "not both" in direct["error"]
    assert len(town.ok("proposal_list", "alice", query=True, count=500)["proposals"]) == before  # nothing was filed
    assert file_install(town, "1.0.0")  # a plain release is filed


def test_an_install_with_ill_typed_arguments_is_refused_and_a_stored_one_fails_and_concludes_instead_of_trapping(
        net, source, town):
    """A manifest that is null made the install raise, so the call trapped: in a vote's execution the trap rolled the
    vote back and the proposal never concluded (review of #695). The real wasm refuses it where it is filed; one stored
    before that (set through the controller's shell) fails, with the reason recorded, when its vote closes."""
    reason = "manifest is the codex's manifest.json"
    malformed = {"source": "arcadia", "version": "1.0.0", "manifest": None}
    action = {"op": "codex_install", "args": malformed}
    # the town's own legislation makes a proposal wait 15 working days: this one takes effect after that, so it is the
    # realm's check, not the town's rule, that refuses it
    refused = town.call("proposal_create", {"title": "Null manifest", "action": action,
                                            "effective_at": town.now() + 60 * 86400}, sender=net.people["alice"])
    assert not refused["ok"] and reason in refused["error"]
    direct = town.call("codex_install", malformed, sender=net.people["ctl"])
    assert not direct["ok"] and reason in direct["error"]

    pid = file_install(town, "1.0.0")  # a plain release, then stored as it would have been before the refusal
    stored = town.shell("from ggg import Proposal\n"
                        f"Proposal[{pid!r}].action = {action!r}\n"
                        "print('stored')")
    assert "Traceback" not in stored
    assert vote(town, pid, "yes") == "failed"  # the last vote's message completes: it did not trap
    row = town.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
    assert row["status"] == "failed" and reason in row["result"]["error"]


def store_action(town, pid, args):
    """Set the action of ``pid`` as a proposal stored before a filing refused it would have it (the controller's
    shell)."""
    action = {"op": "codex_install", "args": args}
    out = town.shell(f"from ggg import Proposal\nProposal[{pid!r}].action = {action!r}\nprint('stored')")
    assert "Traceback" not in out, out


LEX = {"id": "lex", "version": "1.0"}


@pytest.mark.parametrize("args", ["checksum", ["checksum"], {"manifest": {**LEX, "hooks": None}, "source": "pass"},
                                  {"manifest": {**LEX, "capabilities": [{}]}, "source": "pass"},
                                  {"manifest": LEX, "source": "def ("}],
                         ids=["args text", "args list", "hooks null", "capabilities with a record",
                              "code that does not compile"])
def test_a_stored_install_the_check_refuses_concludes_failed_on_the_wasm_where_it_used_to_reject_the_last_vote(
        net, source, town, args):
    """Args that are not a record, hooks that are null, capabilities that cannot be hashed, code that does not compile:
    each raised in the message of the last vote, which the replica rolled back, and the proposal stayed ``voting`` for
    ever (review of #697). The wasm now concludes it ``failed``, with the reason."""
    pid = file_install(town, "1.0.0")
    store_action(town, pid, args)
    assert vote(town, pid, "yes") == "failed"  # the last vote's message completes
    row = town.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
    assert row["status"] == "failed" and row["result"]["ok"] is False and row["result"]["error"]


DEEP = {"binary operators": "1" + "+1" * 5000, "unary operators": "-" * 5000 + "1"}


@pytest.mark.parametrize("approval", ["off", "on", "pinned"])
@pytest.mark.parametrize("name", list(DEEP))
def test_code_nested_too_deep_to_compile_is_a_refusal_and_a_stored_one_concludes_failed_whatever_the_trust_policy(
        net, source, town, name, approval):
    """The compiler raises a RecursionError or a MemoryError on it, which the wasm can catch and which trapped the
    install: a stored package with it left the proposal ``voting`` for ever, the deciding vote and the next both
    rejected, with the approval a realm requires by default (review of #697). Code the realm refuses as untrusted is
    refused for that, without being compiled; code it takes is refused as one that does not compile."""
    lex = {"id": "lex", "version": "1.0", "codex_api_version": 2}
    args = {"manifest": lex, "source": DEEP[name]}
    pin = "[]" if approval != "pinned" else "['sha256:' + checksum(%r, %r)]" % (lex, DEEP[name])
    out = town.shell("from ggg import Realm\nfrom api.extensions import checksum\nr = Realm['1']\n"
                     f"r.require_marketplace_approval = {approval != 'off'}\n"
                     f"r.trusted_approvers = {pin}\nprint('policy')")
    assert "Traceback" not in out, out
    try:
        reason = "neither allowlisted nor approved" if approval == "on" else "source does not compile"
        direct = town.call("codex_install", args, sender=net.people["ctl"])
        assert not direct["ok"] and reason in direct["error"]
        if approval != "on":  # filed all the same when it asks the trust policy at the vote
            # past the town's own 15-working-day wait: it is the realm's check that refuses it, not the town's rule
            filing = {"title": "Install", "action": {"op": "codex_install", "args": args},
                      "effective_at": town.now() + 60 * 86400}
            refused = town.call("proposal_create", filing, sender=net.people["alice"])
            assert not refused["ok"] and reason in refused["error"]
        pid = file_install(town, "1.0.0")
        store_action(town, pid, args)
        assert vote(town, pid, "yes") == "failed"  # the last vote's message completes: it did not trap
        row = town.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
        assert row["status"] == "failed" and reason in row["result"]["error"]
    finally:
        restore = town.shell("from ggg import Realm\nr = Realm['1']\nr.require_marketplace_approval = False\n"
                             "r.trusted_approvers = []\nprint('restored')")
        assert "Traceback" not in restore


LONE = "U+D800, a lone surrogate, cannot be encoded"
SURROGATES = {"in the code": ({"manifest": {"id": "lex", "version": "1.0", "codex_api_version": 2},
                              "source": "x = 1\ny = '\ud800'\n"},
                              f"the code is not UTF-8 text: {LONE} at line 2, column 6"),
              "in a manifest value": ({"manifest": {"id": "lex\ud800", "version": "1.0", "codex_api_version": 2},
                                       "source": "pass"},
                                      f"the manifest is not UTF-8 text: {LONE}")}


@pytest.fixture(scope="module")
def plain(net):
    """A realm of its own with no codex: its filings meet no guard of a layered codex, whose sandbox call cannot take a
    lone surrogate either (a separate bug: it traps before the install's check is reached)."""
    return net.realm("plain")


@pytest.mark.parametrize("name", list(SURROGATES))
def test_text_that_is_not_utf8_is_refused_where_the_install_is_called_and_filed_with_approval_on(net, plain, name):
    """The canister's JSON accepts a lone surrogate, and hashing the text raised a UnicodeEncodeError that dispatch
    trapped on: the direct call and the filing were rejected (review of #697). In a manifest value it shows only here:
    the canister's json.dumps ignores ensure_ascii. A stored proposal with one is not a case here: the canister's
    database cannot encode it, so the shell cannot write one (the in-process test runs the stored vote and timer)."""
    args, reason = SURROGATES[name]
    set_policy = ("from ggg import Realm\nr = Realm['1']\nr.require_marketplace_approval = {}\n"
                  "r.trusted_approvers = []\nprint('policy')")
    assert "Traceback" not in plain.shell(set_policy.format(True))
    direct = plain.call("codex_install", args, sender=net.people["ctl"])
    assert not direct["ok"] and reason in direct["error"]
    action = {"op": "codex_install", "args": args}
    refused = plain.call("proposal_create", {"title": "Install", "action": action}, sender=net.people["alice"])
    assert not refused["ok"] and reason in refused["error"]
    assert plain.ok("proposal_list", "alice", query=True)["proposals"] == []  # nothing was filed


ECHOES = {"a control character": '["\\x7f" * 14000]', "a list of numbers": "[1] * 27000"}


@pytest.mark.parametrize("name", list(ECHOES))
def test_a_stored_install_whose_refusal_echoes_a_large_value_concludes_failed_with_a_cut_reason(net, plain, name):
    """``codex.runnable`` names the codex API a manifest asks for, so a stored manifest whose ``codex_api_version`` is
    long gave a refusal larger than ``Proposal.result`` holds while the action itself fits: the write raised, and the
    deciding vote was rolled back in every message that carried it, so the proposal stayed ``voting`` for ever (review
    of #697). The reason is recorded cut, with a marker, and the proposal concludes."""
    set_policy = ("from ggg import Realm\nr = Realm['1']\nr.require_marketplace_approval = True\n"
                  "r.trusted_approvers = []\nprint('policy')")
    assert "Traceback" not in plain.shell(set_policy)
    filed = {"manifest": {**LEX, "codex_api_version": [1]}, "source": "pass"}
    pid = plain.ok("proposal_create", "alice", title="Install",
                   action={"op": "codex_install", "args": filed})["proposal"]
    stored = plain.shell(
        "import json\nfrom ggg import Proposal\n"
        f"manifest = {{'id': 'lex', 'version': '1.0', 'codex_api_version': {ECHOES[name]}}}\n"
        "args = {'manifest': manifest, 'source': 'pass'}\n"
        f"Proposal[{pid!r}].action = {{'op': 'codex_install', 'args': args}}\n"
        f"print('stored', len(json.dumps(Proposal[{pid!r}].action)))")
    assert "Traceback" not in stored, stored
    assert int(stored.split()[-1]) < 65536  # the action fits the field, as a filing must
    assert vote(plain, pid, "yes") == "failed"  # the last vote's message completes: it did not trap
    row = plain.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
    result = row["result"]
    assert row["status"] == "failed" and result["ok"] is False and result["kind"] == "invalid_argument"
    assert result["error"].startswith("this realm runs codex API 2, not ") and "[cut: the reply was" in result["error"]
    assert len(json.dumps(result)) <= 65536


def test_a_stored_release_with_an_ill_typed_change_list_beside_it_runs_as_the_release_at_its_time_on_the_wasm(
        net, source, town):
    """The release is staged when the vote decides it, and switched to at the time: the same check at both, so the job
    never outlives a refusal. The source relays 1.0.0 again for it (the town runs 2.0.0, which it relays now)."""
    releases = source.ok("legislation_releases", None, query=True)["releases"]
    now_relayed = next(r for r in releases if r["source"] == "arcadia")  # 2.0.0, to restore for the tests that follow
    relay(source, "1.0.0", 6, published=now_relayed["published"] - 1)
    action = {"op": "codex_install", "args": {"source": "arcadia", "version": "1.0.0"}}
    pid = town.ok("proposal_create", "alice", title="Stored", action=action,
                  effective_at=town.now() + HOUR)["proposal"]
    store_action(town, pid, {"source": "arcadia", "version": "1.0.0", "url": 5})
    assert vote(town, pid, "yes") == "execution_scheduled"
    job = settled(net, town)
    assert (job["status"], job["step"]) == ("running", "wait") and running(town) == {"2.0.0"}  # staged, waiting
    net.wait(HOUR + 5)
    row = town.ok("proposal_get", "alice", query=True, proposal=pid)["proposal"]
    assert row["status"] == "executed" and running(town) == {"1.0.0"}  # it ran as the release
    assert status(town)["job"]["status"] == "done"  # and left no job running
    relay(source, "2.0.0", now_relayed["effective_from"], published=now_relayed["published"])  # as it was
    # the next install is not refused, and the tests that follow run on 2.0.0 again
    installed(net, town, file_install(town, "2.0.0"))
    assert running(town) == {"2.0.0"}


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
