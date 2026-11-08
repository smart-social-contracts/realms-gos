"""A proposal's voting rule (ggg I-GOV-3, I-GOV-7; realms-gos#547): stored when it is filed, never weaker than its
scope's default, and applied as it is stored when the vote ends.

Agora has seven members. Its default rule is the realm's: yes votes exceed half of the yes and no votes cast.
"""

import world
from core.ratio import ONE, PERCENT
from ggg import Department, Member, Proposal, Realm
from ic_python_db import Database
from kit import call, later, ok

WEEK = 8 * 86400  # past the voting window


def civil_registry(m, n=4, quorum=0, voters=("alice", "carla")):
    """Civil Registry deciding m of n, with the seats of dana, bob and ``voters``."""
    depts = world.build()
    for voter in voters:
        world.seat(f"civil-registry.{voter}", voter, depts["Civil Registry"])
    world.policy(depts["Civil Registry"], m, n, quorum)


def file(department=None, **rule):
    out = call("proposal_create", "alice", title="x", **({"department": department} if department else {}), **rule)
    return out["proposal"] if out["ok"] else out


def vote(proposal, choice, *voters):
    for voter in voters:
        ok("proposal_vote", voter, proposal=proposal, choice=choice)


def finalized(proposal):
    later(WEEK)
    return ok("proposal_finalize", "ctl", proposal=proposal)["status"]


def rule(proposal):
    p = Proposal[proposal]
    return p.threshold, p.quorum, p.threshold_of, p.casting_vote


# ---- What is stored


def test_a_realm_wide_proposal_stores_the_realms_rule_and_its_casting_vote():
    world.build()
    ok("governance_update", "ctl", voting_threshold=60 * PERCENT, voting_quorum=25 * PERCENT,
       casting_vote="dana")
    p = file(kind="legislation", matter="tax")
    assert rule(p) == (60 * PERCENT, 25 * PERCENT, "cast", "dana")
    assert (Proposal[p].kind, Proposal[p].matter) == ("legislation", "tax")
    ok("governance_update", "ctl", casting_vote="", voting_threshold=70 * PERCENT)
    assert rule(p) == (60 * PERCENT, 25 * PERCENT, "cast", "dana")  # fixed at creation, like the action
    assert rule(file()) == (70 * PERCENT, 25 * PERCENT, "cast", "")


def test_a_departments_proposal_stores_m_of_n_rounded_down_over_the_eligible_seats():
    civil_registry(2, n=3, quorum=50 * PERCENT)
    p = file("Civil Registry")
    assert rule(p) == (66_666_666, 50 * PERCENT, "eligible", "")  # a department has no casting vote
    assert Proposal[p].tally["eligible"] == 4


def test_a_direct_policy_is_the_same_rule_over_one_seat():
    world.build()
    p = file("Infrastructure")
    assert rule(p) == (ONE, 0, "eligible", "")
    assert Proposal[p].tally["eligible"] == 1


def test_a_governed_call_files_its_scopes_default_rule():
    civil_registry(2, n=3, quorum=10 * PERCENT)
    ok("governance_update", "ctl", voting_threshold=70 * PERCENT)
    department = ok("department_create", "ctl", department="Records", parent="Civil Registry")["proposal"]
    assert rule(department) == (66_666_666, 10 * PERCENT, "eligible", "")
    world.policy(Department["Administration"], 2, 3)
    realm_wide = ok("mandate_create", "ctl", title="Road works")["proposal"]
    assert rule(realm_wide) == (70 * PERCENT, 0, "cast", "")


def test_the_electorate_is_counted_when_the_vote_opens():
    world.build()
    p = file()
    world.person("zed")
    assert ok("proposal_get", "alice", proposal=p)["eligible"] == 7
    assert ok("proposal_get", "alice", proposal=file())["eligible"] == 8


# ---- Weaker rules are refused


def test_a_rule_weaker_than_the_scopes_default_is_refused():
    civil_registry(2, quorum=20 * PERCENT)
    ok("governance_update", "ctl", voting_quorum=30 * PERCENT)
    for scope, stated in ((None, {"threshold": 40 * PERCENT}), (None, {"quorum": 20 * PERCENT}),
                          ("Civil Registry", {"threshold": 40 * PERCENT}), ("Civil Registry", {"quorum": 10 * PERCENT}),
                          ("Civil Registry", {"threshold_of": "cast"}), (None, {"threshold_of": "other"})):
        out = file(scope, **stated)
        assert not out["ok"] and out["kind"] == "invalid_argument", (scope, stated, out)
    assert Proposal.count() == 0


