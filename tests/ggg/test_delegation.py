import fake_chain
import world
from core import op
from ggg import Delegation, DelegationRequest, Department, Event, PaymentAccount, Proposal, User, Vote
from kit import call, denied, later, ok, rows

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


def test_a_lapsed_pending_grant_cannot_be_accepted_and_does_not_block_a_new_one():
    world.build()
    d = ok("delegation_grant", "alice", delegate="bob", operations=["proposal.vote"], expires_in=DAY)["delegation"]
    later(DAY)
    assert "expired" in denied("delegation_accept", "bob", delegation=d)
    assert Delegation[d].status == "pending"
    d2 = ok("delegation_grant", "alice", delegate="bob", operations=["proposal.vote"], expires_in=DAY)["delegation"]
    assert (Delegation[d].status, Delegation[d].link, Delegation[d2].link) == ("expired", None, "alice|bob")
    ok("delegation_accept", "bob", delegation=d2)
    assert "already holds" in denied("delegation_grant", "alice", delegate="bob", operations=["proposal.vote"],
                                     expires_in=DAY)


def test_grants_are_listed_a_page_at_a_time():
    world.build()
    for who in ("bob", "carla", "dana"):
        ok("delegation_grant", "alice", delegate=who, operations=["proposal.vote"], expires_in=DAY)
    ok("delegation_grant", "bob", delegate="alice", operations=["proposal.vote"], expires_in=DAY)
    first = ok("delegation_list", "alice", count=2)
    assert [g["delegate"] for g in first["as_grantor"]] == ["bob", "carla"] and first["next"]["grantor"]
    assert [g["grantor"] for g in first["as_delegate"]] == ["bob"] and first["next"]["delegate"] is None
    rest = ok("delegation_list", "alice", count=2, from_grantor=first["next"]["grantor"])
    assert [g["delegate"] for g in rest["as_grantor"]] == ["dana"] and rest["next"]["grantor"] is None
    me = ok("me", "alice")["delegations"]
    assert len(me["as_grantor"]) == 3 and me["next"] == {"grantor": None, "delegate": None}


def test_expired_requests_are_cleared_and_open_ones_capped_each_way():
    world.build()
    ask = dict(operations=["proposal.vote"], expires_in=DAY)
    asking = [f"agent-{i}" for i in range(10)]
    for a in asking:
        ok("delegation_request", a, grantor="alice", **ask)
    assert "too many open requests" in denied("delegation_request", "claude-conn", grantor="alice", **ask)
    later(3601)
    ok("delegation_request", "claude-conn", grantor="alice", **ask)
    assert DelegationRequest.count() == 1
    grantors = [world.person(f"user-{i}").id for i in range(9)]
    for g in grantors:
        ok("delegation_request", "claude-conn", grantor=g, **ask)
    assert "you have too many" in denied("delegation_request", "claude-conn", grantor="bob", **ask)
    ok("delegation_request", "claude-conn", grantor="alice", **ask)
    assert DelegationRequest.count() == 10
    assert len(ok("delegation_list", "alice")["requests"]) == 1


def test_auth():
    world.build()
    for name in ("delegation_grant", "delegation_accept", "delegation_revoke", "delegation_request_decide",
                 "delegation_list", "delegable_operations"):
        assert "needs self." in denied(name, "claude-conn", delegation="D-1", request="DR-1"), name
    assert "sign in" in denied("delegation_request", fake_chain.ANONYMOUS, grantor="alice")
    assert call("delegation_list", "bob")["ok"]


@op.op("self.update_public_profile", delegable=True, governed=lambda a: Department["Infrastructure"])
def t_governed_rename(c, args):
    User[c.user].nickname = args["nickname"]


def delegate_to_claude(*permissions, grantor="alice", days=30):
    return ok("delegation_grant", grantor, delegate="claude-conn", operations=list(permissions),
              expires_in=days * DAY)["delegation"]


def infrastructure():
    """Infrastructure decides by two votes, so a delegate's governed call is filed as a proposal for Carla and Bob."""
    world.build()
    world.policy(Department["Infrastructure"], 2, 3)
    Department["Infrastructure"].head = "bob"
    world.seat("infrastructure.board", "bob", Department["Infrastructure"])  # a head votes with the seat they hold


def rename_for_carla(nickname="Cara"):
    out = ok("t_governed_rename", "claude-conn", nickname=nickname, on_behalf_of="carla")
    assert out["requires_vote"]
    return out["proposal"]


