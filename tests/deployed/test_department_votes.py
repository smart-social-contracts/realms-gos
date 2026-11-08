"""A department's vote on the real wasm (ggg I-GOV-7, realms-gos#524): a veto holder who has not voted keeps it open,
a no can be replaced when the approvals are out of reach, and the policy's quorum applies.

It uses the realm's voting window and its deadline timers, which plain Python fakes.
"""

import pytest
from pic import Canister

PERCENT = 1_000_000
WEEK = 7 * 86400


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm and two departments of four seats each, held by Alice, Bob, Mallory and the founder: the
    Council decides 2 of 4 and the founder holds a veto; the Board decides 1 of 4 with a quorum of 50 %."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder = people["ctl"], people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    holders = [people[name] for name in ("alice", "bob", "mallory")] + [founder]
    for member in holders[:-1]:
        ok(realm, "realm_join", member)
    for department, policy in (("Council", {"m": 2, "n": 4, "veto": [founder]}),
                               ("Board", {"m": 1, "n": 4, "quorum": 50 * PERCENT})):
        ok(realm, "department_create", ctl, department=department)
        seat = f"{department.lower()}.member"
        ok(realm, "position_create", ctl, department=department, position=seat, headcount=4)
        for member in holders:
            ok(realm, "appoint", ctl, position=seat, user=member)
        ok(realm, "department_set_policy", ctl, department=department, **policy)  # last: it ends the direct policy
    return realm


def file(realm, people, department):
    return ok(realm, "proposal_create", people["alice"], title="x", department=department)["proposal"]


def vote(realm, people, proposal, who, choice):
    ok(realm, "proposal_vote", people[who], proposal=proposal, choice=choice)


def status(realm, people, proposal):
    return ok(realm, "proposal_get", people["alice"], query=True, proposal=proposal)["proposal"]["status"]


def test_a_veto_holder_who_has_not_voted_keeps_the_vote_open_and_a_vetoing_no_rejects_it_at_the_deadline(realm, people):
    accepted = file(realm, people, "Council")
    vote(realm, people, accepted, "alice", "yes")
    vote(realm, people, accepted, "bob", "yes")
    assert status(realm, people, accepted) == "voting"  # two approvals, but the founder has not voted
    vote(realm, people, accepted, "founder", "abstain")
    assert status(realm, people, accepted) == "accepted"  # Mallory has not voted: it ended at the veto holder's vote

    vetoed = file(realm, people, "Council")
    vote(realm, people, vetoed, "founder", "no")
    vote(realm, people, vetoed, "alice", "yes")
    vote(realm, people, vetoed, "bob", "yes")
    assert status(realm, people, vetoed) == "voting"  # every veto holder has voted, but against
    realm.wait(WEEK + 3600)
    assert status(realm, people, vetoed) == "rejected"  # the deadline timer closed it


def test_a_no_can_be_replaced_after_the_approvals_are_out_of_reach(realm, people):
    p = file(realm, people, "Council")
    for who in ("alice", "bob", "mallory"):
        vote(realm, people, p, who, "no")  # one voter is left: two approvals can no longer be had
    assert status(realm, people, p) == "voting"
    vote(realm, people, p, "alice", "yes")
    vote(realm, people, p, "bob", "yes")
    assert status(realm, people, p) == "voting"  # the founder, a veto holder, is the one left
    vote(realm, people, p, "founder", "yes")
    assert status(realm, people, p) == "accepted"


def test_the_quorum_of_a_departments_policy_applies(realm, people):
    p = file(realm, people, "Board")
    vote(realm, people, p, "alice", "yes")
    assert status(realm, people, p) == "voting"  # one approval is enough, a quarter of the voters is not
    vote(realm, people, p, "bob", "abstain")
    assert status(realm, people, p) == "accepted"

    short = file(realm, people, "Board")
    vote(realm, people, short, "alice", "yes")
    realm.wait(WEEK + 3600)
    assert status(realm, people, short) == "rejected"
