"""A department that decides by a majority of the votes cast (ggg I-GOV-3, I-GOV-14, D31): the yes votes exceed the no
votes with abstentions counting towards the quorum only, a tie is put to a second vote and a tie in that one to the head's
casting vote, and a proposer may only tighten the rule.

Civil Registry seats dana (its head), bob, alice and carla: four voters, whatever its policy's n.
"""

import fake_chain
import pytest

import world
from api import proposals
from core import op
from core.ratio import PERCENT
from ggg import Department, Proposal, Vote
from kit import call, later, ok

DAY = 86400
WEEK = 8 * DAY  # past the voting window


def council(n=4, quorum=0, veto=(), head="dana"):
    depts = world.build()
    for voter in ("alice", "carla"):
        world.seat(f"civil-registry.{voter}", voter, depts["Civil Registry"])
    depts["Civil Registry"].head = head
    depts["Civil Registry"].policy = {"m": 1, "n": n, "quorum": quorum, "veto": list(veto), "threshold_of": "cast"}


def file(**rule):
    out = call("proposal_create", "alice", title="x", department="Civil Registry", **rule)
    return out["proposal"] if out["ok"] else out


def vote(proposal, **choices):
    for voter, choice in choices.items():
        ok("proposal_vote", voter, proposal=proposal, choice=choice)


def finalized(proposal):
    later(WEEK)
    return ok("proposal_finalize", "ctl", proposal=proposal)["status"]


def test_the_rule_is_a_majority_of_the_votes_cast_with_the_heads_casting_vote_fixed_when_the_vote_opens():
    council(n=5, quorum=40 * PERCENT)
    p = Proposal[file()]
    assert (p.threshold, p.quorum, p.threshold_of, p.casting_vote, p.majority_of_cast) == (
        50 * PERCENT, 40 * PERCENT, "cast", "dana", True)
    assert (p.tally["seats"], p.tally["eligible"]) == (5, 4)
    Department["Civil Registry"].head = "bob"
    assert Proposal[p.id].casting_vote == "dana"  # a change moves no open proposal
    assert Proposal[file()].casting_vote == "bob"
    assert Proposal[file()].majority_of_cast and not Proposal[ok("proposal_create", "alice", title="x")["proposal"]].majority_of_cast


def test_the_yes_votes_must_exceed_the_no_votes_and_abstentions_count_for_the_quorum_only():
    council(quorum=50 * PERCENT)
    p = file()
    vote(p, alice="yes", carla="yes", dana="no")
    assert Proposal[p].status == "voting"  # no early end: bob may still vote
    vote(p, bob="abstain")  # everyone has voted: 2 yes, 1 no, 1 abstention
    assert Proposal[p].status == "accepted"
    rejected = file()
    vote(rejected, alice="yes", carla="no", dana="abstain", bob="abstain")  # a tie, not a majority
    assert Proposal[rejected].status == "voting"
    again = file()
    vote(again, alice="no", carla="no", dana="yes", bob="abstain")
    assert Proposal[again].status == "rejected"


def test_a_vote_that_has_not_ended_is_decided_at_its_deadline_not_before():
    council()
    p = file()
    vote(p, alice="yes", carla="yes")
    assert "still open" in call("proposal_finalize", "ctl", proposal=p)["error"]
    assert finalized(p) == "accepted"


@pytest.mark.parametrize("n, outcome", [(8, "accepted"), (10, "rejected")])
def test_the_quorum_is_over_the_policys_n_so_a_vacancy_never_makes_the_vote_easier(n, outcome):
    council(n=n, quorum=50 * PERCENT)  # four seats are filled: all four voting is 50 % of 8, and 40 % of 10
    p = file()
    vote(p, alice="yes", carla="yes", dana="yes", bob="yes")
    assert Proposal[p].status == outcome


