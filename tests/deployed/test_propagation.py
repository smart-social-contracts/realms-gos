"""A new codex across a realm's own quarters on real wasms (docs/codices/layered-codex.md §9.5, realms-gos#501): a
capital and three quarters on one PocketIC instance. One realm-wide vote installs a codex with a switch time T; the
quarters that stage in time switch at T, one that is paused by an upgrade keeps its codex until an admin resumes it, and
one that cannot be told never stages. A quarter that is ready after T switches only to the codex the capital still runs.
Calls between the quarters carry the sender's codex checksum.

The realms are built once for the module and the tests below use them in order.
"""

import json

import pytest
from network import Network
from test_installs import BIG_CHAPTER, HOUR, Site, expected_id, own_layer

JUDGE_AFTER = 60  # api.propagation: the capital judges its quarters this long after it switched
OUT = "import json\nprint(json.dumps({!r}))"
ROWS = ("import json\nfrom ggg import {entity}\n"
        "print(json.dumps([{{k: getattr(r, k) for k in {fields!r}}} for r in {entity}.load_some(1, 50) if r]))")


@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)


@pytest.fixture(scope="module")
def site(net):
    return Site(net.web)


@pytest.fixture(scope="module")
def capital(net):
    return net.realm("capital")


@pytest.fixture(scope="module")
def early(net):
    return net.realm("early", members=("alice",))


@pytest.fixture(scope="module")
def late(net):
    return net.realm("late", members=("alice",))


@pytest.fixture(scope="module")
def never(net):
    return net.realm("never", members=("alice",))


@pytest.fixture(scope="module")
def federation(net, capital, early, late, never):
    net.register_quarters(capital, early, late, never)
    return capital, early, late, never


def status(realm):
    return realm.ok("codex_status", "founder", query=True)


def active(realm):
    codex = status(realm)["codex"]
    return codex["id"] if codex else None


def job(realm):
    return status(realm)["job"] or {}


def flag(capital, quarter):
    """The flag the capital keeps for ``quarter``, as the directory shows it."""
    row = next(r for r in capital.ok("quarter_directory", None, query=True)["quarters"] if r["id"] == quarter.cid)
    return json.loads(row["compliance"])


def rows(realm, entity, *fields):
    return json.loads(realm.shell(ROWS.format(entity=entity, fields=fields)))


def titles(realm):
    return [n["title"] for n in realm.ok("notification_list", "founder", query=True)["notifications"]]


