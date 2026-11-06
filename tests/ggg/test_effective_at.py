"""A proposal with an ``effective_at`` runs its action then, not when the vote closes (realms-gos#504)."""

import fake_chain
import ggg_api
import ggg_did
import pytest
import world
from api import setup
from ggg import Mandate, Proposal, Task, TaskSchedule
from kit import call, fire_timers, later, ok

HOUR = 3600
NS = 1_000_000_000
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


@pytest.mark.parametrize("effective_at", [1_790_003_600.5, "1790003600", True, [1_790_003_600]],
                         ids=["a fraction", "a string", "a bool", "a list"])
def test_effective_at_is_whole_seconds_or_the_proposal_is_refused(effective_at):
    world.build()
    out = call("proposal_create", "alice", title="Road works", action=ROAD_WORKS, effective_at=effective_at)
    assert not out["ok"] and out["kind"] == "invalid_argument" and "whole seconds" in out["error"]
    assert Proposal.count() == 0


def test_a_task_that_already_has_the_name_stops_the_proposal_from_being_accepted():
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS,
           effective_at=fake_chain.now() + HOUR)["proposal"]
    Task(name=f"proposal {p}", metadata='{"creator": "somebody"}', status="pending")  # as no operation can make it
    vote_yes(p, FOUR[:3])
    assert call("proposal_vote", "carla", proposal=p, choice="yes")["kind"] == "conflict"
    assert Proposal[p].status == "voting" and TaskSchedule.count() == 0
    assert "no accepted action" in call("proposal_execute", "ctl", proposal=p)["error"]
    assert Mandate.count() == 0


def test_a_stop_passes_even_when_the_task_is_gone():
    world.build()
    p = scheduled_proposal()
    Task[f"proposal {p}"].delete()
    assert ok("proposal_stop", "ctl", proposal=p)["status"] == "withdrawn"
    assert Proposal[p].status == "withdrawn"


def test_nobody_creates_a_task_named_like_the_realms_own():
    world.build()
    scheduled_proposal()
    steps = [{"op": "notification_send", "args": {"recipient": "Department:Finance", "title": "x"}}]
    for name in ("proposal P-1", "proposal P-99", "proposal "):
        assert "realm's own" in call("task_create", "ctl", name=name, steps=steps)["error"]
    assert Task["proposal P-99"] is None


@pytest.mark.parametrize("name, args", [
    ("task_edit", {"steps": []}),
    ("task_cancel", {}),
    ("task_delete", {}),
    ("task_run", {}),
    ("task_schedule", {"run_at": 1}),
], ids=lambda value: value if isinstance(value, str) else "")
def test_nobody_changes_the_realms_own_task_not_even_an_admin(name, args):
    world.build()
    p = scheduled_proposal()
    out = call(name, "ctl", task=f"proposal {p}", **args)
    assert not out["ok"] and out["kind"] == "forbidden" and "realm's own" in out["error"]
    assert Task[f"proposal {p}"].status == "pending" and not TaskSchedule[f"proposal {p}@1"].disabled
    later(HOUR)
    fire_timers()
    assert Proposal[p].status == "executed" and Mandate.count() == 1


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


def test_a_time_with_a_fraction_of_a_second_is_rounded_up_so_it_never_runs_early(monkeypatch):
    # The pinned ggg.did has no effective_at yet: give the schema the field as ggg#6 does.
    record = dict(ggg_did.SCHEMA["ProposalCreateArgs"][1], effective_at="opt Timestamp")
    monkeypatch.setitem(ggg_did.SCHEMA, "ProposalCreateArgs", ("record", record))
    world.build()
    ok("governance_update", "ctl", voting_window=0)
    at = fake_chain.now() + HOUR

    def seconds(ns):
        args = ggg_api.to_realms({"title": "x", "description": "", "effective_at": ns}, "ProposalCreateArgs")
        return args["effective_at"]

    assert (seconds(at * NS), seconds(at * NS + 1), seconds(at * NS + NS // 2)) == (at, at + 1, at + 1)
    half_past = seconds(at * NS + NS // 2)
    p = ok("proposal_create", "alice", title="Road works", action=ROAD_WORKS, effective_at=half_past)["proposal"]
    vote_yes(p)
    later(HOUR)  # the second the time falls in
    assert "takes effect at" in call("proposal_run_scheduled", "bob", proposal=p)["error"] and Mandate.count() == 0
    later(1)
    assert ok("proposal_run_scheduled", "bob", proposal=p)["status"] == "executed" and Mandate.count() == 1


def test_other_times_are_still_rounded_down():
    """A time something must not outlast, an expiry, would be stretched by a second if rounded up."""
    assert ggg_api.to_realms({"expires_at": 5 * NS + NS // 2}, "Invite")["expires_at"] == 5


def test_the_listing_and_the_proposal_show_effective_at():
    world.build()
    p = scheduled_proposal()
    at = Proposal[p].effective_at
    assert ok("proposal_list", "alice", status="execution_scheduled")["proposals"][0]["effective_at"] == at
    assert ok("proposal_get", "alice", proposal=p)["proposal"]["effective_at"] == at
