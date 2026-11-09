"""Links between realms in process (realms-gos#500; GGG's I-NEST-1 to 3): the sync sources a realm takes legislation
from, its followers, and the published record. The other realms are scripted peers (fake_chain.peer) that answer by
method name. tests/deployed/test_sync_sources.py runs the same flows between real realms on one PocketIC instance."""

import fake_chain
import pytest
import world
from api import links
from core import legislation, op
from core.db import Fail
from fake_chain import peer, rejects
from ggg import Follower, Quarter, Realm, Release, SyncSource
from kit import call, denied, direct, fire_timers, later, ok
from test_legislation_runtime import LOG, activate, layered

ME, SRC, FOL, OTHER = fake_chain.SELF, "src", "fol", "other"
CHECKSUM = "sha256:" + "a" * 64
OTHER_CHECKSUM = "sha256:" + "b" * 64
UNANSWERED = {"ok": False, "kind": "forbidden", "error": "the other realm has not voted"}  # an answer, not a failure


def realm_info(*sources, ended=()):
    """What a realm answers to ``realm_info``: the realms it takes legislation from, and those it no longer does."""
    listed = [{"source": {"Realm": s}, "name": s, "status": "active"} for s in sources]
    gone = [{"source": {"Realm": s}, "name": s, "status": {"Ended": {"by": s, "at": 1}}} for s in ended]
    return {"ok": True, "sync_sources": listed + gone}


@pytest.fixture(autouse=True)
def realm():
    world.build()
    LOG.clear()
    legislation._state.update(codex=legislation.UNKNOWN, box=None, failing=False)
    yield
    legislation.load(None)


def status(link):
    return links.link_status(link)


# ---- I-NEST-1: a link is active only after both votes


def test_the_follower_votes_first_and_the_source_completes_the_link():
    source = peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="The source")
    assert status(SyncSource[SRC]) == "requested"
    assert source.sent("link_notify") == [{"side": "follower", "status": "requested"}]
    assert ok("link_notify", SRC, side="sync_source", status="requested")["status"] == "active"
    assert status(SyncSource[SRC]) == "active"


def test_the_source_votes_first_and_the_followers_vote_completes_the_link():
    follower = peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    assert status(Follower[FOL]) == "requested"
    assert follower.sent("link_notify") == [{"side": "sync_source", "status": "requested"}]
    assert ok("link_notify", FOL, side="follower", status="requested")["status"] == "active"
    assert status(Follower[FOL]) == "active"

    # the other order, from the follower's side: the source's vote is already in, so its answer says active
    peer(SRC, realm_info=realm_info(), link_notify={"ok": True, "status": "active"})
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="The source")
    assert status(SyncSource[SRC]) == "active"


def test_a_vote_for_a_link_that_stands_is_a_conflict_and_a_name_is_needed():
    peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="The source")
    assert "already a sync source" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Again")
    assert "name is 1 to" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="")
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    assert "already a follower" in denied("follower_accept", "ctl", follower=FOL)
    assert "itself" in denied("sync_source_add", "ctl", source={"Realm": ME}, name="Me")
    assert "its own follower" in denied("follower_accept", "ctl", follower=ME)


def test_a_stand_in_record_is_active_on_the_followers_vote_and_pinned_again_by_a_second_add():
    url = "https://example.org/releases.json"
    bad = {"Record": {"url": "http://example.org/releases.json", "checksum": CHECKSUM}}
    assert "url is https" in denied("sync_source_add", "ctl", source=bad, name="Record")
    assert "checksum" in denied("sync_source_add", "ctl", source={"Record": {"url": url, "checksum": "abc"}}, name="R")
    ok("sync_source_add", "ctl", source={"Record": {"url": url, "checksum": CHECKSUM}}, name="The EU")
    assert (status(SyncSource[url]), SyncSource[url].checksum) == ("active", CHECKSUM)
    ok("sync_source_add", "ctl", source={"Record": {"url": url, "checksum": OTHER_CHECKSUM}}, name="The EU")
    assert (status(SyncSource[url]), SyncSource[url].checksum) == ("active", OTHER_CHECKSUM)
    assert fake_chain.state["calls"] == []  # nobody to tell

    # removed by its url, whatever checksum is named; a new add opens a new link
    ok("sync_source_remove", "ctl", source={"Record": {"url": url, "checksum": CHECKSUM}})
    assert status(SyncSource[url])["Ended"]["by"] == ME
    ok("sync_source_add", "ctl", source={"Record": {"url": url, "checksum": CHECKSUM}}, name="The EU")
    assert status(SyncSource[url]) == "active"