def test_a_codex_reaches_the_quarters_that_stage_in_time_at_t_and_the_others_keep_theirs(
        net, site, people, federation, costs):
    capital, early, late, never = federation
    layer = {**own_layer(people["founder"]), "arcadia/water": BIG_CHAPTER}  # a chapter of the most a chapter may be
    never.stop()  # it cannot be told
    t = capital.now() + HOUR
    proposal = capital.govern("codex_install", site.change_list(layer), effective_at=t)

    # late is upgraded in the middle of its staging: its job is paused, and nothing resumes it by itself
    net.upgrade_when(late, lambda: job(late).get("step") == "fetch_chapters", rounds=1)
    assert (job(late)["status"], job(late)["step"]) == ("paused", "fetch_chapters")
    net.wait_until(lambda: job(early).get("step") == "wait" and job(capital).get("step") == "wait", rounds=2)

    codex_id = expected_id(layer)
    for staged in (capital, early):
        seen = status(staged)
        assert seen["codex"] is None and seen["pending"]["id"] == codex_id and seen["pending"]["activates_at"] == t
    assert {c["route"] for c in status(early)["pending"]["chapters"].values()} == {"capital"}
    assert {c["route"] for c in status(capital)["pending"]["chapters"].values()} == {"https"}
    assert job(early)["proposal"] == proposal

    early.upgrade()  # a staged job has no timer to lose: its schedule is armed again, and it waits for T
    assert (job(early)["status"], job(early)["step"]) == ("running", "wait")
    net.wait(HOUR - 600)  # time passes; nothing switches before T
    assert [active(realm) for realm in (capital, early, late)] == [None, None, None]

    capital.run_at_effective_at(proposal)
    net.wait_until(lambda: active(early) == codex_id, rounds=2)  # at T it asks the capital, then switches
    assert active(capital) == active(early) == codex_id  # the same instant: T is an exact instant on every clock
    switched = {realm.name: status(realm)["codex"]["installed_at"] for realm in (capital, early)}
    assert t <= switched["capital"] <= t + 2 and abs(switched["early"] - switched["capital"]) <= 2
    asked = [m for m in rows(capital, "FederationMessage", "topic", "source") if m["topic"] == "codex.required"]
    assert [m["source"] for m in asked] == [early.cid]  # only the quarter that switched in time asked
    net.wait(2)
    assert titles(capital) == ["activated"] and titles(early) == []  # on_codex_activate runs once, on the capital

    assert active(late) is None and job(late)["status"] == "paused"  # it was not ready: it keeps its codex
    net.wait(JUDGE_AFTER)
    assert flag(capital, early) == "compliant"
    behind = {"NonCompliant": {"since": t, "stale": ["arcadia"]}}
    assert flag(capital, late) == behind and flag(capital, never) == behind

    # an admin's resume completes the paused job: ready after T, it switches because the capital still runs that codex
    late.ok("codex_install_resume", "ctl", job=job(late)["id"])
    net.wait_until(lambda: job(late).get("status") in ("done", "failed"), rounds=2)
    assert (job(late)["status"], active(late)) == ("done", codex_id)
    assert status(late)["codex"]["installed_at"] > t and titles(late) == []
    net.wait(2)
    assert flag(capital, late) == "compliant"

    never.start()
    assert active(never) is None and job(never) == {}  # nobody told it: it never staged, and keeps its codex
    assert flag(capital, never) == behind
    told = [m for m in rows(capital, "FederationMessage", "topic", "response") if m["topic"] == "codex.stage"]
    assert sorted(next(iter(m["response"])) for m in told) == sorted(q.cid for q in (early, late, never))
    refused = next(m["response"][never.cid] for m in told if never.cid in m["response"])
    assert refused["ok"] is False and "failed" in refused["error"]
    never.stop()  # the later tests do not need it

    # what receiving a 256 KB chapter from the capital costs a quarter
    reply = json.dumps({"ok": True, "text": BIG_CHAPTER.decode()})
    costs.measure(late, "quarter: read the capital's 256 KB reply (candid text, then json)",
                  "import json\nfrom core.candid import quote, unquote\n"
                  f"text = quote({reply!r})\njson.loads(unquote(text))")


def layer_of(people, tax="1.0.0"):
    return {**own_layer(people["founder"], tax), "arcadia/water": BIG_CHAPTER}


def test_a_move_between_quarters_carries_the_codex_of_the_quarter_it_leaves(net, people, federation):
    capital, early, late, _ = federation
    codex_id = expected_id(layer_of(people))
    for quarter in (early, late):
        quarter.ok("quarter_sync", "ctl")  # each now knows what the capital judged of the other
    early.ok("self_change_quarter", "alice", quarter=late.cid)
    arrived = [m for m in rows(late, "FederationMessage", "topic", "source", "codex", "compliance")
               if m["topic"] == "user.arrive"]
    assert [(m["source"], m["codex"], json.loads(m["compliance"])) for m in arrived] == [
        (early.cid, codex_id, "compliant")]
    (moved,) = rows(early, "Forward", "origin", "destination", "codex", "compliance")
    assert (moved["codex"], json.loads(moved["compliance"])) == (codex_id, "compliant")  # late, as early knows it
    # quarter_sync and quarter_report carry the codex too: the capital knows what each quarter runs
    assert {row["id"]: row["codex_id"] for row in capital.ok("quarter_directory", None, query=True)["quarters"]
            }[early.cid] == codex_id


