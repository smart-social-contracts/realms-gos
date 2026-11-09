"""The revert floor and the IT admin team (docs/codices/layered-codex.md §10, realms-gos#490), in process: a member's
revert restores any earlier codex by its checksum and is decided by the rule recorded with that codex; the IT admin team
is a department the admins designate, and reverts to the previous codex under its own policy; no codex can change the
team; the team is told when a codex method fails. tests/deployed/test_revert.py runs the floor on the real wasm."""

import json

import pytest

import fake_chain
import world
from core import auth, op
from core.ratio import PERCENT
from ggg import Appointment, Codex, Department, Event, LayeredCodex, Proposal, Realm
from kit import call, denied, later, ok, rows
from test_codex import SOURCE, build, manifest, vote_through
from test_legislation_runtime import (BROKEN_GUARD, DENY_ALL, TOWN, activate, failures, fresh_realm, layered,  # noqa: F401
                                      town)

DAY = 86400


def installed(*versions):
    """The legacy codices ``test@<version>``, installed in this order: each replaces the one before."""
    for version in versions:
        ok("codex_install", "ctl", manifest=manifest(version), source=SOURCE)
    return [Codex[f"test@{v}"] for v in versions]


def installed_by_vote_result(version):
    """Install ``test@<version>`` as an accepted proposal's replay would: whatever the root department's policy is."""
    out = op.run(op.dispatch("codex_install", json.dumps({"manifest": manifest(version), "source": SOURCE}),
                             origin="replay", signer="ctl"))
    assert json.loads(out)["ok"]


def statuses(*versions):
    return [Codex[f"test@{v}"].status for v in versions]


def it_team(holders=("bob", "carla", "alice"), policy=None):
    """The department ``IT`` with a seat for each of ``holders``, designated as the IT admin team."""
    team = Department["IT"] or Department(id="IT", parent=Department["Administration"])
    for holder in holders:
        world.seat(f"it.{holder}", holder, team)
    if policy:
        world.policy(team, *policy)
    ok("it_team_set", "ctl", department="IT")
    return team


# ── A member's revert: any earlier codex, by its checksum ───────────────────────────────────────────────────────────


def test_a_revert_restores_the_previous_codex_or_any_earlier_one_named_by_its_checksum():
    build()
    one, two, three = installed("1.0.0", "1.1.0", "1.2.0")
    assert ok("codex_revert", "ctl", codex=one.checksum)["codex"] == "test@1.0.0"  # two codices back, nothing downloaded
    assert statuses("1.0.0", "1.1.0", "1.2.0") == ["active", "inactive", "reverted"]
    ok("codex_install", "ctl", manifest=manifest("1.3.0"), source=SOURCE)
    assert ok("codex_revert", "ctl")["codex"] == "test@1.0.0"  # none named: the one the active codex replaced
    assert call("codex_revert", "ctl")["kind"] == "conflict"  # the first codex replaced none
    assert ok("codex_revert", "ctl", codex="sha256:" + three.checksum)["codex"] == "test@1.2.0"  # as GGG writes a checksum
    assert ok("codex_revert", "ctl", codex="test@1.1.0")["codex"] == "test@1.1.0"  # or as the history lists it


def test_a_revert_refuses_a_codex_that_is_not_an_earlier_one():
    build()
    one, two = installed("1.0.0", "1.1.0")
    assert call("codex_revert", "ctl", codex="0" * 64)["kind"] == "not_found"
    assert call("codex_revert", "ctl", codex=two.checksum)["kind"] == "conflict"  # the active codex
    waiting = LayeredCodex(id="sha256:" + "a" * 64, status="pending")
    assert "codex_activate activates it" in call("codex_revert", "ctl", codex=waiting.id)["error"]
    assert call("codex_revert", "ctl", codex=7)["kind"] == "invalid_argument"
    assert statuses("1.0.0", "1.1.0") + [LayeredCodex[waiting.id].status] == ["inactive", "active", "pending"]  # never activated
    ok("codex_uninstall", "ctl")
    assert call("codex_revert", "ctl")["error"] == "no codex is active"