def test_a_tie_in_the_first_vote_is_put_to_a_second_that_opens_on_the_same_proposal_when_everyone_has_voted():
    council()
    p = file()
    later(2 * DAY)
    vote(p, alice="yes", carla="no", dana="abstain")
    assert (Proposal[p].status, Proposal[p].first_vote) == ("voting", None)  # bob may still vote
    first_opened = Proposal[p].opens_at
    vote(p, bob="abstain")
    second = Proposal[p]
    assert second.status == "voting"
    assert second.first_vote == {"yes": 1, "no": 1, "abstain": 2, "eligible": 4, "seats": 4}
    assert (second.tally["yes"], second.tally["no"], second.tally["abstain"], second.tally["eligible"]) == (0, 0, 0, 4)
    assert Vote.count() == 0 and second.opens_at == first_opened + 2 * DAY and second.deadline == second.opens_at + 7 * DAY
    assert second.casting_vote == "dana" and second.threshold == 50 * PERCENT
    assert call("proposal_vote", "alice", proposal=p, choice="yes")["ok"]  # every member votes again


def test_a_tie_at_the_deadline_is_recorded_by_finalize_as_a_second_vote():
    council()
    p = file()
    vote(p, alice="yes", carla="no")
    later(WEEK)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "voting"
    assert Proposal[p].first_vote["yes"] == 1 and Proposal[p].deadline == fake_chain.now() + 7 * DAY


@pytest.mark.parametrize("casting, others, outcome", [
    ("yes", {"carla": "no"}, "accepted"),
    ("no", {"alice": "yes"}, "rejected"),
    ("abstain", {"alice": "yes", "carla": "no"}, "rejected"),
])
def test_a_tie_in_the_second_vote_is_decided_by_the_casting_vote_only_when_it_voted_yes(casting, others, outcome):
    council()
    p = file()
    vote(p, alice="yes", carla="no", dana="abstain", bob="abstain")
    vote(p, dana=casting, **others)
    assert Proposal[p].status == "voting"  # the tie has to wait for the end of the second vote: bob may yet vote
    assert finalized(p) == outcome
    assert Proposal[p].first_vote is not None  # there is no third vote


def test_a_head_who_holds_no_seat_neither_votes_nor_casts_the_deciding_vote():
    council(head="mallory")  # a member, with no seat in Civil Registry
    p = file()
    assert Proposal[p].casting_vote == "mallory" and Proposal[p].tally["eligible"] == 4
    assert call("proposal_vote", "mallory", proposal=p, choice="yes")["kind"] == "forbidden"
    vote(p, alice="yes", carla="no", dana="abstain", bob="abstain")
    vote(p, alice="yes", carla="no")
    assert finalized(p) == "rejected"


def test_with_no_head_a_tie_in_the_second_vote_rejects():
    council(head="")
    p = file()
    vote(p, alice="yes", carla="no", dana="abstain", bob="abstain")
    vote(p, alice="yes", carla="no")
    assert Proposal[p].casting_vote == "" and finalized(p) == "rejected"


def test_the_second_vote_is_decided_like_the_first_so_one_that_misses_its_quorum_is_rejected():
    council(quorum=75 * PERCENT)
    p = file()
    vote(p, alice="yes", carla="no", dana="abstain")  # 3 of 4 voted: the quorum is met, and it ties
    assert finalized(p) == "voting"  # at its deadline: the second vote
    vote(p, alice="yes")
    assert finalized(p) == "rejected"


def test_a_veto_holders_no_rejects_a_tied_first_vote_instead_of_putting_it_to_a_second():
    council(veto=("carla",))
    p = file()
    vote(p, alice="yes", carla="no", dana="abstain", bob="abstain")
    assert (Proposal[p].status, Proposal[p].first_vote) == ("rejected", None)


def test_the_timer_of_a_first_vote_that_ended_early_leaves_the_second_vote_open():
    council()
    p = file()
    first_deadline = Proposal[p].deadline
    later(2 * DAY)
    vote(p, alice="yes", carla="no", dana="abstain", bob="abstain")  # the second vote opens, until day 9
    later(5 * DAY + 1)  # the first vote's own deadline has passed
    op.run(proposals.expire(p, first_deadline))
    assert Proposal[p].status == "voting"
    later(3 * DAY)
    op.run(proposals.expire(p, Proposal[p].deadline))
    assert Proposal[p].status == "rejected"  # the second vote ended with nobody voting: no majority


def test_a_first_vote_that_ends_after_the_effective_time_expires_the_proposal_instead_of_putting_it_to_a_second():
    council()
    p = file(effective_at=fake_chain.now() + 3 * DAY)
    vote(p, alice="yes", carla="no")
    later(WEEK)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "expired"
    assert Proposal[p].first_vote is None