def test_the_legs_of_a_federal_vote_carry_the_codex_and_flag_of_their_quarter(net, people, federation):
    capital, early, late, never = federation
    codex_id = expected_id(layer_of(people))
    never.start()  # a vote opens in every active quarter
    vote = capital.ok("federal_vote_propose", "founder", title="legs",
                      action={"op": "governance_update", "args": {"voting_window": 0}})["vote"]
    for realm in (capital, early, late, never):
        (leg,) = [r for r in rows(realm, "FederalVoteLeg", "id", "proposal_id") if r["id"] == f"{vote}:{realm.cid}"]
        for member in realm.members:
            if realm.proposal(leg["proposal_id"])["status"] == "voting":
                realm.ok("proposal_vote", member, proposal=leg["proposal_id"], choice="yes")
    for quarter in (early, late, never):
        assert quarter.ok("federal_vote_finalize", "founder", vote=vote)["reported"]
    capital.ok("federal_vote_finalize", "founder", vote=vote)
    legs = {leg["quarter"]: leg for leg in rows(capital, "FederalVoteLeg", "quarter", "codex", "compliance")}
    assert {q: (leg["codex"], json.loads(leg["compliance"])) for q, leg in legs.items()} == {
        capital.cid: (codex_id, "compliant"), early.cid: (codex_id, "compliant"), late.cid: (codex_id, "compliant"),
        never.cid: ("", {"NonCompliant": {"since": status(capital)["codex"]["activates_at"], "stale": ["arcadia"]}})}
    never.stop()


def test_a_quarter_ready_after_t_does_not_switch_to_a_codex_the_capital_reverted(net, site, people, federation):
    capital, early, late, _ = federation
    first = expected_id(layer_of(people))
    early.stop()  # it is not told, and the other quarter alone is paused
    layer = layer_of(people, "2.0.0")
    t = capital.now() + HOUR
    proposal = capital.govern("codex_install", site.change_list(layer, replaces=first), effective_at=t)
    net.upgrade_when(late, lambda: job(late).get("step") == "check_change_list", rounds=1)
    assert job(late)["status"] == "paused"
    capital.run_at_effective_at(proposal)
    assert active(capital) == expected_id(layer)
    capital.govern("codex_revert", {"codex": first})  # the realm goes back before late is ready
    assert active(capital) == first

    late.ok("codex_install_resume", "ctl", job=job(late)["id"])
    net.wait_until(lambda: job(late).get("status") in ("done", "failed"), rounds=2)
    assert job(late)["status"] == "failed" and "a codex it has left is not switched to" in job(late)["error"]
    assert active(late) == first and status(late)["pending"] is None  # it never ran the codex the capital left
    net.wait(2)
    assert flag(capital, late) == "compliant"  # it runs what the capital runs


def test_a_quarter_ready_after_t_does_not_switch_to_a_codex_the_capital_replaced_by_a_newer_one(net, site, people,
                                                                                               federation):
    capital, early, late, _ = federation
    first = expected_id(layer_of(people))
    second = layer_of(people, "2.0.0")
    third = {**second, "arcadia/procedure": second["arcadia/procedure"] + b"\n# newer\n"}
    t = capital.now() + HOUR
    proposal = capital.govern("codex_install", site.change_list(second, replaces=first), effective_at=t)
    net.upgrade_when(late, lambda: job(late).get("step") == "check_change_list", rounds=1)
    assert job(late)["status"] == "paused"
    capital.run_at_effective_at(proposal)
    capital.govern("codex_install", site.change_list(third, replaces=expected_id(second)))  # a newer codex, at once
    net.wait_until(lambda: job(capital).get("status") in ("done", "failed"), rounds=2)
    assert job(capital)["status"] == "done", job(capital)
    net.wait_until(lambda: job(late).get("id") != "J-3" and job(late).get("status") in ("done", "failed"), rounds=2)
    jobs = {j["id"]: j for j in rows(late, "InstallJob", "id", "status", "error")}
    assert jobs["J-3"]["status"] == "failed" and "which replaces it" in jobs["J-3"]["error"]
    assert active(late) == first and status(late)["pending"] is None  # neither of them was ever activated


