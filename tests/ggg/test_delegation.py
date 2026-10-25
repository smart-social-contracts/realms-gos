import fake_chain
import world
from ggg import Delegation, DelegationRequest, Event, Vote
from kit import call, denied, later, ok

DAY = 86400


def test_story_claude_and_bob_act_for_alice():
    world.build()
    r = ok("delegation_request", "claude-conn", grantor="alice", operations=["proposal.vote", "proposal.create"],
           expires_in=DAY, label="Claude")["request"]
    assert ok("delegation_list", "alice")["requests"][0]["delegate"] == "claude-conn"
    out = ok("delegation_request_decide", "alice", request=r, approve=True, operations=["proposal.vote"])
    assert out["status"] == "active" and DelegationRequest.count() == 0
    p = ok("proposal_create", "alice", title="Bike lane on Main St")["proposal"]
    ok("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="alice")
    assert Vote[f"{p}:alice"].cast_by == "claude-conn"
    denied("proposal_create", "claude-conn", title="x", on_behalf_of="alice")
    d = ok("delegation_grant", "alice", delegate="bob", operations=["proposal.vote"], expires_in=14 * DAY)
    assert d["status"] == "pending"
    denied("proposal_vote", "bob", proposal=p, choice="no", on_behalf_of="alice")
    ok("delegation_accept", "bob", delegation=d["delegation"])
    ok("proposal_vote", "bob", proposal=p, choice="no", on_behalf_of="alice")
    ok("delegation_revoke", "alice", delegation=d["delegation"])
    denied("proposal_vote", "bob", proposal=p, choice="yes", on_behalf_of="alice")
    ops = {o["op"]: o["permission"] for o in ok("delegable_operations", "alice")["operations"]}
    assert ops["proposal_vote"] == "proposal.vote" and "identity_review" not in ops and "user_add" not in ops
    assert {g["status"] for g in ok("delegation_list", "alice")["as_grantor"]} == {"active", "revoked"}


def test_I_ID_6_what_a_delegation_never_covers():
    world.build()
    grant = dict(delegate="claude-conn", expires_in=DAY)
    assert "never" in denied("delegation_grant", "ctl", operations=["all"], **grant)
    assert "never" in denied("delegation_grant", "ctl", operations=["identity.review"], **grant)
    assert "not a delegable" in denied("delegation_grant", "dana", operations=["invite.manage"], **grant)
    world.person("mallory").profiles = [world.profile("delegator", "self.delegate")]
    assert "does not hold" in denied("delegation_grant", "mallory", operations=["proposal.vote"], **grant)
    assert "one year" in denied("delegation_grant", "alice", operations=["proposal.vote"], delegate="claude-conn",
                                expires_in=366 * DAY)
    assert "never" in denied("delegation_request", "claude-conn", grantor="dana", operations=["identity.review"],
                             expires_in=DAY)
    ok("delegation_grant", "alice", operations=["proposal.vote"], **grant)
    p = ok("proposal_create", "alice", title="x")["proposal"]
    ok("role_revoke", "ctl", user="alice", profile="member")
    assert "power of attorney" in denied("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="alice")


def test_I_ID_7_only_inside_the_window_and_while_active():
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    start = fake_chain.now() + DAY
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["proposal.vote"], starts_at=start,
       expires_at=start + DAY)
    vote = dict(proposal=p, choice="yes", on_behalf_of="alice")
    denied("proposal_vote", "claude-conn", **vote)
    later(DAY)
    ok("proposal_vote", "claude-conn", **vote)
    later(DAY)
    denied("proposal_vote", "claude-conn", **vote)
    assert ok("delegation_list", "alice")["as_grantor"][0]["status"] == "expired"


def test_I_ID_8_delegated_acts_record_both():
    world.build()
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["self.update_public_profile"], expires_in=DAY)
    ok("profile_update", "claude-conn", nickname="Ali", on_behalf_of="alice")
    e = Event[str(Event.max_id())]
    assert (e.op, e.user, e.by) == ("profile_update", "alice", "claude-conn")


def test_request_decisions():
    world.build()
    ask = dict(grantor="alice", operations=["proposal.vote"], expires_in=7 * DAY)
    r = ok("delegation_request", "claude-conn", **ask)["request"]
    r2 = ok("delegation_request", "claude-conn", **ask)["request"]
    assert DelegationRequest.count() == 1 and DelegationRequest[r] is None
    assert "not an open request" in denied("delegation_request_decide", "bob", request=r2, approve=True)
    assert "not asked" in denied("delegation_request_decide", "alice", request=r2, approve=True,
                                 operations=["proposal.create"])
    assert "earlier" in denied("delegation_request_decide", "alice", request=r2, approve=True,
                               expires_at=fake_chain.now() + 8 * DAY)
    assert ok("delegation_request_decide", "alice", request=r2, approve=False)["status"] == "declined"
    assert DelegationRequest.count() == 0 and Delegation.count() == 0
    r3 = ok("delegation_request", "claude-conn", **ask)["request"]
    later(3601)
    assert "not an open request" in denied("delegation_request_decide", "alice", request=r3, approve=True)
    assert "yourself" in denied("delegation_request", "alice", **ask)


def test_grant_rules_and_who_may_accept_or_revoke():
    world.build()
    d = ok("delegation_grant", "alice", delegate="bob", operations=["proposal.vote"], expires_in=DAY)["delegation"]
    assert "already holds" in denied("delegation_grant", "alice", delegate="bob", operations=["proposal.vote"],
                                     expires_in=DAY)
    assert "yourself" in denied("delegation_grant", "alice", delegate="alice", operations=["proposal.vote"],
                                expires_in=DAY)
    assert "expires_at" in denied("delegation_grant", "alice", delegate="carla", operations=["proposal.vote"])
    assert "not a pending grant" in denied("delegation_accept", "mallory", delegation=d)
    assert "not yours" in denied("delegation_revoke", "mallory", delegation=d)
    assert ok("delegation_revoke", "bob", delegation=d)["status"] == "declined"
    assert "already declined" in denied("delegation_revoke", "alice", delegation=d)


def test_auth():
    world.build()
    for name in ("delegation_grant", "delegation_accept", "delegation_revoke", "delegation_request_decide",
                 "delegation_list", "delegable_operations"):
        assert "needs self." in denied(name, "claude-conn", delegation="D-1", request="DR-1"), name
    assert "sign in" in denied("delegation_request", fake_chain.ANONYMOUS, grantor="alice")
    assert call("delegation_list", "bob")["ok"]
