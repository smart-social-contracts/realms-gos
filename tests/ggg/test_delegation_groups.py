"""Power of attorney by groups (phase 3): what a grant that names groups reaches, the rules of phase 0 under it, the
member's notices and the record of what a delegate did. Grants that name permissions are in test_delegation.py."""
import json
import pathlib
import re

import pytest

import fake_chain
import ggg_api
import ggg_delegation
import world
from core import op, poa
from ggg import Delegation, Event, Notification, Proposal, Transfer
from kit import call, denied, later, ok
from test_treasury import allocate, ledger_ok, receive, setup_finance

DAY = 86400
ROOT = pathlib.Path(__file__).resolve().parents[2]
NAMES = sorted(n for n in op.ops() if not n.startswith("t_"))


def grant(*groups, grantor="alice", delegate="claude-conn", days=30, **extra):
    return ok("delegation_grant", grantor, delegate=delegate, groups=list(groups), expires_in=days * DAY,
              **extra)["delegation"]


def as_alice(name, who="claude-conn", **args):
    return call(name, who, on_behalf_of="alice", **args)


def reached(out):
    """The call got past the permission check: it ran, or failed for what it was given."""
    return not out.get("denied")


def notices(member="alice"):
    return [n for n in ok("notification_list", member)["notifications"] if n["topic"] == poa.TOPIC]


# ---- What a grant by groups reaches