def test_a_revert_filed_as_a_proposal_names_the_codex_it_restores():
    build()
    one, two, three = installed("1.0.0", "1.1.0", "1.2.0")
    world.policy(Department["Administration"], 2, 3)
    filed = ok("codex_revert", "ctl")  # none named: resolved now, so what is voted on is what runs
    assert Proposal[filed["proposal"]].action == {"op": "codex_revert", "args": {"codex": "test@1.1.0"}}
    installed_by_vote_result("1.3.0")  # the codex the call would now restore is another one
    vote_through(filed["proposal"])
    assert statuses("1.1.0", "1.3.0") == ["active", "reverted"]
    refused = call("proposal_create", "alice", title="revert", action={"op": "codex_revert", "args": {}})
    assert refused["kind"] == "invalid_argument" and "names the codex it restores" in refused["error"]
    unknown = call("proposal_create", "alice", title="revert", action={"op": "codex_revert", "args": {"codex": "0" * 64}})
    assert unknown["kind"] == "not_found"
    active = call("proposal_create", "alice", title="revert", action={"op": "codex_revert", "args": {"codex": "test@1.1.0"}})
    assert active["kind"] == "conflict"


# ── Its vote follows the rule recorded with the codex restored (rule 15, D-14) ──────────────────────────────────────


def settings(threshold, quorum=0, casting=""):
    ok("governance_update", "ctl", voting_threshold=threshold, voting_quorum=quorum, casting_vote=casting)


def rule_of(proposal):
    p = Proposal[proposal]
    return p.threshold, p.quorum, p.casting_vote


def test_a_revert_vote_follows_the_rule_recorded_with_the_codex_restored_whatever_the_settings_are_now():
    build()
    settings(60 * PERCENT, 25 * PERCENT, "dana")
    one, = installed("1.0.0")
    assert one.voting_rule["threshold"] == 60 * PERCENT  # recorded when the codex became active
    settings(50 * PERCENT)
    two, = installed("1.1.0")
    assert two.voting_rule == {"threshold": 50 * PERCENT, "quorum": 0, "threshold_of": "cast", "casting_vote": ""}
    settings(100 * PERCENT, 100 * PERCENT)  # the rules the codex now running left behind
    revert = ok("proposal_create", "alice", title="back", action={"op": "codex_revert", "args": {"codex": "test@1.0.0"}})
    assert rule_of(revert["proposal"]) == (60 * PERCENT, 25 * PERCENT, "dana")
    other = ok("proposal_create", "alice", title="other")
    assert rule_of(other["proposal"]) == (100 * PERCENT, 100 * PERCENT, "")
    stricter = ok("proposal_create", "alice", title="back", threshold=80 * PERCENT,
                  action={"op": "codex_revert", "args": {"codex": "test@1.0.0"}})
    assert rule_of(stricter["proposal"])[0] == 80 * PERCENT
    weaker = call("proposal_create", "alice", title="back", threshold=10 * PERCENT,
                  action={"op": "codex_revert", "args": {"codex": "test@1.0.0"}})
    assert "weaker" in weaker["error"]


def test_a_codex_keeps_the_rule_it_was_recorded_with_when_it_is_restored():
    build()
    settings(60 * PERCENT)
    installed("1.0.0")
    settings(70 * PERCENT)
    installed("1.1.0")
    ok("codex_revert", "ctl")  # restored under settings that are not the ones it was recorded with
    assert Codex["test@1.0.0"].voting_rule["threshold"] == 60 * PERCENT
    settings(90 * PERCENT)
    installed("1.2.0")
    revert = ok("proposal_create", "alice", title="back", action={"op": "codex_revert", "args": {"codex": "test@1.0.0"}})
    assert rule_of(revert["proposal"])[0] == 60 * PERCENT


def test_a_codex_with_no_recorded_rule_is_restored_under_the_rule_in_force():
    build()
    one, two = installed("1.0.0", "1.1.0")
    one.voting_rule = {}  # it became active before the realm recorded rules
    settings(70 * PERCENT)
    revert = ok("proposal_create", "alice", title="back", action={"op": "codex_revert", "args": {"codex": one.id}})
    assert rule_of(revert["proposal"])[0] == 70 * PERCENT


# ── The IT admin team: a department the admins designate ─────────────────────────────────────────────────────────────