def carry(proposal):
    """Carla and Bob vote yes: the deciding vote closes the proposal and runs its action."""
    for voter in ("carla", "bob"):
        ok("proposal_vote", voter, proposal=proposal, choice="yes")
    return {"status": Proposal[proposal].status, "result": Proposal[proposal].result}


def test_payout_accounts_are_not_delegable():
    world.build()
    delegate_to_claude("self.data_manage")
    mine = ok("payment_account_add", "alice", network="ICP", address="alice-wallet", symbol="REALM")["account"]
    added = denied("payment_account_add", "claude-conn", network="ICP", address="other-wallet", symbol="REALM",
                   on_behalf_of="alice")
    removed = denied("payment_account_remove", "claude-conn", account=mine, on_behalf_of="alice")
    assert "power of attorney" in added and "power of attorney" in removed
    assert [a.address for a in rows(PaymentAccount)] == ["alice-wallet"]
    assert not {"payment_account_add", "payment_account_remove"} & {
        o["op"] for o in ok("delegable_operations", "alice")["operations"]}


def test_identity_submission_and_quarter_moves_are_not_delegable():
    world.build()
    for permission in ("identity.submit", "self.change_quarter"):
        assert "not a delegable" in denied("delegation_grant", "alice", delegate="claude-conn",
                                           operations=[permission], expires_in=DAY)
    # A grant made while they were delegable stops covering them.
    Delegation(id="D-old", grantor="alice", delegate="claude-conn", status="active", expires_at=fake_chain.now() + DAY,
               scope={"operations": ["identity.submit", "self.change_quarter"]})
    assert "power of attorney" in denied("identity_submit", "claude-conn", kind="passport", on_behalf_of="alice")
    assert "power of attorney" in denied("self_change_quarter", "claude-conn", quarter="south", on_behalf_of="alice")


def test_a_proposal_filed_under_power_of_attorney_is_a_motion():
    world.build()
    grant = delegate_to_claude("proposal.create", "proposal.vote", "self.data_view", "self.update_public_profile")
    for action in ({"op": "me", "args": {}}, {"op": "task_create", "args": {"name": "later", "steps": []}},
                   {"op": "profile_update", "args": {"nickname": "Ali"}}):
        error = denied("proposal_create", "claude-conn", title="x", action=action, on_behalf_of="alice")
        assert "motion" in error, (action["op"], error)
    assert Proposal.count() == 0
    p = ok("proposal_create", "claude-conn", title="Bike lane", on_behalf_of="alice")["proposal"]
    assert (Proposal[p].proposer, Proposal[p].on_behalf_of, Proposal[p].filed_under) == ("alice", "alice", grant)


def test_a_delegates_governed_call_is_a_proposal_that_runs_under_its_grant():
    infrastructure()
    grant = delegate_to_claude("self.update_public_profile", grantor="carla")
    p = rename_for_carla()
    assert (Proposal[p].proposer, Proposal[p].on_behalf_of, Proposal[p].filed_under) == ("carla", "carla", grant)
    assert carry(p)["status"] == "executed" and User["carla"].nickname == "Cara"


def test_a_delegates_proposal_fails_when_its_grant_was_revoked():
    infrastructure()
    grant = delegate_to_claude("self.update_public_profile", grantor="carla")
    p = rename_for_carla()
    ok("delegation_revoke", "carla", delegation=grant)
    out = carry(p)
    assert out["status"] == "failed", out
    assert out["result"]["error"] == f"the power of attorney {grant} it was filed under has been revoked or replaced"
    assert User["carla"].nickname == "Carla"


def test_a_delegates_proposal_fails_when_its_grant_was_replaced():
    infrastructure()
    grant = delegate_to_claude("self.update_public_profile", grantor="carla")
    p = rename_for_carla()
    ask = ok("delegation_request", "claude-conn", grantor="carla", operations=["self.update_public_profile"],
             expires_in=DAY)["request"]
    ok("delegation_request_decide", "carla", request=ask, approve=True)
    assert Delegation[grant].status == "revoked"
    out = carry(p)
    assert out["status"] == "failed" and "has been revoked or replaced" in out["result"]["error"], out
    assert User["carla"].nickname == "Carla"


