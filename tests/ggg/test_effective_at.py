"""A proposal with an ``effective_at`` runs its action then, not when the vote closes (realms-gos#504)."""

import fake_chain
import world
from api import setup
from ggg import Mandate, Proposal, Task, TaskSchedule
from kit import call, fire_timers, later, ok

HOUR = 3600
FOUR = ("alice", "bob", "dana", "carla")  # more than half of the seven members: enough to carry a vote
ROAD_WORKS = {"op": "mandate_create", "args": {"title": "Road works"}}


def vote_yes(proposal, voters=FOUR):
    for voter in voters:
        ok("proposal_vote", voter, proposal=proposal, choice="yes")


def scheduled_proposal(effective_in=HOUR):
    """A realm-wide proposal for the road works, accepted now, to take effect ``effective_in`` seconds from now."""
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + effective_in)["proposal"]
    vote_yes(p)
    return p


def test_an_accepted_proposal_waits_for_its_effective_time_then_runs():
    world.build()
    p = scheduled_proposal()
    assert Proposal[p].status == "execution_scheduled" and Mandate.count() == 0
    assert fake_chain.state["timers"][-1][0] == HOUR
    assert "takes effect at" in call("proposal_run_scheduled", "bob", proposal=p)["error"]
    assert "no accepted action" in call("proposal_execute", "ctl", proposal=p)["error"]
    assert Mandate.count() == 0
    later(HOUR)
    fire_timers()
    assert Proposal[p].status == "executed" and Mandate.count() == 1
    assert Task[f"proposal {p}"].status == "completed"


def test_a_proposal_without_effective_at_runs_when_the_vote_closes():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS)["proposal"]
    vote_yes(p)
    assert Proposal[p].status == "executed" and Mandate.count() == 1 and Task.count() == 0


def test_the_schedule_is_armed_again_after_an_upgrade_and_runs_once():
    world.build()
    p = scheduled_proposal()
    before_upgrade = fake_chain.state["timers"]
    fake_chain.state["timers"] = []  # timers do not survive an upgrade
    later(HOUR - 60)
    setup.start()  # post_upgrade
    assert [secs for secs, _ in fake_chain.state["timers"]] == [60]
    later(60)
    fake_chain.state["timers"] += before_upgrade  # the same step due twice
    fire_timers()
    assert Proposal[p].status == "executed" and Mandate.count() == 1
    assert call("proposal_run_scheduled", "bob", proposal=p)["status"] == "executed" and Mandate.count() == 1


def test_a_schedule_missed_during_an_upgrade_runs_at_the_next_start():
    world.build()
    p = scheduled_proposal()
    fake_chain.state["timers"] = []
    later(2 * HOUR)
    setup.start()
    fire_timers()
    assert Proposal[p].status == "executed" and Mandate.count() == 1


def test_another_proposal_stops_a_scheduled_one():
    world.build()
    p = scheduled_proposal()
    stop = ok("proposal_create", "bob", title="Stop the road works",
              action={"op": "proposal_stop", "args": {"proposal": p}})["proposal"]
    vote_yes(stop)
    assert Proposal[stop].status == "executed" and Proposal[p].status == "withdrawn"
    assert Task[f"proposal {p}"].status == "cancelled" and TaskSchedule[f"proposal {p}@1"].disabled
    later(HOUR)
    fire_timers()
    assert Mandate.count() == 0 and Proposal[p].status == "withdrawn"
    assert "withdrawn" in call("proposal_stop", "ctl", proposal=p)["error"]


def test_only_a_proposal_waiting_for_its_time_can_be_stopped():
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    assert "only a proposal waiting" in call("proposal_stop", "ctl", proposal=p)["error"]


def test_a_vote_that_ends_after_effective_at_expires_and_runs_nothing():
    world.build()
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    vote_yes(p)
    assert Proposal[p].status == "voting"  # the realm's voting window is a week long
    later(8 * 86400)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "expired"
    assert Mandate.count() == 0 and Task.count() == 0
    assert "no accepted action" in call("proposal_execute", "ctl", proposal=p)["error"]


def test_a_deciding_vote_after_effective_at_expires_the_proposal():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    vote_yes(p, FOUR[:3])
    later(HOUR + 1)
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "expired"
    fire_timers()
    assert Mandate.count() == 0 and Task.count() == 0


def test_a_proposal_voted_down_stays_rejected_after_effective_at():
    world.build()
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    later(8 * 86400)
    assert ok("proposal_finalize", "ctl", proposal=p)["status"] == "rejected"


def test_the_listing_and_the_proposal_show_effective_at():
    world.build()
    p = scheduled_proposal()
    at = Proposal[p].effective_at
    assert ok("proposal_list", "alice", status="execution_scheduled")["proposals"][0]["effective_at"] == at
    assert ok("proposal_get", "alice", proposal=p)["proposal"]["effective_at"] == at
