import pytest

import fake_chain
import op
from db import Fail
from ggg import Appointment, Delegation, Event, Member, Position, User, UserProfile
from kit import call, denied, later, ok


@op.op(None, read=True, public=True)
def t_public(c, args):
    return {"who": c.user}


@op.op("proposal.vote", delegable=True)
def t_vote(c, args):
    return {"user": c.user, "by": c.by}


@op.op("proposal.manage")
def t_manage(c, args):
    return {}


@op.op("credential.sign", signing=True, delegable=True)
def t_sign(c, args):
    return {}


@op.op("dispute.accept", signing=True, parties=lambda a: {a["claimant"]})
def t_decide(c, args):
    return {}


@op.op("proposal.vote")
def t_fail(c, args):
    raise Fail("nope")


@op.op("proposal.vote")
def t_crash(c, args):
    User(id="ghost")
    raise ValueError("boom")


def member(principal, *perms):
    profile = UserProfile[principal] or UserProfile(id=principal, allowed_to=list(perms))
    user = User(id=principal)
    user.profiles = [profile]
    Member(id=principal)
    return user


def grant(grantor, delegate, *perms, expires_in=3600):
    Delegation(id=f"{grantor}>{delegate}", grantor=grantor, delegate=delegate, status="active",
               scope={"operations": list(perms)}, starts_at=0, expires_at=fake_chain.now() + expires_in)


def test_public_needs_no_sign_in():
    assert ok("t_public", fake_chain.ANONYMOUS)["who"] == fake_chain.ANONYMOUS


def test_permission_needed():
    member("alice", "proposal.vote")
    assert ok("t_vote", "alice")["user"] == "alice"
    assert "needs proposal.manage" in denied("t_manage", "alice")
    assert "sign in" in denied("t_vote", fake_chain.ANONYMOUS)


def test_controller_holds_everything():
    ok("t_manage", "ctl")


def test_power_of_attorney():
    member("alice", "proposal.vote", "credential.sign")
    grant("alice", "claude", "proposal.vote")
    out = ok("t_vote", "claude", on_behalf_of="alice")
    assert (out["user"], out["by"]) == ("alice", "claude")
    assert "power of attorney" in denied("t_sign", "claude", on_behalf_of="alice")
    assert "power of attorney" in denied("t_vote", "mallory", on_behalf_of="alice")
    later(3601)
    assert "power of attorney" in denied("t_vote", "claude", on_behalf_of="alice")
    assert Delegation["alice>claude"].status == "expired"


def test_delegation_outside_scope_or_not_delegable():
    member("alice", "proposal.vote", "proposal.manage")
    grant("alice", "claude", "proposal.manage")
    assert "cannot be done" in denied("t_manage", "claude", on_behalf_of="alice")
    assert "cannot be done" in denied("t_vote", "claude", on_behalf_of="alice")


def test_agent_never_signs():
    member("bot", "credential.sign")
    Appointment(id="1", user="bot", agent=True, position=Position(id="clinic.records"))
    assert "human" in denied("t_sign", "bot")


def test_party_cannot_decide():
    member("elena", "dispute.accept")
    assert "party" in denied("t_decide", "elena", claimant="elena")
    ok("t_decide", "elena", claimant="bob")


def test_banned_member_can_only_read():
    member("mallory", "proposal.vote")
    Member["mallory"].status = "banned"
    denied("t_vote", "mallory")
    ok("t_public", "mallory")


def test_refusal_and_trap():
    member("alice", "proposal.vote")
    assert call("t_fail", "alice") == {"ok": False, "error": "nope"}
    with pytest.raises(fake_chain.Trap):
        call("t_crash", "alice")


def test_updates_are_audited():
    member("alice", "proposal.vote")
    grant("alice", "claude", "proposal.vote")
    ok("t_vote", "claude", on_behalf_of="alice", proposal="P-7")
    e = Event[str(Event.max_id())]
    assert (e.op, e.user, e.by, e.args) == ("t_vote", "alice", "claude", {"proposal": "P-7"})
    ok("t_public", "alice")
    assert Event[str(Event.max_id())].op == "t_vote"