def test_the_admins_designate_the_team_directly_in_setup_and_the_realm_votes_after_launch():
    build()
    Department(id="IT", parent=Department["Administration"])
    assert "it_team.set" in denied("it_team_set", "alice", department="IT")
    assert call("it_team_set", "ctl", department="Nowhere")["kind"] == "not_found"
    assert ok("it_team_set", "ctl", department="IT")["department"] == "IT" and Realm["1"].it_team == "IT"
    assert ok("codex_status", "alice", query=True)["it_team"] == "IT"
    Realm["1"].status = "beta"
    world.policy(Department["Administration"], 2, 3)
    filed = ok("it_team_set", "ctl", department="Civil Registry")  # a realm-wide vote
    assert Realm["1"].it_team == "IT"
    vote_through(filed["proposal"])
    assert Realm["1"].it_team == "Civil Registry"
    vote_through(ok("it_team_set", "ctl", department="")["proposal"])
    assert Realm["1"].it_team == ""


def test_the_department_that_is_the_team_cannot_be_deleted_until_another_is_designated():
    build()
    Department(id="Spare", parent=Department["Administration"])
    ok("it_team_set", "ctl", department="Spare")
    assert call("department_delete", "ctl", department="Spare")["kind"] == "conflict"
    ok("it_team_set", "ctl", department="")
    ok("department_delete", "ctl", department="Spare")
    assert Department["Spare"] is None


def test_there_is_no_fast_revert_without_a_team():
    build()
    installed("1.0.0", "1.1.0")
    out = call("codex_revert_fast", "ctl")
    assert out["kind"] == "conflict" and "no IT admin team" in out["error"]
    assert Codex["test@1.1.0"].status == "active"


def test_the_team_reverts_fast_to_the_previous_codex_and_only_to_it():
    build()
    one, two, three = installed("1.0.0", "1.1.0", "1.2.0")
    it_team(("bob",))  # a policy of one: the team decides directly
    assert "codex.revert_fast" in denied("codex_revert_fast", "alice")  # not a seat of the team
    assert call("codex_revert_fast", "bob", codex=one.checksum)["kind"] == "conflict"  # not the previous one
    assert ok("codex_revert_fast", "bob")["codex"] == "test@1.1.0"
    assert statuses("1.0.0", "1.1.0", "1.2.0") == ["inactive", "active", "reverted"]
    assert ok("codex_revert_fast", "bob")["codex"] == "test@1.0.0"
    assert call("codex_revert_fast", "bob")["kind"] == "conflict"  # the first codex replaced none


def test_a_fast_revert_is_decided_by_the_teams_own_policy():
    build()
    one, two = installed("1.0.0", "1.1.0")
    team = it_team(("bob", "carla", "alice"), policy=(2, 3, 0, ["carla"]))
    filed = ok("codex_revert_fast", "bob")  # two of the three seats, and carla's veto
    assert Proposal[filed["proposal"]].department.id == team.id
    assert Proposal[filed["proposal"]].action == {"op": "codex_revert_fast", "args": {"codex": "test@1.0.0"}}
    assert "not eligible" in denied("proposal_vote", "dana", proposal=filed["proposal"], choice="yes")
    ok("proposal_vote", "bob", proposal=filed["proposal"], choice="yes")
    ok("proposal_vote", "alice", proposal=filed["proposal"], choice="yes")
    assert statuses("1.0.0", "1.1.0") == ["inactive", "active"]  # carla holds a veto and has not voted
    ok("proposal_vote", "carla", proposal=filed["proposal"], choice="yes")
    assert Proposal[filed["proposal"]].status == "executed" and statuses("1.0.0", "1.1.0") == ["active", "reverted"]


def test_a_fast_revert_is_recorded_with_an_event():
    build()
    installed("1.0.0", "1.1.0")
    it_team(("bob",))
    ok("codex_revert_fast", "bob")
    event = max(rows(Event), key=lambda e: int(e.id))
    assert (event.op, event.user, event.result["codex"]) == ("codex_revert_fast", "bob", "test@1.0.0")