def test_a_group_grant_covers_its_groups_enabled_operations_and_nothing_else():
    world.build()
    grant("vote")
    p = ok("proposal_create", "bob", title="Park")["proposal"]
    ok("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="alice")
    assert "does not cover" in denied("profile_update", "claude-conn", nickname="Ali", on_behalf_of="alice")
    assert "does not cover" in denied("proposal_create", "claude-conn", title="x", on_behalf_of="alice")


def test_an_operation_of_the_group_that_is_not_enabled_yet_is_refused():
    world.build()
    grant("see")
    assert ok("me", "claude-conn", on_behalf_of="alice")["user"]["id"] == "alice"
    # proposal_list is a direct operation of the see group; a later phase switches it on.
    assert poa.row("proposal_list")[:2] == ("direct", "see") and "proposal_list" not in poa.ENABLED
    assert "does not cover" in denied("proposal_list", "claude-conn", on_behalf_of="alice")


def test_everything_covers_what_the_member_gains_later():
    depts = world.build()
    grant("*")
    assert "may not do it now" in denied("transfer_create", "claude-conn", on_behalf_of="alice")
    world.seat("infrastructure.treasurer", "alice", depts["Infrastructure"], "transfer.create")
    out = as_alice("transfer_create")  # past the check: it fails for the arguments it was not given
    assert reached(out) and "missing" in out["error"]


def test_everything_never_covers_a_members_own_or_the_founders_acts(monkeypatch):
    world.build()
    ok("delegation_grant", "ctl", delegate="claude-conn", groups=["*"], expires_in=DAY)
    for name in ("delegation_revoke_all", "setup_launch"):
        assert not reached(call(name, "claude-conn", on_behalf_of="ctl")), name
    # Not even once a later phase has enabled them.
    monkeypatch.setattr(poa, "ENABLED", poa.ENABLED | {"delegation_revoke_all", "setup_launch"})
    for name in ("delegation_revoke_all", "setup_launch"):
        assert "does not cover" in denied(name, "claude-conn", on_behalf_of="ctl")


def without_argument_checks(monkeypatch, name):
    """The operation's spec, with the checks that read its arguments off: these tests call it with none, to see
    whether the grant reaches it."""
    spec = op.OPS[name]
    monkeypatch.setitem(spec, "parties", None)
    monkeypatch.setitem(spec, "perm_for", None)
    return spec


@pytest.mark.parametrize("name", NAMES)
def test_an_old_grant_covers_exactly_the_legacy_operations(name, monkeypatch):
    world.build()
    world.person("ctl")  # a controller holds every permission
    spec = without_argument_checks(monkeypatch, name)
    Delegation(id="D-old", grantor="ctl", delegate="claude-conn", status="active", expires_at=fake_chain.now() + DAY,
               scope={"operations": [spec["perm"] or ""]})
    assert reached(call(name, "claude-conn", on_behalf_of="ctl")) == (name in poa.LEGACY)


@pytest.mark.parametrize("name", NAMES)
def test_everything_covers_exactly_the_enabled_operations(name, monkeypatch):
    world.build()
    world.person("ctl")
    without_argument_checks(monkeypatch, name)
    ok("delegation_grant", "ctl", delegate="claude-conn", groups=["*"], expires_in=DAY)
    assert reached(call(name, "claude-conn", on_behalf_of="ctl")) == (name in poa.ENABLED)


def test_the_legacy_operations_are_those_the_model_lists_as_delegable_before_groups():
    file = (ROOT / "ggg" / "model" / "README.md").read_text()
    listed = file.split("A grant whose `scope` names permissions covers only the operations")[1].split("Under such a grant")[0]
    names = {n for n in re.findall(r"`([a-z_]+)`", listed) if n in ggg_delegation.OPERATIONS}
    assert poa.LEGACY == names and poa.ENABLED == poa.LEGACY <= set(op.OPS)


# ---- What a grant cannot say


def test_grants_that_cannot_be_made():
    depts = world.build()
    base = dict(delegate="claude-conn", expires_in=DAY)
    assert "not both" in denied("delegation_grant", "alice", groups=["vote"], operations=["proposal.vote"], **base)
    assert "name at least one" in denied("delegation_grant", "alice", **base)
    assert "not a group" in denied("delegation_grant", "alice", groups=["vote", "nonsense"], **base)
    assert "not a group" in denied("delegation_grant", "alice", groups=["office:Health"], **base)
    assert "cannot be granted yet" in denied("delegation_grant", "alice", groups=["payout"], **base)
    assert "nothing in money" in denied("delegation_grant", "alice", groups=["money"], **base)
    assert "one year" in denied("delegation_grant", "alice", groups=["vote"], delegate="claude-conn",
                                expires_in=366 * DAY)
    assert "90 days" in denied("delegation_grant", "alice", groups=["*"], delegate="claude-conn",
                               expires_in=91 * DAY)
    world.grant(depts["Infrastructure"], "transfer.create")
    assert "90 days" in denied("delegation_grant", "carla", groups=["money"], delegate="claude-conn",
                               expires_in=91 * DAY)
    assert "90 days" in denied("delegation_grant", "carla", groups=["vote", "money"], delegate="claude-conn",
                               expires_in=91 * DAY)
    ok("delegation_grant", "carla", groups=["vote", "money"], delegate="claude-conn", expires_in=90 * DAY)
    assert Delegation.count() == 1


def test_a_limit_stricter_than_the_grant_binds_it_at_use(monkeypatch):
    world.build()
    grant("vote", days=60)
    monkeypatch.setitem(poa.GROUPS, "vote", (*poa.GROUPS["vote"][:3], 30 * DAY))
    later(29 * DAY)
    p = ok("proposal_create", "bob", title="Park")["proposal"]
    ok("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of="alice")
    later(2 * DAY)
    q = ok("proposal_create", "bob", title="Road")["proposal"]
    assert "does not cover" in denied("proposal_vote", "claude-conn", proposal=q, choice="no", on_behalf_of="alice")


def test_everything_is_cut_to_90_days_at_use_whatever_the_group_allows(monkeypatch):
    world.build()
    grant("*", days=90)
    p = ok("proposal_create", "bob", title="Park")["proposal"]
    monkeypatch.setitem(poa.EVERYTHING, "longest", 30 * DAY)
    later(31 * DAY)
    assert "does not cover" in denied("proposal_vote", "claude-conn", proposal=p, choice="no", on_behalf_of="alice")


def test_requests_and_decisions_by_group():
    world.build()
    ask = dict(grantor="alice", expires_in=7 * DAY)
    assert "cannot be granted yet" in denied("delegation_request", "claude-conn", groups=["payout"], **ask)
    assert "not a group" in denied("delegation_request", "claude-conn", groups=["nonsense"], **ask)
    assert "90 days" in denied("delegation_request", "claude-conn", groups=["*"], grantor="alice", expires_in=91 * DAY)
    r = ok("delegation_request", "claude-conn", groups=["vote", "propose"], **ask)["request"]
    decide = dict(request=r, approve=True)
    assert "not asked for: profile" in denied("delegation_request_decide", "alice", groups=["profile"], **decide)
    assert "request names groups" in denied("delegation_request_decide", "alice", operations=["proposal.vote"], **decide)
    out = ok("delegation_request_decide", "alice", request=r, approve=True, groups=["vote"])
    assert Delegation[out["delegation"]].scope == {"groups": ["vote"]}
    everything = ok("delegation_request", "claude-conn", groups=["*"], **ask)["request"]
    out = ok("delegation_request_decide", "alice", request=everything, approve=True, groups=["vote", "propose"])
    assert Delegation[out["delegation"]].scope == {"groups": ["vote", "propose"]}


def test_a_human_delegate_accepts_a_grant_by_groups():
    world.build()
    d = grant("vote", delegate="bob")
    p = ok("proposal_create", "carla", title="Park")["proposal"]
    assert Delegation[d].status == "pending"
    vote = dict(proposal=p, choice="yes", on_behalf_of="alice")
    assert "no active power of attorney" in denied("proposal_vote", "bob", **vote)
    ok("delegation_accept", "bob", delegation=d)
    ok("proposal_vote", "bob", **vote)


# ---- The rules of phase 0, under a grant by groups


def test_a_proposal_under_a_group_grant_is_a_motion():
    world.build()
    g = grant("propose")
    for action in ({"op": "me", "args": {}}, {"op": "task_create", "args": {"name": "later", "steps": []}}):
        error = denied("proposal_create", "claude-conn", title="x", action=action, on_behalf_of="alice")
        assert "asks the member each time" in error
    assert Proposal.count() == 0
    p = ok("proposal_create", "claude-conn", title="Bike lane", on_behalf_of="alice")["proposal"]
    assert (Proposal[p].proposer, Proposal[p].filed_under) == ("alice", g)


def finance_for_carla():
    depts = setup_finance()
    allocate(ROADS=100)
    receive(1_000)
    world.grant(depts["Infrastructure"], "transfer.create")
    ledger_ok()
    return depts


def file_transfer(delegate="claude-conn"):
    out = ok("transfer_create", delegate, fund="ROADS", to="buildco", amount=10, on_behalf_of="carla")
    assert out["requires_vote"] and Transfer.count() == 0
    return out["proposal"]


def test_a_transfer_under_a_group_grant_is_always_a_proposal_that_runs_under_its_grant():
    finance_for_carla()
    g = grant("money", grantor="carla")
    p = file_transfer()
    assert Proposal[p].filed_under == g
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "executed"
    assert Transfer.count() == 1


def test_a_transfer_filed_under_a_group_grant_fails_once_the_grant_is_revoked():
    finance_for_carla()
    g = grant("money", grantor="carla")
    p = file_transfer()
    ok("delegation_revoke", "carla", delegation=g)
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "failed"
    assert "revoked or replaced" in Proposal[p].result["error"] and Transfer.count() == 0


def test_a_transfer_filed_under_a_group_grant_fails_when_a_limit_has_tightened(monkeypatch):
    finance_for_carla()
    grant("money", grantor="carla")
    p = file_transfer()
    monkeypatch.setitem(poa.GROUPS, "money", (*poa.GROUPS["money"][:3], DAY))
    later(2 * DAY)
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "failed"
    assert "does not allow transfer_create now" in Proposal[p].result["error"]


def test_payout_identity_quarter_moves_and_federal_votes_are_refused_under_everything():
    world.build()
    grant("*")
    for name, args in (("payment_account_add", dict(network="ICP", address="other", symbol="REALM")),
                       ("identity_submit", dict(kind="passport")), ("self_change_quarter", dict(quarter="south")),
                       ("federal_vote_propose", dict(title="x", action={"op": "me", "args": {}}))):
        assert "does not cover" in denied(name, "claude-conn", on_behalf_of="alice", **args), name


# ---- I-ID-13


def test_I_ID_13_no_delegate_of_a_member_votes_on_what_another_delegate_of_the_member_filed():
    world.build()
    first = grant("propose", "vote", delegate="agent-1")
    grant("vote", delegate="agent-2")
    p = ok("proposal_create", "agent-1", title="Bike lane", on_behalf_of="alice")["proposal"]
    vote = dict(proposal=p, choice="yes", on_behalf_of="alice")
    assert "filed by a delegate of alice" in denied("proposal_vote", "agent-2", **vote)
    assert "filed by agent-1" in denied("proposal_vote", "agent-1", **vote)
    ok("delegation_revoke", "alice", delegation=first)  # whatever that grant's status now
    assert "filed by a delegate of alice" in denied("proposal_vote", "agent-2", **vote)
    ok("delegation_grant", "alice", delegate="agent-3", groups=["propose"], expires_in=DAY)
    q = ok("proposal_create", "agent-3", title="Park", on_behalf_of="alice")["proposal"]
    later(2 * DAY)  # ... or has expired
    assert "filed by a delegate of alice" in denied("proposal_vote", "agent-2", proposal=q, choice="yes",
                                                    on_behalf_of="alice")
    ok("proposal_vote", "alice", proposal=p, choice="yes")


def lend(style, grantor, perms, groups, delegate="claude-conn"):
    """A grant to ``delegate``, naming permissions (as before groups) or groups."""
    scope = {"operations": perms} if style == "permissions" else {"groups": groups}
    return ok("delegation_grant", grantor, delegate=delegate, expires_in=30 * DAY, **scope)["delegation"]


@pytest.mark.parametrize("style", ("permissions", "groups"))
def test_I_ID_10_a_delegate_that_files_for_one_member_does_not_vote_on_it_for_another(style):
    world.build()
    lend(style, "alice", ["proposal.create", "proposal.vote"], ["propose", "vote"])
    lend(style, "bob", ["proposal.vote"], ["vote"])
    p = ok("proposal_create", "claude-conn", title="Bike lane", on_behalf_of="alice")["proposal"]
    for member in ("alice", "bob"):
        error = denied("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of=member)
        assert "filed by claude-conn" in error, (member, error)
    ok("proposal_vote", "bob", proposal=p, choice="yes")


@pytest.mark.parametrize("style", ("permissions", "groups"))
def test_I_ID_10_a_delegate_that_files_a_transfer_for_one_member_does_not_vote_on_it_for_another(style):
    depts = finance_for_carla()
    world.policy(depts["Infrastructure"], 2, 3)
    depts["Infrastructure"].head = "bob"
    lend(style, "carla", ["transfer.create", "proposal.vote"], ["money", "vote"])
    lend(style, "bob", ["proposal.vote"], ["vote"])
    p = file_transfer()
    for member in ("carla", "bob"):
        error = denied("proposal_vote", "claude-conn", proposal=p, choice="yes", on_behalf_of=member)
        assert "filed by claude-conn" in error, (member, error)
    assert Proposal[p].status == "voting" and Transfer.count() == 0
    ok("proposal_vote", "bob", proposal=p, choice="yes")
    assert ok("proposal_vote", "carla", proposal=p, choice="yes")["status"] == "executed"


def test_I_ID_13_a_delegate_of_another_member_votes_and_the_member_does():
    world.build()
    grant("propose", delegate="agent-1")
    p = ok("proposal_create", "agent-1", title="Bike lane", on_behalf_of="alice")["proposal"]
    grant("vote", grantor="bob", delegate="agent-2")
    ok("proposal_vote", "agent-2", proposal=p, choice="yes", on_behalf_of="bob")
    ok("proposal_vote", "alice", proposal=p, choice="no")


# ---- Notices


def test_a_delegates_acts_within_an_hour_share_one_notice():
    world.build()
    grant("profile", "propose", label="Claude")

    def as_alice_ok(name, **args):
        return ok(name, "claude-conn", on_behalf_of="alice", **args)

    as_alice_ok("profile_update", nickname="Ali")
    [first] = notices()
    assert (first["title"], first["href"], first["sender"]) == ("Claude did profile_update", "/delegations", "claude-conn")
    p = as_alice_ok("proposal_create", title="Bike lane")["proposal"]
    [shared] = notices()
    assert shared["id"] == first["id"] and shared["title"] == "Claude did 2 things"
    assert shared["body"].split("\n") == ["profile_update", f"proposal_create {p}"] and shared["href"] == "/delegations"
    ok("notification_read", "alice", notification=shared["id"])
    as_alice_ok("profile_update", nickname="Alie")
    [again] = notices()
    assert again["id"] == first["id"] and again["title"] == "Claude did 3 things" and not again["read"]
    later(3601)
    as_alice_ok("profile_update", nickname="Ali")
    assert [n["title"] for n in notices()] == ["Claude did 3 things", "Claude did profile_update"]


def test_a_notice_links_the_proposal_and_keeps_the_last_ten_acts():
    world.build()
    grant("propose")
    p = ok("proposal_create", "claude-conn", title="Bike lane", on_behalf_of="alice")["proposal"]
    [one] = notices()
    assert (one["title"], one["href"]) == (f"claude-conn did proposal_create {p}", f"/proposal/{p}")
    for i in range(11):
        ok("proposal_create", "claude-conn", title=f"Motion {i}", on_behalf_of="alice")
    [many] = notices()
    assert many["title"] == "claude-conn did 12 things" and len(many["body"].split("\n")) == poa.NOTICE_ACTS


def test_each_grant_has_its_own_notice_and_reads_leave_none():
    world.build()
    grant("see", "profile")
    grant("profile", delegate="agent-2")
    ok("me", "claude-conn", on_behalf_of="alice")
    assert Notification.count() == 0
    ok("profile_update", "claude-conn", nickname="Ali", on_behalf_of="alice")
    ok("profile_update", "agent-2", nickname="Alie", on_behalf_of="alice")
    assert sorted(n["title"] for n in notices()) == ["agent-2 did profile_update", "claude-conn did profile_update"]


def test_a_delegate_cannot_mark_the_notices_read_and_the_member_can():
    world.build()
    grant("profile")
    ok("profile_update", "claude-conn", nickname="Ali", on_behalf_of="alice")
    [n] = notices()
    assert "only you mark it read" in denied("notification_read", "claude-conn", notification=n["id"],
                                             on_behalf_of="alice")
    assert ok("notification_read", "claude-conn", all=True, on_behalf_of="alice")["read"] == 0
    assert not notices()[0]["read"]
    assert Notification.count() == 1  # clearing the inbox leaves no notice of its own
    assert ok("notification_read", "alice", notification=n["id"])["read"] == 1
    assert notices()[0]["read"]


# ---- The record


def test_a_delegated_act_names_its_grant_in_the_audit_log():
    world.build()
    g = grant("profile")
    ok("profile_update", "alice", nickname="Alie")
    own = Event[str(Event.max_id())]
    ok("profile_update", "claude-conn", nickname="Ali", on_behalf_of="alice")
    delegated = Event[str(Event.max_id())]
    assert (own.grant, delegated.grant, delegated.by, delegated.user) == (None, g, "claude-conn", "alice")
    assert ok("events", "ctl")["events"][0]["grant"] == g


def test_the_grantor_and_the_delegate_page_a_grants_activity_newest_first():
    world.build()
    g = grant("profile", "propose", delegate="bob")
    ok("delegation_accept", "bob", delegation=g)
    ok("profile_update", "bob", nickname="Ali", on_behalf_of="alice")
    ok("profile_update", "alice", nickname="Alie")
    p = ok("proposal_create", "bob", title="Park", on_behalf_of="alice")["proposal"]
    ok("proposal_withdraw", "bob", proposal=p, on_behalf_of="alice")
    for reader in ("alice", "bob"):
        page = ok("delegation_activity", reader, delegation=g, count=2)
        assert [e["op"] for e in page["events"]] == ["proposal_withdraw", "proposal_create"]
        assert {e["grant"] for e in page["events"]} == {g} and page["since"] == fake_chain.now()
        rest = ok("delegation_activity", reader, delegation=g, count=2, **{"from": page["next"]})
        assert [e["op"] for e in rest["events"]] == ["profile_update"] and rest["next"] is None
    assert "not yours" in denied("delegation_activity", "carla", delegation=g)
    assert "unknown" in denied("delegation_activity", "alice", delegation="D-99")


def test_the_activity_says_how_far_back_the_log_reaches(monkeypatch):
    world.build()
    g = grant("profile", delegate="bob")
    ok("delegation_accept", "bob", delegation=g)
    monkeypatch.setattr(op, "EVENTS_KEPT", 3)
    for i in range(4):
        later(60)
        ok("profile_update", "bob", nickname=f"Ali{i}", on_behalf_of="alice")
    page = ok("delegation_activity", "alice", delegation=g)
    assert len(page["events"]) == 3 and page["since"] == page["events"][-1]["at"]


# ---- Revoking everything


def test_revoke_all_ends_every_open_grant_a_page_at_a_time():
    world.build()
    ended = grant("vote", delegate="agent-0")
    ok("delegation_revoke", "alice", delegation=ended)
    open_grants = [grant("vote", delegate=who) for who in ("agent-1", "bob", "carla")]  # bob and carla still to accept
    other = grant("vote", grantor="dana", delegate="agent-1")
    first = ok("delegation_revoke_all", "alice", count=2)
    assert first["revoked"] == 1 and first["next"]  # the revoked one was skipped
    rest = ok("delegation_revoke_all", "alice", **{"from": first["next"]}, count=2)
    assert rest["revoked"] == 2 and rest["next"] is None
    assert {Delegation[d].status for d in open_grants} == {"revoked"}
    assert Delegation[other].status == "active"
    assert ok("delegation_revoke_all", "alice") == {"ok": True, "revoked": 0, "next": None}


def test_revoke_all_is_refused_under_any_grant():
    world.build()
    grant("*")
    assert "cannot be done" in denied("delegation_revoke_all", "claude-conn", on_behalf_of="alice")
    assert Delegation.count() == 1 and Delegation["D-1"].status == "active"


# ---- The vocabulary


def test_the_vocabulary_lists_the_groups_the_caller_can_grant():
    depts = world.build()
    groups = {g["id"]: g for g in ok("delegable_operations", "alice")["groups"]}
    assert list(groups) == ["see", "profile", "vote", "propose", "filings", "justice"]
    vote = groups["vote"]
    label, summary, risk, longest = ggg_delegation.GROUPS["vote"]
    assert (vote["label"], vote["summary"], vote["risk"], vote["longest"]) == (label, summary, risk, longest)
    assert vote["operations"] == [{"operation": "proposal_vote", "permission": "proposal.vote", "tier": "direct",
                                   "confirm_if": None}]
    assert groups["propose"]["confirms"] and not vote["confirms"]
    assert {o["operation"]: o["confirm_if"] for o in groups["propose"]["operations"]} == {
        "proposal_create": "with_action", "proposal_withdraw": None}
    world.grant(depts["Infrastructure"], "transfer.create")
    assert "money" in [g["id"] for g in ok("delegable_operations", "carla")["groups"]]
    assert "money" in [g["id"] for g in ok("delegation_groups", "carla")["groups"]]
    assert [g["id"] for g in ok("delegable_operations", "ctl")["groups"]][-1] == "money"


def test_the_old_operations_list_stays_what_a_permission_grant_covers():
    world.build()
    listed = {o["op"] for o in ok("delegable_operations", "alice")["operations"] if not o["op"].startswith("t_")}
    assert listed == poa.LEGACY


def test_the_groups_are_paged():
    world.build()
    first = ok("delegation_groups", "alice", count=4)
    assert [g["id"] for g in first["groups"]] == ["see", "profile", "vote", "propose"] and first["next"] == 5
    rest = ok("delegation_groups", "alice", count=4, **{"from": first["next"]})
    assert [g["id"] for g in rest["groups"]] == ["filings", "justice"] and rest["next"] is None


# ---- The tables


def test_every_registered_operation_is_classified_exactly_once():
    for name in NAMES:
        assert (name in ggg_delegation.OPERATIONS) + (name in poa.OWN) == 1, name
        spec = op.OPS[name]
        assert spec["tier"] in ("direct", "confirm", "member", "founder", "none"), name
        assert spec["delegable"] == (spec["tier"] == "direct"), name
    assert sorted(set(poa.OWN) - set(op.OPS)) == [], "OWN names an operation that is not registered"


def test_a_classified_operation_cannot_disagree_with_its_decorator():
    """GGG's tier decides: a decorator's ``delegable`` counts only for an operation no table classifies."""
    spec = {"delegable": False}
    poa.classify(spec, "me")
    assert spec["tier"] == "direct" and spec["delegable"]
    spec = {"delegable": True}
    poa.classify(spec, "orchestration_decide")
    assert spec["tier"] == "confirm" and not spec["delegable"]
    spec = {"delegable": True}
    poa.classify(spec, "t_a_test_operation")
    assert spec["tier"] is None and spec["delegable"]


def test_the_tables_are_consistent():
    file = json.loads((ROOT / "ggg" / "delegation.json").read_text())
    assert set(poa.CONDITIONS) == set(file["conditions"])
    for name, (tier, group, after_launch, condition) in {**ggg_delegation.OPERATIONS, **poa.OWN}.items():
        assert tier in file["tiers"], name
        assert group is None or group in ggg_delegation.GROUPS, name
        assert after_launch is None or after_launch in ggg_delegation.GROUPS, name
        assert condition is None or condition in poa.CONDITIONS, name
        assert tier != "none" or group is None, name
    assert all(op.OPS[name]["tier"] in ("direct", "confirm") for name in poa.ENABLED)


@pytest.mark.parametrize("condition, asks, doesnt", [
    ("with_action", {"action": {"op": "me"}}, {"title": "x"}),
    ("profile_or_seat", {"profile": "treasurer"}, {"profile": "member"}),
    ("profile_or_seat", {"position": "clerk"}, {}),
    ("trust_step", {"step": "trust_policy"}, {"step": "branding"}),
    ("shares_keys", {"keys": {"bob": "key"}}, {"keys": {}}),
    ("changes_admins", {"admins": ["bob"]}, {"description": "x"}),
    ("approves", {"approve": True}, {"approve": False}),
])
def test_the_conditions_that_make_a_direct_operation_ask(condition, asks, doesnt):
    assert poa.CONDITIONS[condition](asks) and not poa.CONDITIONS[condition](doesnt)


def test_the_conditions_that_depend_on_the_realm_being_in_setup():
    world.build()
    assert poa.group_of("realm_configure") == "setup"
    assert poa.CONDITIONS["in_setup"]({}) and poa.CONDITIONS["head_or_veto_in_setup"]({"head": "bob"})
    assert poa.CONDITIONS["head_or_veto_in_setup"]({"veto": ["dana"]})
    assert not poa.CONDITIONS["head_or_veto_in_setup"]({"description": "x", "veto": []})
    from ggg import Realm
    Realm["1"].status = "active"
    assert not poa.CONDITIONS["in_setup"]({}) and not poa.CONDITIONS["head_or_veto_in_setup"]({"head": "bob"})
    assert poa.group_of("realm_configure") == "admin"


# ---- GGG's scope


def test_ggg_names_a_grants_groups_as_group_entries_and_never_mixes_them_with_permissions():
    assert ggg_api.grouped({"operations": ["group:vote", "group:*"], "delegate": "x"}, None) == {
        "groups": ["vote", "*"], "delegate": "x"}
    assert ggg_api.grouped({"operations": ["proposal.vote"]}, None) == {"operations": ["proposal.vote"]}
    with pytest.raises(ggg_api.BadArgument):
        ggg_api.grouped({"operations": ["group:vote", "proposal.vote"]}, None)
    assert ggg_api.named({"scope": {"groups": ["vote", "*"]}}) == ["group:vote", "group:*"]
    assert ggg_api.named({"scope": {"operations": ["proposal.vote"]}}) == ["proposal.vote"]