def test_a_rule_out_of_range_is_refused_before_anything_is_written():
    world.build()
    for stated in ({"threshold": ONE + 1}, {"quorum": -1}, {"threshold": "100000001"}):
        out = file(**stated)
        assert out["kind"] == "invalid_argument" and "must be between 0 and" in out["error"], stated
    assert Proposal.count() == 0


def test_a_stricter_rule_is_stored():
    civil_registry(2)
    assert rule(file(threshold=60 * PERCENT, quorum=10 * PERCENT, threshold_of="eligible")) == (
        60 * PERCENT, 10 * PERCENT, "eligible", "")
    assert rule(file("Civil Registry", threshold=75 * PERCENT, quorum=50 * PERCENT)) == (
        75 * PERCENT, 50 * PERCENT, "eligible", "")
    assert rule(file(threshold=50 * PERCENT, threshold_of="cast")) == (50 * PERCENT, 0, "cast", "")  # as strict


# ---- A realm-wide vote


def test_the_yes_votes_exceed_the_threshold_of_the_votes_cast_or_of_all_the_eligible():
    world.build()
    cast, eligible = file(), file(threshold_of="eligible")
    for p in (cast, eligible):
        vote(p, "yes", "alice", "bob", "dana")  # 3 yes of the 3 cast, and of the 7 who could vote
    assert finalized(cast) == "accepted"
    assert finalized(eligible) == "rejected"  # half of seven is more than three
    four = file(threshold_of="eligible")
    vote(four, "yes", "alice", "bob", "dana", "carla")
    assert finalized(four) == "accepted"


def test_a_raised_threshold_decides_the_vote():
    world.build()
    p = file(threshold=75 * PERCENT)
    vote(p, "yes", "alice", "bob", "dana")
    vote(p, "no", "carla")
    assert finalized(p) == "rejected"  # exactly three quarters is not more than three quarters
    q = file(threshold=75 * PERCENT)
    vote(q, "yes", "alice", "bob", "dana", "carla")
    vote(q, "no", "dr-ana")
    assert finalized(q) == "accepted"


def test_a_tie_at_the_threshold_is_accepted_only_with_the_casting_members_yes():
    world.build()
    ok("governance_update", "ctl", casting_vote="dana")
    yes = file()
    vote(yes, "yes", "dana")
    vote(yes, "no", "alice")
    assert finalized(yes) == "accepted"  # one yes and one no, and Dana's is the yes
    for dana in ("no", "abstain", None):
        other = file()
        vote(other, "yes", "bob")
        vote(other, "no", "alice")
        if dana:
            vote(other, dana, "dana")
        assert finalized(other) == "rejected", dana
    raised = file(threshold=60 * PERCENT)
    vote(raised, "yes", "dana", "bob", "carla")
    vote(raised, "no", "alice", "mallory")
    assert finalized(raised) == "accepted"  # a tie at the raised threshold, 3 of 5, is decided the same way
    short = file(threshold=60 * PERCENT)
    vote(short, "yes", "dana", "bob")
    vote(short, "no", "alice", "mallory")
    assert finalized(short) == "rejected"  # 2 of 4 is below it: the casting vote decides ties, not shortfalls
    outvoted = file()
    vote(outvoted, "yes", "dana")
    vote(outvoted, "no", "alice", "bob")
    assert finalized(outvoted) == "rejected"


def test_without_a_casting_vote_a_tie_rejects():
    world.build()
    p = file()
    vote(p, "yes", "dana")
    vote(p, "no", "alice")
    assert finalized(p) == "rejected"


def test_a_casting_member_who_has_left_means_ties_reject_until_another_is_named():
    world.build()
    ok("governance_update", "ctl", casting_vote="dana")
    p = file()
    vote(p, "yes", "dana")
    vote(p, "no", "alice")
    ok("user_set_status", "ctl", user="dana", status="suspended")
    assert rule(file())[3] == "dana"  # new proposals still copy her, and her vote cannot count on them
    assert finalized(p) == "rejected"
    ok("governance_update", "ctl", casting_vote="bob")
    q = file()
    vote(q, "yes", "bob")
    vote(q, "no", "alice")
    assert finalized(q) == "accepted"