def test_the_teams_seat_holders_hold_the_permission_only_while_the_department_is_the_team():
    build()
    it_team(("bob",))
    assert "codex.revert_fast" in auth.permissions("bob") and "codex.revert_fast" not in auth.permissions("alice")
    ok("it_team_set", "ctl", department="")
    assert "codex.revert_fast" not in auth.permissions("bob")


# ── No codex may change the team ────────────────────────────────────────────────────────────────────────────────────

CHANGES = [
    ("department_set_policy", {"department": "IT", "m": 1, "n": 1}),
    ("department_edit", {"department": "IT", "description": "mine"}),
    ("department_delete", {"department": "IT"}),
    ("department_expel", {"department": "IT", "user": "bob"}),
    ("position_create", {"department": "IT", "position": "it.extra"}),
    ("position_edit", {"position": "it.bob", "headcount": 3}),
    ("position_close", {"position": "it.bob"}),
    ("appoint", {"position": "it.bob", "user": "dana"}),
    ("appoint_acting", {"position": "it.bob", "user": "dana"}),
    ("appointment_end", {"appointment": "it.bob:bob"}),
    ("it_team_set", {"department": "Health"}),
]


def as_codex(name, args):
    """What a codex's effect does: the call, as the codex, naming the operation it declares."""
    return json.loads(op.run(op.dispatch(name, json.dumps(args), origin="codex", signer=fake_chain.self_id(),
                                         declared=(name, "proposal_create", "federal_vote_propose"))))


@pytest.mark.parametrize("name,args", CHANGES, ids=[name for name, _ in CHANGES])
def test_a_codex_cannot_change_the_team_nor_carry_a_change_to_a_vote(name, args):
    build()
    it_team(("bob",))
    refused = as_codex(name, args)
    assert (refused["ok"], refused["kind"]) == (False, "forbidden") and "no codex may" in refused["error"]
    carried = as_codex("proposal_create", {"title": "x", "action": {"op": name, "args": args}})
    assert (carried["ok"], carried["kind"]) == (False, "forbidden") and "no codex may" in carried["error"]
    assert Proposal.max_id() == 0 and Department["IT"] is not None


def test_a_codex_changes_any_other_department_as_before():
    build()
    it_team(("bob",))
    assert as_codex("department_set_policy", {"department": "Health", "m": 1, "n": 1})["ok"]


CHANGE_THE_TEAM = '''
from realms.legislation import above

class Register(above("register")):
    def on_t_codex_poke(self, event):
        what = event.args["what"]
        if what == "policy":
            self.ggg.department_set_policy(department="IT", m=1, n=1)
        if what == "seat":
            self.ggg.appoint(position="it.bob", user="dana")
        if what == "designate":
            self.ggg.it_team_set(department="Health")
'''


@pytest.mark.parametrize("what,error", [
    ("policy", "department_set_policy: department_set_policy changes the IT admin team: no codex may"),
    ("seat", "appoint: appoint changes the IT admin team: no codex may"),
    ("designate", "it_team_set: it_team_set is about a reserved operation: no chapter may call it")])
def test_a_chapter_that_tries_to_change_the_team_fails_and_changes_nothing(what, error):
    build()
    it_team(("bob",))
    town({(TOWN, "register"): CHANGE_THE_TEAM})
    world.policy(Department["IT"], 2, 2)
    ok("t_codex_poke", what=what)
    assert failures()["x/town/register.py"]["on_t_codex_poke"]["error"] == error
    assert Department["IT"].policy == {"m": 2, "n": 2, "quorum": 0, "veto": []} and Proposal.max_id() == 0
    assert [a.user for a in rows(Appointment) if a.position.id == "it.bob"] == ["bob"]
    assert Realm["1"].it_team == "IT"


# ── The team is told when a codex method fails ──────────────────────────────────────────────────────────────────────


def inbox(who="bob"):
    return [(n["title"], n["topic"]) for n in ok("notification_list", who, recipient="Department:IT")["notifications"]]