def test_a_delegates_proposal_runs_after_its_grant_has_expired():
    infrastructure()
    grant = delegate_to_claude("self.update_public_profile", grantor="carla", days=2)
    p = rename_for_carla()
    ok("proposal_vote", "carla", proposal=p, choice="yes")
    later(3 * DAY)
    assert Delegation[grant].expires_at < fake_chain.now()
    delegate_to_claude("self.update_public_profile", grantor="carla")  # a new grant after the first lapsed
    assert ok("proposal_vote", "bob", proposal=p, choice="yes")["status"] == "executed"
    assert User["carla"].nickname == "Cara"


def test_a_delegates_proposal_fails_when_its_grant_no_longer_allows_the_call(monkeypatch):
    infrastructure()
    delegate_to_claude("self.update_public_profile", grantor="carla")
    p = rename_for_carla()
    monkeypatch.setitem(op.OPS["t_governed_rename"], "delegable", False)  # as when an upgrade ends delegating it
    out = carry(p)
    assert out["status"] == "failed" and "does not allow t_governed_rename now" in out["result"]["error"], out
    assert User["carla"].nickname == "Carla"


def test_a_delegates_proposal_fails_when_the_member_no_longer_holds_the_permission():
    infrastructure()
    delegate_to_claude("self.update_public_profile", grantor="carla")
    p = rename_for_carla()
    ok("proposal_vote", "carla", proposal=p, choice="yes")
    ok("role_revoke", "ctl", user="carla", profile="member")
    out = ok("proposal_vote", "bob", proposal=p, choice="yes")
    assert out["status"] == "failed" and "does not allow t_governed_rename now" in Proposal[p].result["error"]
    assert User["carla"].nickname == "Carla"


def test_a_delegates_proposal_fails_when_the_member_has_been_banned():
    infrastructure()
    delegate_to_claude("self.update_public_profile", grantor="carla")
    p = rename_for_carla()
    ok("proposal_vote", "carla", proposal=p, choice="yes")
    ok("user_set_status", "ctl", user="carla", status="banned")
    out = ok("proposal_vote", "bob", proposal=p, choice="yes")
    assert out["status"] == "failed" and "does not allow t_governed_rename now" in Proposal[p].result["error"]
    assert User["carla"].nickname == "Carla"


def test_a_proposal_a_delegate_filed_before_grants_were_recorded_is_not_run_unchecked():
    infrastructure()
    action = {"op": "t_governed_rename", "args": {"nickname": "Cara"}}
    p = ok("proposal_create", "carla", title="x", department="Infrastructure", action=action)["proposal"]
    Proposal[p].on_behalf_of = "carla"  # how a delegate's filing looked before the grant was recorded
    out = carry(p)
    assert out["status"] == "failed", out
    assert out["result"]["error"] == (
        "it was filed under power of attorney before this realm recorded grants; file it again")
    assert User["carla"].nickname == "Carla"


def test_a_delegate_does_not_vote_on_a_proposal_it_filed():
    world.build()
    delegate_to_claude("proposal.create", "proposal.vote")
    mine = ok("proposal_create", "claude-conn", title="Bike lane", on_behalf_of="alice")["proposal"]
    assert "filed" in denied("proposal_vote", "claude-conn", proposal=mine, choice="yes", on_behalf_of="alice")
    other = ok("proposal_create", "bob", title="Park")["proposal"]
    ok("proposal_vote", "claude-conn", proposal=other, choice="yes", on_behalf_of="alice")
    ok("proposal_vote", "alice", proposal=mine, choice="yes")
    assert Vote[f"{mine}:alice"].cast_by == "alice"


def test_a_delegate_does_not_vote_on_the_governed_call_it_filed():
    infrastructure()
    delegate_to_claude("self.update_public_profile", "proposal.vote", grantor="carla")
    p = rename_for_carla()
    assert "filed" in denied("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="carla")
    assert Proposal[p].status == "voting"


def test_a_delegate_withdraws_only_the_proposals_it_filed_under_its_grant():
    world.build()
    delegate_to_claude("proposal.create")
    hers = ok("proposal_create", "alice", title="Alice's own")["proposal"]
    claudes = ok("proposal_create", "claude-conn", title="Claude's", on_behalf_of="alice")["proposal"]
    assert "not filed under" in denied("proposal_withdraw", "claude-conn", proposal=hers, on_behalf_of="alice")
    ok("proposal_withdraw", "claude-conn", proposal=claudes, on_behalf_of="alice")
    assert (Proposal[hers].status, Proposal[claudes].status) == ("voting", "withdrawn")
    ok("proposal_withdraw", "alice", proposal=hers)