# ---- I-NEST-2: no cycles


def test_a_cycle_is_refused_directly_and_through_other_realms_and_leaves_no_trace():
    peer(SRC, realm_info=realm_info(ME))
    assert "a cycle" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert SyncSource[SRC] is None

    # through another realm: src takes it from a hop, which takes it from this realm
    peer(SRC, realm_info=realm_info("hop"))
    peer("hop", realm_info=realm_info(ME))
    assert "a cycle" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert SyncSource[SRC] is None
    assert [(cid, name) for cid, name, _ in fake_chain.state["calls"]] == [
        (SRC, "realm_info"), (SRC, "realm_info"), ("hop", "realm_info")]

    # a link that has ended is no source of legislation
    peer("hop", realm_info=realm_info(ended=[ME]))
    peer(SRC, realm_info=realm_info("hop"), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")


def test_a_follower_this_realm_takes_legislation_from_is_a_cycle():
    peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert "a cycle" in denied("follower_accept", "ctl", follower=SRC)
    assert Follower[SRC] is None
    peer("far", realm_info=realm_info())
    peer(SRC, realm_info=realm_info("far"), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": "far"}, name="Far")  # a second source: src is not the only one
    assert "a cycle" in denied("follower_accept", "ctl", follower="far")


def test_a_realm_that_cannot_be_read_fails_the_check_and_an_ended_link_comes_back_as_it_was():
    peer(SRC)  # no realm_info
    assert "could not be read" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert SyncSource[SRC] is None
    peer(SRC, realm_info={"ok": True})  # an answer that is no realm's
    assert "could not be read" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert SyncSource[SRC] is None
    peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    ok("sync_source_remove", "ctl", source={"Realm": SRC})
    ended = status(SyncSource[SRC])
    peer(SRC, realm_info=realm_info(ME))
    assert "a cycle" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Again")
    assert status(SyncSource[SRC]) == ended and SyncSource[SRC].name == "Src"


def test_an_answer_that_is_no_realms_fails_the_check_cleanly():
    for sources in ([{"source": {"Realm": {"id": SRC}}, "name": "x", "status": "active"}], [{"source": 7}], "none"):
        peer(SRC, realm_info={"ok": True, "sync_sources": sources})
        assert "could not be read" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
        assert SyncSource[SRC] is None
    peer(SRC, realm_info=realm_info(), link_notify=["not", "an", "answer"])  # a notice answered with no reply at all
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert status(SyncSource[SRC]) == "requested" and not SyncSource[SRC].unsent and not SyncSource[SRC].checking


def test_a_follower_this_realm_has_accepted_takes_legislation_from_it_before_its_own_vote_shows():
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    peer(FOL, realm_info=realm_info())  # its realm_info does not list this realm yet
    assert "a cycle" in denied("sync_source_add", "ctl", source={"Realm": FOL}, name="Fol")
    peer(SRC, realm_info=realm_info(FOL))  # nor through another realm
    assert "a cycle" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert SyncSource[FOL] is None and SyncSource[SRC] is None


def test_a_walk_reads_at_most_a_bounded_number_of_realms():
    for i in range(links.WALK_LIMIT + 1):
        peer(f"r{i}", realm_info=realm_info(f"r{i + 1}"))
    assert "too many realms" in denied("sync_source_add", "ctl", source={"Realm": "r0"}, name="Long")
    assert SyncSource["r0"] is None


# ---- I-NEST-3: calls between the capitals of a link and nowhere else


def test_link_notify_and_follower_report_are_accepted_only_from_the_other_sides_capital():
    peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    notice = {"side": "follower", "status": "requested"}
    report = {"layers": [], "compliance": "unknown", "population": 0}
    for stranger in ("mallory", "alice", OTHER):
        assert "needs link.sync" in denied("link_notify", stranger, **notice)
        assert "needs link.sync" in denied("follower_report", stranger, **report)
    # the controller holds every permission, and is still no capital of a link
    assert "not the capital" in denied("link_notify", "ctl", **notice)
    assert "not a follower" in denied("follower_report", "ctl", **report)
    # src is this realm's sync source, not its follower: it cannot speak as one
    assert "not the capital" in denied("link_notify", SRC, **notice)
    assert "not a follower" in denied("follower_report", SRC, **report)
    assert call("link_notify", fake_chain.ANONYMOUS, **notice)["kind"] == "unauthenticated"
    assert status(Follower[FOL]) == "requested"


def test_a_notice_that_a_link_ended_names_its_sender_and_a_time_not_ahead_of_the_clock():
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    ok("link_notify", FOL, side="follower", status="requested")
    now = fake_chain.now()
    assert "names its sender" in denied("link_notify", FOL, side="follower", status={"Ended": {"by": ME, "at": now}})
    ahead = {"Ended": {"by": FOL, "at": now + links.CLOCK_TOLERANCE + 1}}
    assert "ahead" in denied("link_notify", FOL, side="follower", status=ahead)
    assert status(Follower[FOL]) == "active"
    ok("link_notify", FOL, side="follower", status={"Ended": {"by": FOL, "at": now + links.CLOCK_TOLERANCE}})
    assert status(Follower[FOL]) == {"Ended": {"by": FOL, "at": now + links.CLOCK_TOLERANCE}}
    # the same news again is no error; any other news about an ended link is
    ok("link_notify", FOL, side="follower", status={"Ended": {"by": FOL, "at": now}})
    assert "has ended" in denied("link_notify", FOL, side="follower", status="requested")
    assert status(Follower[FOL])["Ended"]["at"] == now + links.CLOCK_TOLERANCE


def test_a_quarter_makes_no_call_to_another_realm(monkeypatch):
    r = Realm["1"]
    r.is_quarter, r.is_capital, r.federation_realm_id = True, False, "capital"
    Quarter(id="capital", name="Capital", index=0, status="active")
    for name, args in (("sync_source_add", {"source": {"Realm": SRC}, "name": "Src"}),
                       ("sync_source_remove", {"source": {"Realm": SRC}}),
                       ("follower_accept", {"follower": FOL}), ("follower_remove", {"follower": FOL})):
        assert "only the capital" in denied(name, "ctl", **args)
    assert fake_chain.state["calls"] == []


# ---- Notices that nobody answers, and leaving


def test_a_notice_nobody_answered_is_sent_again_until_it_is():
    source = peer(SRC, realm_info=realm_info(), link_notify=rejects("the canister is stopped"))
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert SyncSource[SRC].unsent and status(SyncSource[SRC]) == "requested"
    fire_timers()
    assert SyncSource[SRC].unsent and len(source.sent("link_notify")) == 2  # still stopped: the timer is set again
    source.answer("link_notify", {"ok": True, "status": "active"})
    fire_timers()
    assert not SyncSource[SRC].unsent and status(SyncSource[SRC]) == "active"
    assert fake_chain.state["timers"] == []


def test_one_chain_of_timers_serves_a_link_and_ends_when_the_other_side_ends_it():
    source = peer(SRC, realm_info=realm_info(), link_notify=rejects("the canister is stopped"))
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    ok("sync_source_remove", "ctl", source={"Realm": SRC})  # a second notice to the same stopped realm
    assert len(source.sent("link_notify")) == 2 and len(fake_chain.state["timers"]) == 1
    fire_timers()  # sends the status as it is now, and sets the next timer
    assert len(source.sent("link_notify")) == 3 and len(fake_chain.state["timers"]) == 1
    assert source.sent("link_notify")[-1]["status"]["Ended"]["by"] == ME

    follower = peer(FOL, link_notify=rejects("the canister is stopped"))
    ok("follower_accept", "ctl", follower=FOL)
    assert Follower[FOL].unsent
    ok("link_notify", FOL, side="follower", status={"Ended": {"by": FOL, "at": fake_chain.now()}})
    assert not Follower[FOL].unsent  # their notice settles what this realm had to tell
    fake_chain.state["timers"].clear()
    sent = len(follower.sent("link_notify"))
    op.run(links.resend(Follower, FOL))  # the timer that was set when the notice failed
    assert len(follower.sent("link_notify")) == sent and fake_chain.state["timers"] == []


def test_a_notice_waiting_when_the_realm_is_upgraded_is_sent_again_at_its_start():
    peer(FOL, link_notify=rejects("the canister is stopped"))
    ok("follower_accept", "ctl", follower=FOL)
    fake_chain.state["timers"].clear()  # the upgrade drops the timers
    peer(FOL, link_notify={"ok": True, "status": "active"})
    links.resume()
    fire_timers()
    assert not Follower[FOL].unsent and status(Follower[FOL]) == "active"


def test_a_notice_during_our_cycle_walk_is_refused_and_our_own_notice_after_it_completes_the_link():
    seen = []

    def src_info(_):  # the source votes while the walk is out
        seen.append(direct("link_notify", SRC, side="sync_source", status="requested"))
        return realm_info()

    source = peer(SRC, realm_info=src_info, link_notify={"ok": True, "status": "active"})
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert [(n["ok"], n["kind"]) for n in seen] == [(False, "forbidden")]  # not voted yet, as far as the source can tell
    assert source.sent("link_notify") == [{"side": "follower", "status": "requested"}]
    assert status(SyncSource[SRC]) == "active" and not SyncSource[SRC].checking  # the answer to our notice completed it


def test_the_same_for_a_follower_this_realm_accepts_while_its_walk_is_out():
    peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    seen = []

    def src_info(_):  # the follower votes while the walk, which reads this realm's sources, is out
        seen.append(direct("link_notify", FOL, side="follower", status="requested"))
        return realm_info()

    peer(SRC, realm_info=src_info)
    peer(FOL, link_notify={"ok": True, "status": "active"})
    ok("follower_accept", "ctl", follower=FOL)
    assert [(n["ok"], n["kind"]) for n in seen] == [(False, "forbidden")]
    assert status(Follower[FOL]) == "active" and not Follower[FOL].checking


def test_a_walk_that_fails_after_the_other_sides_notice_leaves_no_link_on_either_side():
    seen = []

    def cyclic(_):
        seen.append(direct("link_notify", SRC, side="sync_source", status="requested"))
        return realm_info(ME)

    source = peer(SRC, realm_info=cyclic, link_notify=UNANSWERED)
    assert "a cycle" in denied("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    assert [n["kind"] for n in seen] == ["forbidden"] and SyncSource[SRC] is None
    assert source.sent("link_notify") == []  # and the source was told nothing
    assert "not the capital" in denied("link_notify", "ctl", side="sync_source", status="requested")


def test_either_side_ends_a_link_and_tells_the_other_and_a_new_vote_opens_a_new_link():
    source = peer(SRC, realm_info=realm_info(), link_notify={"ok": True, "status": "active"})
    follower = peer(FOL, link_notify={"ok": True, "status": "active"})
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    ok("follower_accept", "ctl", follower=FOL)
    later(10)
    ok("sync_source_remove", "ctl", source={"Realm": SRC})
    ok("follower_remove", "ctl", follower=FOL)
    ended = {"Ended": {"by": ME, "at": fake_chain.now()}}
    assert source.sent("link_notify")[-1] == {"side": "follower", "status": ended}
    assert follower.sent("link_notify")[-1] == {"side": "sync_source", "status": ended}
    assert status(SyncSource[SRC]) == status(Follower[FOL]) == ended
    assert "has ended" in denied("sync_source_remove", "ctl", source={"Realm": SRC})
    assert "has ended" in denied("follower_remove", "ctl", follower=FOL)
    assert "unknown" in denied("follower_remove", "ctl", follower=OTHER)

    # an ended link takes no report, and a new pair of votes opens a new one
    assert "not active" in denied("follower_report", FOL, layers=[], compliance="unknown", population=0)
    ok("follower_accept", "ctl", follower=FOL)
    assert status(Follower[FOL]) == "active" and Follower[FOL].layers == []


# ---- Reports


def test_a_followers_report_is_its_own_claim_over_an_active_link_and_is_kept_with_its_time():
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    report = {"layers": [{"version": "1.0.0", "checksum": CHECKSUM}], "compliance": "compliant", "population": 800_000}
    assert "not active" in denied("follower_report", FOL, **report)
    ok("link_notify", FOL, side="follower", status="requested")
    later(5)
    ok("follower_report", FOL, **report)
    listed = ok("follower_list", "ctl")["followers"]
    assert [(f["id"], f["layers"], f["population"], f["reported_at"]) for f in listed] == [
        (FOL, report["layers"], 800_000, fake_chain.now())]
    behind = {"NonCompliant": {"since": 7, "stale": ["eu/es"]}}
    unlisted = [{"version": "9", "checksum": OTHER_CHECKSUM}]
    ok("follower_report", FOL, **{**report, "compliance": behind, "layers": unlisted})
    assert Follower[FOL].layers == unlisted  # a layer the record lacks is kept, as reported
    for bad in ({"layers": [{"version": "1", "checksum": "x"}]}, {"compliance": "late"},
                {"compliance": {"NonCompliant": {}}},
                {"layers": [{"version": "1", "checksum": CHECKSUM}] * (links.MOST_LAYERS + 1)}):
        assert not direct("follower_report", FOL, **{**report, **bad})["ok"]  # malformed: no Candid call can say so


def test_follower_list_pages_the_followers():
    for i in range(3):
        peer(f"f{i}", link_notify=UNANSWERED)
        ok("follower_accept", "ctl", follower=f"f{i}")
    first = ok("follower_list", "ctl", count=2)
    assert [f["id"] for f in first["followers"]] == ["f0", "f1"] and first["next"] == 3
    assert [f["id"] for f in ok("follower_list", "ctl", count=2, **{"from": first["next"]})["followers"]] == ["f2"]


# ---- The follower_compliance fact


FORBID_NONCOMPLIANT = """
from realms.legislation import F, above, forbid

class Treasury(above("treasury")):
    compliant_only = forbid("transfer_create", F.follower_compliance == "NonCompliant",
        legislation="a source pays no follower that is behind")
"""
FORBID_BEHIND_REPORT = """
from realms.legislation import F, above, forbid

class Federation(above("federation")):
    no_late_quarter = forbid("quarter_report", F.follower_compliance != "Compliant",
        legislation="a quarter reports only when it is compliant")
"""


def test_a_forbid_of_the_source_reads_what_the_follower_last_reported():
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    ok("link_notify", FOL, side="follower", status="requested")
    activate(layered({("x/town", "treasury"): FORBID_NONCOMPLIANT}))
    pay = {"fund": "ROOT", "to": f"Follower:{FOL}", "amount": 1}
    refused = "forbidden by the legislation: its arguments: refused by x/town/treasury/compliant_only"
    # unknown: nothing reported yet, and not a follower the forbid names
    assert refused not in denied("transfer_create", "ctl", **pay)
    ok("follower_report", FOL, layers=[], compliance={"NonCompliant": {"since": 1, "stale": ["eu"]}}, population=1)
    assert refused in denied("transfer_create", "ctl", **pay)
    assert refused not in denied("transfer_create", "ctl", **{**pay, "to": "Follower:other"})  # not a follower
    assert refused not in denied("transfer_create", "ctl", **{**pay, "to": FOL})  # not named as one
    ok("follower_report", FOL, layers=[], compliance="compliant", population=1)
    assert refused not in denied("transfer_create", "ctl", **pay)
    ok("follower_report", FOL, layers=[], compliance={"NonCompliant": {"since": 1, "stale": ["eu"]}}, population=1)
    assert refused in denied("transfer_create", "ctl", **pay)
    ok("follower_remove", "ctl", follower=FOL)  # the report of a link that has ended counts for nothing
    assert refused not in denied("transfer_create", "ctl", **pay)


def test_a_quarters_report_carries_the_compliance_of_the_quarter_for_a_forbid():
    Quarter(id="north", name="North", index=1, status="active")
    activate(layered({("x/town", "federation"): FORBID_BEHIND_REPORT}))
    assert "refused by x/town/federation/no_late_quarter" in denied("quarter_report", "north", population=3)
    Quarter["north"].compliance = '"compliant"'
    ok("quarter_report", "north", population=3)


# ---- Hooks, not guards


HOOK_LEAVING = """
from realms.legislation import above

class Realm(above("realm")):
    def on_link_notify(self, event):
        self.ggg.t_log(text="notice " + event.args["side"])
"""
GUARD_LEAVING = """
from realms.legislation import above

class Realm(above("realm")):
    def check_link_notify(self, args):
        return self.deny("a follower may not leave")
"""
FORBID_LEAVING = """
from realms.legislation import F, above, forbid

class Realm(above("realm")):
    nobody_leaves = forbid("link_notify", F.side == "Follower", legislation="no")
"""


def test_a_sources_legislation_hooks_a_notice_and_never_guards_or_forbids_it():
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    activate(layered({("x/town", "realm"): HOOK_LEAVING}))
    ok("link_notify", FOL, side="follower", status={"Ended": {"by": FOL, "at": fake_chain.now()}})
    assert LOG == [("notice follower", "codex")]
    for text in (GUARD_LEAVING, FORBID_LEAVING):
        with pytest.raises(Fail, match="link_notify can be hooked only"):
            activate(layered({("x/town", "realm"): text}))


def test_a_notice_that_a_link_ended_is_accepted_while_the_codex_is_not_loaded_and_other_guards_fail_closed():
    peer(FOL, link_notify=UNANSWERED)
    ok("follower_accept", "ctl", follower=FOL)
    activate(layered({("x/town", "realm"): HOOK_LEAVING}))
    legislation._drop()  # the sandbox is gone, as after an upgrade whose codex does not load
    assert "legislation error" in denied("proposal_create", "alice", title="a motion")
    ok("link_notify", FOL, side="follower", status={"Ended": {"by": FOL, "at": fake_chain.now()}})
    assert status(Follower[FOL])["Ended"]["by"] == FOL  # nothing refused it, and the hook that could not run did not
    assert LOG == []


def test_a_sync_source_removal_is_reserved_so_no_legislation_runs_for_it():
    assert "sync_source_remove" in legislation.RESERVED and "link_notify" not in legislation.RESERVED
    assert legislation.is_reserved("sync_source_remove", {"source": {"Realm": SRC}})
    assert not legislation.is_reserved("link_notify", {})


# ---- The published record


def stored(text):
    from core import layered_codex as store

    return store.store_chapter(store.checksum(text), text).sha256


def release(version="1.0.0", source="x/town", **more):
    return {"source": source, "version": version, "effective_from": 1_900_000_000, "commit": "c" * 40,
            "chapters": [{"domain": "tax", "checksum": stored("tax " + version)}], **more}


def test_a_release_names_chapters_the_realm_stores_and_is_never_edited():
    sha = stored("tax 1.0.0")
    bad = release(chapters=[{"domain": "tax", "checksum": OTHER_CHECKSUM}])
    assert "not stored by this realm" in denied("legislation_release_publish", "ctl", **bad)
    no_calendar = release(calendar=OTHER_CHECKSUM)
    assert "not stored by this realm" in denied("legislation_release_publish", "ctl", **no_calendar)
    assert "names a domain once" in denied("legislation_release_publish", "ctl", **release(
        chapters=[{"domain": "tax", "checksum": sha}] * 2))
    assert ok("legislation_releases", fake_chain.ANONYMOUS)["releases"] == []

    later(60)
    ok("legislation_release_publish", "ctl", **release(notes={"es": "Primera"}))
    published = ok("legislation_releases", fake_chain.ANONYMOUS)["releases"]
    assert published == [{"source": "x/town", "version": "1.0.0", "published": fake_chain.now(),
                          "effective_from": 1_900_000_000, "commit": "c" * 40, "chapters": {"tax": sha},
                          "calendar": "", "notes": {"es": "Primera"}}]
    assert "published already" in denied("legislation_release_publish", "ctl", **release(commit="d" * 40))
    assert "publishes as x/town" in denied("legislation_release_publish", "ctl", **release("1.1.0", source="other"))
    ok("legislation_release_publish", "ctl", **release("1.1.0"))
    assert [r["version"] for r in ok("legislation_releases", fake_chain.ANONYMOUS)["releases"]] == ["1.0.0", "1.1.0"]
    assert ok("legislation_chapter", fake_chain.ANONYMOUS, chapter=sha)["text"] == "tax 1.0.0"
    assert "unknown" in denied("legislation_chapter", fake_chain.ANONYMOUS, chapter=OTHER_CHECKSUM)


def test_a_publication_is_governed_by_the_root_and_needs_its_permission():
    assert "needs legislation.publish" in denied("legislation_release_publish", "alice", **release())
    world.policy(world.Department["Administration"], 2, 3)
    assert ok("legislation_release_publish", "ctl", **release())["requires_vote"]
    assert Release["x/town@1.0.0"] is None


def relayed(source, version, sha):
    return {"source": source, "version": version, "published": 5, "effective_from": 6, "commit": "e" * 40,
            "chapters": {"tax": sha}, "calendar": None, "notes": {}}


def test_the_record_lists_the_relayed_layers_top_to_bottom_then_the_realms_own():
    eu, spain = stored("eu tax"), stored("es tax")
    ok("legislation_release_publish", "ctl", **release())
    links.relay_release(relayed("eu/es", "2.4.0", spain), ["eu", "eu/es"])
    links.relay_release(relayed("eu", "1.0.0", eu), ["eu", "eu/es"])  # the layer above arrives last: the record still runs top down

    def versions():
        return [(r["source"], r["version"]) for r in ok("legislation_releases", fake_chain.ANONYMOUS)["releases"]]

    assert versions() == [("eu", "1.0.0"), ("eu/es", "2.4.0"), ("x/town", "1.0.0")]
    links.relay_release(relayed("eu/es", "2.5.0", spain), ["eu", "eu/es"])  # a newer release takes its layer's place
    assert versions() == [("eu", "1.0.0"), ("eu/es", "2.5.0"), ("x/town", "1.0.0")]
    with pytest.raises(Fail, match="does not list it"):
        links.relay_release(relayed("eu/ca", "1.0.0", eu), ["eu", "eu/es"])
    # a relayed layer is no id this realm publishes as
    assert "relays" in denied("legislation_release_publish", "ctl", **release("3.0.0", source="eu/es"))
    # paged, relayed first
    page = ok("legislation_releases", fake_chain.ANONYMOUS, count=2)
    assert [r["source"] for r in page["releases"]] == ["eu", "eu/es"] and page["next"] == 3
    last = ok("legislation_releases", fake_chain.ANONYMOUS, count=2, **{"from": 3})
    assert [r["source"] for r in last["releases"]] == ["x/town"] and last["next"] is None


def test_a_layer_from_above_is_never_the_source_id_the_realm_publishes_under():
    ok("legislation_release_publish", "ctl", **release())
    with pytest.raises(Fail, match="x/town is the source id this realm publishes under"):
        links.relay_release(relayed("x/town", "2.0.0", stored("tax 1.0.0")), ["x/town", "eu"])
    assert [(r["source"], r["version"]) for r in ok("legislation_releases", fake_chain.ANONYMOUS)["releases"]] == [
        ("x/town", "1.0.0")]  # nothing was written, and the realm can still publish
    ok("legislation_release_publish", "ctl", **release("1.1.0"))
    assert [r["version"] for r in ok("legislation_releases", fake_chain.ANONYMOUS)["releases"]] == ["1.0.0", "1.1.0"]
    links.relay_release(relayed("eu", "1.0.0", stored("eu tax")), ["eu", "x/town"])  # another layer is still welcome


def test_a_release_is_kept_under_its_source_and_version_so_neither_holds_an_at():
    """Relayed ``a`` ``b@1`` and own ``a@b`` ``1`` would be one id."""
    for odd in (release(source="a@b"), release("1@2")):
        assert "hold no '@'" in denied("legislation_release_publish", "ctl", **odd)
    assert ok("legislation_releases", fake_chain.ANONYMOUS)["releases"] == []
    for source, version in (("a@b", "1"), ("a", "b@1")):
        with pytest.raises(Fail, match="hold no '@'"):
            links.relay_release(relayed(source, version, stored("eu tax")), [source])
    assert ok("legislation_releases", fake_chain.ANONYMOUS)["releases"] == []


def test_a_realm_with_a_link_that_has_not_ended_does_not_become_a_quarter():
    url = "https://example.org/releases.json"
    peer(SRC, realm_info=realm_info(), link_notify=UNANSWERED)
    peer(FOL, link_notify=UNANSWERED)
    ok("sync_source_add", "ctl", source={"Realm": SRC}, name="Src")
    ok("sync_source_add", "ctl", source={"Record": {"url": url, "checksum": CHECKSUM}}, name="The EU")
    ok("follower_accept", "ctl", follower=FOL)
    refused = denied("quarter_join_federation", "ctl", capital="capital")
    assert all(open_link in refused for open_link in (f"sync source {SRC}", f"sync source {url}", f"follower {FOL}"))
    assert not Realm["1"].is_quarter and Quarter["capital"] is None
    ok("sync_source_remove", "ctl", source={"Realm": SRC})
    ok("sync_source_remove", "ctl", source={"Record": {"url": url, "checksum": CHECKSUM}})
    assert denied("quarter_join_federation", "ctl", capital="capital").endswith(f"follower {FOL}")
    ok("follower_remove", "ctl", follower=FOL)
    ok("quarter_join_federation", "ctl", capital="capital")  # every link has ended
    assert Realm["1"].is_quarter and Quarter["capital"] is not None