def test_the_team_is_told_of_a_methods_first_failure_and_again_after_a_day_if_it_still_fails():
    build()
    it_team(("bob", "carla"))
    town({(TOWN, "water"): BROKEN_GUARD})
    assert inbox() == []
    call("t_act", what="break the water")
    title = "The codex fails: check_t_act of x/town/water.py"
    assert inbox() == [(title, "codex.failing")] and inbox("carla") == inbox()
    body = ok("notification_list", "bob", recipient="Department:IT")["notifications"][0]["body"]
    assert body == "ValueError: no water here"
    call("t_act", what="break the water")  # failing again at once: told already
    later(DAY - 1)
    call("t_act", what="break the water")
    assert len(inbox()) == 1
    later(1)
    call("t_act", what="break the water")  # a day since the last notification, and it still fails
    assert len(inbox()) == 2 and failures()["x/town/water.py"]["check_t_act"]["count"] == 4
    ok("t_act", what="fine")  # it works again: a later failure is a first one
    call("t_act", what="break the water")
    assert len(inbox()) == 3


def test_a_failure_before_a_team_is_designated_is_told_when_it_next_fails_and_a_team_exists():
    build()
    town({(TOWN, "water"): BROKEN_GUARD})
    call("t_act", what="break the water")
    assert "notified" not in failures()["x/town/water.py"]["check_t_act"]
    it_team(("bob",))
    call("t_act", what="break the water")
    assert len(inbox()) == 1


# ── A codex that forbids and guards everything is still reverted, by a vote and by the team ─────────────────────────

FORBID_ALL = '''
from realms.legislation import F, above, forbid

class Tax(above("tax")):
    no_proposals = forbid("proposal_create", F.title != "-")
    no_votes = forbid("proposal_vote", F.proposal != "-")
'''


def blocking_codex():
    first = town()
    first.status = "inactive"
    return first, activate(layered({(TOWN, "procedure"): DENY_ALL, (TOWN, "tax"): FORBID_ALL}, layers=[TOWN],
                                   previous=first.id))


def test_a_codex_that_forbids_and_guards_everything_is_reverted_by_a_member_vote():
    first, blocker = blocking_codex()
    assert call("proposal_create", "alice", title="x")["kind"] == "forbidden"
    revert = ok("proposal_create", "alice", title="revert", action={"op": "codex_revert", "args": {"codex": first.id}})
    for voter in world.PEOPLE:
        ok("proposal_vote", voter, proposal=revert["proposal"], choice="yes")
    assert Proposal[revert["proposal"]].status == "executed"
    assert (LayeredCodex[first.id].status, LayeredCodex[blocker.id].status) == ("active", "reverted")


def test_a_codex_that_forbids_and_guards_everything_is_reverted_by_the_team_under_its_own_policy():
    first, blocker = blocking_codex()
    it_team(("bob", "carla"), policy=(2, 2))
    filed = ok("codex_revert_fast", "bob")
    assert Proposal[filed["proposal"]].action["args"] == {"codex": first.id}
    ok("proposal_vote", "bob", proposal=filed["proposal"], choice="yes")
    ok("proposal_vote", "carla", proposal=filed["proposal"], choice="yes")
    assert (LayeredCodex[first.id].status, LayeredCodex[blocker.id].status) == ("active", "reverted")
    assert call("proposal_create", "alice", title="x")["ok"]  # the first codex runs again


def test_the_guardrails_name_no_reserved_operation():
    from realms.legislation import RESERVED

    assert not [name for name in RESERVED if name in auth.GUARDRAILS or name in auth.POLICIES]


def test_a_reserved_operation_and_a_proposal_about_one_are_reserved_and_the_list_is_the_librarys():
    from core import legislation
    from realms.legislation import RESERVED

    assert set(RESERVED) == {"codex_revert", "codex_revert_fast", "it_team_set", "sync_source_remove"}
    for name in RESERVED:
        assert legislation.is_reserved(name, {})
        assert legislation.is_reserved("proposal_create", {"action": {"op": name, "args": {}}})


def test_a_codex_stored_before_rules_were_recorded_reads_as_having_none():
    assert Codex.migrate({"id": "test@1.0.0"}, 1, 2)["voting_rule"] == {}
    assert LayeredCodex.migrate({"id": "sha256:0"}, 1, 2)["voting_rule"] == {}
    assert Codex.migrate({"voting_rule": {"threshold": 5}}, 1, 2)["voting_rule"] == {"threshold": 5}  # and keeps one