def test_the_casting_vote_decides_only_ties_over_the_votes_cast():
    world.build()
    world.person("zed")
    ok("governance_update", "ctl", casting_vote="dana")
    p = file(threshold_of="eligible")
    vote(p, "yes", "dana", "alice", "bob", "carla")  # four of the eight: exactly half, which is not more than half
    assert finalized(p) == "rejected"


def test_with_no_voting_window_a_vote_that_cannot_pass_ends_unless_a_casting_vote_may_still_decide_it():
    world.build()
    world.person("zed")
    ok("governance_update", "ctl", voting_window=0)
    p = file()
    vote(p, "no", "alice", "bob", "carla")
    vote(p, "no", "mallory")  # four of the eight say no: at best a tie remains
    assert Proposal[p].status == "rejected"
    ok("governance_update", "ctl", casting_vote="dana")
    q = file()
    vote(q, "no", "alice", "bob", "carla", "mallory")
    assert Proposal[q].status == "voting"
    vote(q, "yes", "dana", "dr-ana", "elena", "zed")
    assert Proposal[q].status == "accepted"  # four against four, and Dana said yes


def test_the_casting_vote_names_an_active_member_or_clears():
    world.build()
    for bad in ("nobody", {"Set": "dana"}, 7, ["dana"]):
        out = call("governance_update", "ctl", casting_vote=bad)
        assert not out["ok"] and out["kind"] == "invalid_argument", bad
    Member["mallory"].status = "banned"
    assert call("governance_update", "ctl", casting_vote="mallory")["kind"] == "invalid_argument"
    ok("governance_update", "ctl", casting_vote="dana")
    assert Realm["1"].manifest_data["governance"]["casting_vote"] == "dana"
    ok("governance_update", "ctl", casting_vote="")
    assert Realm["1"].manifest_data["governance"]["casting_vote"] == ""


# ---- A department's vote


def test_a_departments_vote_ends_at_its_stored_threshold():
    civil_registry(2)
    plain, raised = file("Civil Registry"), file("Civil Registry", threshold=75 * PERCENT)
    vote(plain, "yes", "bob", "alice")
    assert Proposal[plain].status == "accepted"  # m of four seats
    vote(raised, "yes", "bob", "alice")
    assert Proposal[raised].status == "voting"  # three quarters of four seats are three
    vote(raised, "yes", "dana")
    assert Proposal[raised].status == "accepted"


def test_two_of_three_seats_reach_two_thirds_rounded_down():
    civil_registry(2, n=3, voters=("alice",))
    p = file("Civil Registry")
    assert rule(p)[0] == 66_666_666 and Proposal[p].tally["eligible"] == 3
    vote(p, "yes", "bob")
    assert Proposal[p].status == "voting"
    vote(p, "yes", "alice")
    assert Proposal[p].status == "accepted"


# ---- Migration


def stored_as_version_2(proposal, **fields):
    """The row as the last release saved it: no rule fields, and a department's m in its tally."""
    db = Database.get_instance()
    row = {k: v for k, v in db.load("Proposal", Proposal[proposal]._id).items()
           if k not in ("kind", "matter", "threshold_of", "casting_vote")}
    db.save("Proposal", Proposal[proposal]._id, {**row, "__version__": 2, **fields})
    db.clear_registry()


def test_a_proposal_of_the_last_release_gets_the_rule_it_was_decided_by():
    civil_registry(2, n=3, quorum=20 * PERCENT, voters=("alice",))
    realm_wide, department = file(), file("Civil Registry")
    stored_as_version_2(realm_wide)
    stored_as_version_2(department, tally={"yes": 0, "no": 0, "abstain": 0, "required": 2, "veto": []},
                        threshold=50 * PERCENT)
    assert rule(realm_wide) == (50 * PERCENT, 0, "cast", "")
    assert rule(department) == (66_666_666, 20 * PERCENT, "eligible", "")
    assert "required" not in Proposal[department].tally
    assert Database.get_instance().load("Proposal", Proposal[department]._id)["__version__"] == 3
    vote(department, "yes", "bob")  # it was open: the seats are counted when it is next read
    assert Proposal[department].status == "voting"
    vote(department, "yes", "alice")
    assert Proposal[department].status == "accepted"