# ---- A revert on the capital reaches the quarters. A federation of its own: the codices above are left as they are.


@pytest.fixture(scope="module")
def reverting(net):
    """A capital and two quarters, ``follower`` and ``absent``, registered and synced."""
    chief = net.realm("chief")
    follower, absent = net.realm("follower", members=("alice",)), net.realm("absent", members=("alice",))
    net.register_quarters(chief, follower, absent)
    return chief, follower, absent


def installed_everywhere(net, site, realms, layer, replaces=None):
    """The capital installs ``layer`` at once and the quarters follow: returns when every realm runs it."""
    realms[0].govern("codex_install", site.change_list(layer, replaces=replaces))
    net.wait_until(lambda: all(active(realm) == expected_id(layer) for realm in realms), rounds=2)


def test_a_quarter_that_switched_to_a_bad_codex_follows_the_capitals_revert_and_a_down_one_catches_up(
        net, site, people, reverting):
    chief, follower, absent = reverting
    good, bad = layer_of(people), layer_of(people, "2.0.0")
    installed_everywhere(net, site, reverting, good)
    installed_everywhere(net, site, reverting, bad, replaces=expected_id(good))
    net.wait(2)
    activated = titles(chief)
    assert activated == ["activated", "activated"] and titles(follower) == []

    absent.stop()  # it will not hear of the revert
    chief.govern("codex_revert", {"codex": expected_id(good)})
    reverted = chief.now()
    assert active(chief) == expected_id(good)
    net.wait_until(lambda: active(follower) == expected_id(good), rounds=2)  # nothing is downloaded: it has the codex
    assert [h["status"] for h in status(follower)["history"]] == ["active", "reverted"]  # the good codex, the bad one
    net.wait(2)
    assert titles(chief) == activated and titles(follower) == []  # a revert runs no on_codex_activate

    net.wait(JUDGE_AFTER)
    assert flag(chief, follower) == "compliant"
    behind = flag(chief, absent)  # it runs the bad codex, since the revert, not since the good one was first installed
    assert abs(behind["NonCompliant"]["since"] - reverted) <= 2 and behind["NonCompliant"]["stale"] == ["arcadia"]

    absent.start()
    absent.ok("quarter_sync", "ctl")  # it reports the codex the realm voted out: the capital tells it again
    net.wait_until(lambda: active(absent) == expected_id(good), rounds=2)
    net.wait(2)
    assert flag(chief, absent) == "compliant" and titles(absent) == []


def test_a_quarter_that_missed_the_withdrawal_does_not_switch_at_t_for_the_capital_no_longer_requires_it(
        net, site, people, reverting):
    chief, follower, _ = reverting
    good = layer_of(people)
    third = {**good, "arcadia/procedure": good["arcadia/procedure"] + b"\n# third\n"}
    proposal = chief.govern("codex_install", site.change_list(third, replaces=expected_id(good)),
                            effective_at=chief.now() + HOUR)
    net.wait_until(lambda: job(follower).get("step") == "wait", rounds=2)
    follower.stop()  # it will not hear that the proposal is stopped
    chief.govern("proposal_stop", {"proposal": proposal})
    follower.start()
    assert status(follower)["pending"]["id"] == expected_id(third)  # still staged: nobody told it
    net.wait(HOUR + 60)
    assert active(follower) == expected_id(good) and status(follower)["pending"] is None
    assert job(follower)["status"] == "failed"
    assert "a codex it has left is not switched to" in job(follower)["error"]