def test_a_proposer_may_tighten_the_rule_and_never_loosen_it():
    council(quorum=25 * PERCENT)
    for stated in ({"threshold": 40 * PERCENT}, {"quorum": 10 * PERCENT}, {"threshold_of": "other"}):
        out = file(**stated)
        assert out["kind"] == "invalid_argument", stated
    assert Proposal.count() == 0
    stricter = Proposal[file(threshold=75 * PERCENT, quorum=50 * PERCENT)]
    assert (stricter.threshold, stricter.quorum, stricter.threshold_of, stricter.majority_of_cast) == (
        75 * PERCENT, 50 * PERCENT, "cast", True)


def test_a_stricter_threshold_has_its_own_tie_decided_as_the_default_is():
    council()
    p = file(threshold=75 * PERCENT)
    vote(p, alice="yes", dana="yes", bob="yes", carla="no")  # 3 of 4 is exactly 75 %: a tie
    assert Proposal[p].status == "voting" and Proposal[p].first_vote["yes"] == 3
    vote(p, alice="yes", dana="yes", bob="yes", carla="no")
    assert Proposal[p].status == "accepted"  # the casting member (dana) voted yes
    below = file(threshold=75 * PERCENT)
    vote(below, alice="yes", dana="yes", bob="no", carla="no")  # a half: below the threshold, no tie
    assert Proposal[below].status == "rejected"


def test_eligible_adds_an_absolute_majority_of_the_policys_n_to_the_majority_of_the_votes_cast():
    council()
    lone = file(threshold_of="eligible")
    vote(lone, alice="yes", carla="abstain", dana="abstain", bob="abstain")  # 1 yes beats 0 no, but 2 of 4 seats is half
    assert Proposal[lone].status == "rejected"
    default = file()
    vote(default, alice="yes", carla="abstain", dana="abstain", bob="abstain")
    assert Proposal[default].status == "accepted"
    enough = file(threshold_of="eligible")
    vote(enough, alice="yes", carla="yes", dana="no", bob="abstain")  # 2 of 4 reach half, and 2 yes beat 1 no
    assert Proposal[enough].status == "accepted"
    tied = file(threshold_of="eligible")
    vote(tied, alice="yes", carla="yes", dana="no", bob="no")  # a tie still goes to a second vote
    assert Proposal[tied].status == "voting" and Proposal[tied].first_vote["yes"] == 2


def test_under_eligible_a_stricter_threshold_is_a_share_of_the_policys_n_not_of_the_votes_cast():
    council()  # n = 4: three yes reach 75 % of it, and beat one no at a half of the votes cast
    p = file(threshold=75 * PERCENT, threshold_of="eligible")
    vote(p, alice="yes", carla="yes", dana="yes", bob="no")
    assert Proposal[p].status == "accepted"


def test_under_eligible_the_majority_of_the_votes_cast_stays_at_a_half_with_its_own_tie():
    council(n=2)  # four seated voters: two yes and two no tie at a half of the votes cast, whatever the share of n
    p = file(threshold=75 * PERCENT, threshold_of="eligible")
    vote(p, alice="yes", carla="yes", dana="no", bob="no")
    assert Proposal[p].status == "voting" and Proposal[p].first_vote["yes"] == 2


def test_a_policy_may_count_the_votes_cast_and_is_otherwise_m_of_n():
    world.build()
    assert "threshold_of is one of" in call("department_set_policy", "ctl", department="Civil Registry", m=1, n=2,
                                           threshold_of="other")["error"]
    ok("department_set_policy", "ctl", department="Finance", m=2, n=3)
    assert "threshold_of" not in Department["Finance"].policy
    p = Proposal[call("proposal_create", "alice", title="x", department="Finance")["proposal"]]
    assert (p.threshold_of, p.majority_of_cast, p.casting_vote) == ("eligible", False, "")
    ok("department_set_policy", "ctl", department="Civil Registry", m=1, n=3, quorum=10 * PERCENT, threshold_of="cast")
    assert Department["Civil Registry"].policy["threshold_of"] == "cast"
    assert ok("department_get", "ctl", department="Civil Registry")["department"]["policy"]["threshold_of"] == "cast"
