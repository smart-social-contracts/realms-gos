"""Pending actions (phase 4): a delegate's call that asks the member each time is prepared, and runs only on the member's
own approval of its hash. The doors phase 0 closed come back through it, and only through it."""
import json
import pathlib
import subprocess
import sys

import pytest

import fake_chain
import ggg_api
import ggg_delegation
import ggg_methods as g
import keys_people
import world
from api import extensions, federation
from core import auth, chain, op, pending, poa
from core.candid import Reader
from ggg import (Delegation, Department, Event, FederalVote, FederalVoteLeg, Forward, Identity, Member, Notification, PaymentAccount,
                 PendingAction, Proposal)
from kit import call, denied, later, ok, rows
from mirror import FakePrincipal as P, convert
from test_delegation_groups import finance_for_carla
from test_quarters import CAPITAL, capital, quarter, reply

DAY = 86400
ACCOUNT = dict(network="ICP", address="aaaaa-bbbbb", symbol="REALM", label="Savings")
ACTION = {"op": "governance_update", "args": {"voting_threshold": 60_000_000}}
# Every way a call can reach `dispatch` besides the member's own signed message.
OTHER_ORIGINS = ("extension", "codex", "shell", "task", "replay", "confirmed")


def grant(*groups, grantor="alice", delegate="claude-conn", days=30):
    return ok("delegation_grant", grantor, delegate=delegate, groups=list(groups), expires_in=days * DAY)["delegation"]


def file(name="payment_account_add", delegate="claude-conn", grantor="alice", **args):
    """The delegate prepares a call for the member: the action's id."""
    out = ok(name, delegate, on_behalf_of=grantor, **(args or ACCOUNT))
    assert out["pending"].startswith("AC-") and out["approve_link"] == "/delegations/confirm/" + out["pending"]
    return out["pending"]


def card(action, who="alice"):
    return ok("action_get", who, action=action)["action"]


def approve(action, who="alice", **extra):
    return call("action_approve", who, action=action, hash=card(action, who)["hash"], **extra)


def approved(action, who="alice"):
    out = ok("action_approve", who, action=action, hash=card(action, who)["hash"])
    return out["action"]


def next_step(action):
    """What the card of ``action`` says approving it does."""
    return {line["label"]: line["value"] for line in card(action)["summary"]}["What happens next"]


def only_pending_action_changed(action):
    assert card(action)["status"] == "pending" and PaymentAccount.count() == 0


# ---- Filing


def test_a_delegates_confirm_call_is_filed_and_nothing_runs():
    world.build()
    g1 = grant("payout", "profile")
    out = ok("payment_account_add", "claude-conn", on_behalf_of="alice", **ACCOUNT)
    a = out["pending"]
    assert out["expires_at"] == fake_chain.now() + DAY and PaymentAccount.count() == 0
    c = card(a)
    assert (c["grantor"], c["delegate"], c["grant"], c["method"], c["status"]) == (
        "alice", "claude-conn", g1, "payment_account_add", "pending")
    assert c["args"] == ACCOUNT and c["note"] == "" and c["risk"] == ggg_delegation.GROUPS["payout"][2]
    assert c["expires_at"] == c["filed_at"] + DAY and c["decided_at"] == 0 and c["result"] is None
    lines = {line["label"]: line["value"] for line in c["summary"]}
    assert lines["address"] == "aaaaa-bbbbb" and lines["symbol"] == "REALM"
    assert lines["What changes"] == ('Realm payments to you in "REALM" will go to "aaaaa-bbbbb" instead of "alice"')
    assert c["hash"] == pending.seal(a, "payment_account_add", ACCOUNT, "alice", "claude-conn", g1, c["expires_at"])
    # the member is told, and a delegate cannot mark it read
    told = [n for n in rows(Notification) if n.topic == "poa.confirm"]
    assert len(told) == 1 and told[0].recipient == "User:alice" and told[0].href == "/delegations/confirm/" + a
    unread = denied("notification_read", "claude-conn", notification=told[0].id, on_behalf_of="alice")
    assert "tells you what your delegates did" in unread
    assert ok("notification_read", "claude-conn", all=True, on_behalf_of="alice")["read"] == 0
    assert ok("notification_read", "alice", notification=told[0].id)["read"] == 1


def test_the_filing_is_audited_as_the_delegates_call_for_the_member():
    world.build()
    g1 = grant("payout")
    a = file()
    (event,) = [e for e in rows(Event) if e.op == "payment_account_add"]
    assert (event.user, event.by, event.origin, event.grant, event.result) == (
        "alice", "claude-conn", "user", g1, {"pending": a})
    assert event.prepared_by == ""


def test_the_hash_belongs_to_one_realm_and_one_record(monkeypatch):
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    assert sealed == pending.seal(a, "payment_account_add", ACCOUNT, "alice", "claude-conn", "D-1", fake_chain.now() + DAY)
    monkeypatch.setattr(fake_chain, "SELF", "another-realm")
    assert sealed != pending.seal(a, "payment_account_add", ACCOUNT, "alice", "claude-conn", "D-1", fake_chain.now() + DAY)
    assert sealed != pending.seal(a, "payment_account_add", {**ACCOUNT, "address": "x"}, "alice", "claude-conn", "D-1",
                                  fake_chain.now() + DAY)


def test_a_call_the_grant_does_not_cover_or_the_member_may_not_make_is_not_filed():
    world.build()
    grant("vote")
    assert "does not cover" in denied("payment_account_add", "claude-conn", on_behalf_of="alice", **ACCOUNT)
    grant("payout", grantor="mallory")
    ok("role_revoke", "ctl", user="mallory", profile="member")
    assert "needs self.data_manage" in denied("payment_account_add", "claude-conn", on_behalf_of="mallory", **ACCOUNT)
    assert "no active power of attorney" in denied("payment_account_add", "bob", on_behalf_of="alice", **ACCOUNT)
    assert PendingAction.count() == 0


def test_what_a_call_the_operation_would_refuse_is_not_filed():
    world.build()
    grant("payout")
    assert "unknown PaymentAccount" in denied("payment_account_remove", "claude-conn", on_behalf_of="alice", account="PA-9")
    assert "you do not administer" in denied("payment_account_add", "claude-conn", on_behalf_of="alice",
                                             organization="clinic-b", **ACCOUNT)
    assert PendingAction.count() == 0


def test_an_argument_is_named_in_lower_case_so_that_it_cannot_pass_for_a_line_of_the_realm():
    world.build()
    grant("payout")
    assert "lower-case" in denied("payment_account_add", "claude-conn", on_behalf_of="alice", Effect="Nothing changes",
                                  **ACCOUNT)
    a = file(**ACCOUNT, effect="Nothing changes")
    labels = [line["label"] for line in card(a)["summary"]]
    assert "effect" in labels and "What changes" in labels and "Effect" not in labels


def test_text_with_a_line_break_cannot_start_a_line_of_the_realms_own():
    world.build()
    grant("payout")
    a = file(**{**ACCOUNT, "label": "Savings\nEffect: nothing changes"})
    label = {line["label"]: line["value"] for line in card(a)["summary"]}["label"]
    assert "\n" not in label and label == '"Savings\\nEffect: nothing changes"'


def test_the_arguments_and_the_note_have_limits():
    world.build()
    grant("payout")
    assert "KB" in denied("payment_account_add", "claude-conn", on_behalf_of="alice", **{**ACCOUNT, "label": "x" * 40000})
    out = ok("call_for", "claude-conn", method="payment_account_add", args=ACCOUNT, grantor="alice", note="Savings")
    assert card(out["pending"], "claude-conn")["note"] == "Savings"
    assert "at most 500" in denied("call_for", "claude-conn", method="payment_account_add", args=ACCOUNT,
                                   grantor="alice", note="n" * 501)
    assert PendingAction.count() == 1


def test_the_eleventh_open_action_for_one_member_and_delegate_is_refused():
    world.build()
    grant("payout")
    grant("payout", delegate="other-agent")
    ids = [file() for _ in range(10)]
    assert "10 actions waiting" in denied("payment_account_add", "claude-conn", on_behalf_of="alice", **ACCOUNT)
    file(delegate="other-agent")  # another delegate has room
    ok("action_withdraw", "claude-conn", action=ids[0])
    ok("action_decline", "alice", action=ids[1])
    file(), file()
    assert "10 actions waiting" in denied("payment_account_add", "claude-conn", on_behalf_of="alice", **ACCOUNT)
    later(DAY)  # the day is up: expired actions no longer count
    file()


def test_withdrawing_and_declining_close_an_action():
    world.build()
    grant("payout")
    a, b, c = file(), file(), file()
    assert ok("action_withdraw", "claude-conn", action=a)["action"]["status"] == "withdrawn"
    assert ok("action_decline", "alice", action=b)["action"]["status"] == "declined"
    for gone in (a, b):
        assert f"is {card(gone)['status']}" in approve(gone)["error"]
    assert "was not prepared by you" in denied("action_withdraw", "alice", action=c)
    assert "needs self.confirm" in denied("action_decline", "claude-conn", action=c)  # an AI connection is no member
    assert "not yours to decide" in denied("action_decline", "bob", action=c)
    assert PaymentAccount.count() == 0


# ---- The approval


def test_an_approval_runs_exactly_the_stored_call_as_the_members_own_act():
    world.build()
    g1 = grant("payout")
    a = file()
    done = approved(a)
    assert done["status"] == "done" and done["result"]["ok"] and done["decided_at"] == fake_chain.now()
    account = PaymentAccount[done["result"]["account"]]
    assert (account.owner, account.network, account.address, account.symbol, account.label) == (
        "User:alice", "ICP", "aaaaa-bbbbb", "REALM", "Savings")
    # what the delegate reads afterwards
    assert card(a, "claude-conn")["status"] == "done" and card(a, "claude-conn")["result"] == {
        "ok": True, "account": account.id}
    event = [e for e in rows(Event) if e.origin == "confirmed"][0]
    assert (event.op, event.user, event.by, event.prepared_by, event.grant, event.origin) == (
        "payment_account_add", "alice", "alice", "claude-conn", g1, "confirmed")
    assert [n for n in rows(Notification) if n.topic == "poa"] == []  # the member did it: no notice of a delegate's act


def test_an_approval_with_another_hash_or_with_any_other_argument_is_refused_and_changes_nothing():
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    assert "does not match" in denied("action_approve", "alice", action=a, hash="0" * 64)
    assert "does not match" in denied("action_approve", "alice", action=a, hash=sealed[:-1] + "0")
    for extra in (dict(address="mine"), dict(args=ACCOUNT), dict(network="ICP"), dict(note="x")):
        assert "nothing else" in denied("action_approve", "alice", action=a, hash=sealed, **extra)
    assert "nothing else" in denied("action_approve", "alice", action=a)
    only_pending_action_changed(a)
    ok("action_approve", "alice", action=a, hash=sealed)


def test_a_second_approval_is_refused():
    world.build()
    grant("payout")
    a = file()
    approved(a)
    assert "is done" in approve(a)["error"]
    assert PaymentAccount.count() == 1


def test_an_approval_after_a_day_is_refused_and_the_action_reads_expired():
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    later(DAY - 1)
    assert card(a)["status"] == "pending"
    later(1)
    assert card(a)["status"] == "expired"  # worked out as it is read
    assert "is expired" in denied("action_approve", "alice", action=a, hash=sealed)
    assert PaymentAccount.count() == 0 and PendingAction[a].open_link is None


@pytest.mark.parametrize("origin", OTHER_ORIGINS)
def test_an_approval_from_a_task_an_extension_a_codex_a_shell_a_replay_or_another_approval_is_refused(origin):
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    fake_chain.state["caller"] = "alice"
    out = json.loads(op.run(op.dispatch("action_approve", json.dumps({"action": a, "hash": sealed}), origin=origin,
                                        signer="alice", declared=("action_approve",))))
    assert not out["ok"], out
    only_pending_action_changed(a)


def test_an_approval_by_the_delegate_by_another_member_or_for_the_member_is_refused():
    world.build()
    grant("payout", "vote")
    a = file()
    sealed = card(a)["hash"]
    assert "needs self.confirm" in denied("action_approve", "claude-conn", action=a, hash=sealed)  # not even a member
    assert "not yours to decide" in denied("action_approve", "bob", action=a, hash=sealed)
    ok("delegation_accept", "bob", delegation=grant("payout", delegate="bob"))
    assert "not yours to decide" in denied("action_approve", "bob", action=file(delegate="bob"), hash=sealed)
    for_alice = denied("action_approve", "claude-conn", action=a, hash=sealed, on_behalf_of="alice")
    assert "cannot be done under this power of attorney" in for_alice
    only_pending_action_changed(a)


def test_an_approval_through_call_for_is_refused():
    world.build()
    ok("delegation_grant", "alice", delegate="claude-conn", groups=["*"], expires_in=DAY)
    a = file()
    sealed = card(a)["hash"]
    assert "cannot be done for a member" in denied("call_for", "claude-conn", method="action_approve", grantor="alice",
                                                   args={"action": a, "hash": sealed})
    out = call_for("action_approve", action=a, hash=sealed)
    assert "Err" in out and "cannot be done for a member" in out["Err"]["Forbidden"]["message"]
    only_pending_action_changed(a)


def do(update):
    return op.run(update)


@pytest.mark.parametrize("how", ("revoked", "replaced", "expired", "lost the group"))
def test_an_approval_after_the_grant_ended_or_lost_the_group_is_refused_and_the_action_reads_void(how):
    world.build()
    if how == "expired":
        g1 = ok("delegation_grant", "alice", delegate="claude-conn", groups=["payout"], expires_in=3600)["delegation"]
    else:
        g1 = grant("payout")
    a = file()
    sealed = card(a)["hash"]
    if how == "revoked":
        ok("delegation_revoke", "alice", delegation=g1)
    elif how == "replaced":
        r = ok("delegation_request", "claude-conn", grantor="alice", groups=["payout"], expires_in=DAY)["request"]
        ok("delegation_request_decide", "alice", request=r, approve=True)
        assert Delegation[g1].status == "revoked"
    elif how == "expired":
        later(3600)
    else:
        Delegation[g1].scope = {"groups": ["vote"]}
    assert card(a)["status"] == "void" and card(a, "claude-conn")["status"] == "void"
    assert "is void" in denied("action_approve", "alice", action=a, hash=sealed)
    assert PaymentAccount.count() == 0 and PendingAction[a].open_link is None


def test_an_approval_of_a_call_the_member_may_no_longer_make_fails_the_action():
    world.build()
    grant("payout")
    a = file()
    ok("role_revoke", "ctl", user="alice", profile="member")  # alice no longer holds self.data_manage
    done = approved(a)
    assert done["status"] == "failed" and "needs self.data_manage" in done["result"]["error"]
    assert PaymentAccount.count() == 0
    assert "is failed" in approve(a)["error"]  # never retried


def test_a_banned_member_cannot_approve():
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    Member["alice"].status = "banned"
    assert not call("action_approve", "alice", action=a, hash=sealed)["ok"]
    Member["alice"].status = "active"
    assert PendingAction[a].status == "pending" and PaymentAccount.count() == 0


def test_an_approval_that_never_finished_reads_failed_and_is_never_retried():
    world.build()
    grant("payout")
    a = file()
    row = PendingAction[a]
    row.status, row.decided_at, row.open_link = "approving", fake_chain.now(), None  # what a trap leaves behind
    assert "is approving" in approve(a)["error"] and card(a)["status"] == "approving"
    later(600)
    assert card(a)["status"] == "failed"
    assert "is failed" in approve(a)["error"] and PaymentAccount.count() == 0


# ---- Who reads what


def test_an_action_is_read_by_its_grantor_and_its_delegate_alone():
    world.build()
    grant("payout")
    a = file()
    assert card(a, "claude-conn")["id"] == a and card(a)["id"] == a
    assert card(a, "bob") is None and ok("action_get", "alice", action="AC-99")["action"] is None
    assert [c["id"] for c in ok("action_list", "alice")["actions"]] == [a]
    assert [c["id"] for c in ok("action_list", "claude-conn", role="delegate")["actions"]] == [a]
    assert ok("action_list", "bob")["actions"] == [] and ok("action_list", "alice", role="delegate")["actions"] == []
    assert ok("action_list", "alice", status="done")["actions"] == []
    approved(a)
    assert [c["status"] for c in ok("action_list", "alice", status="done")["actions"]] == ["done"]
    # a delegate reads, through the grant, the actions it prepared and no other delegate's (test_action_readers.py)
    grant("see", delegate="reader")
    assert card(a, "reader") is None
    assert ok("read_for", "reader", method="action_get", args={"action": a}, grantor="alice")["action"] is None


# ---- The doors phase 0 closed


def test_a_delegates_proposal_with_an_action_files_no_proposal_until_the_member_approves_it():
    world.build()
    grant("propose", "vote", delegate="claude-conn")
    out = ok("proposal_create", "claude-conn", title="Raise the threshold", action=ACTION, on_behalf_of="alice")
    assert Proposal.count() == 0
    c = card(out["pending"])
    lines = {line["label"]: line["value"] for line in c["summary"]}
    assert (lines["Who votes"], lines["What it would run"]) == ("the whole realm", '"governance_update"')
    assert c["risk"] == ggg_delegation.GROUPS["propose"][2]
    done = approved(out["pending"])
    p = Proposal[done["result"]["proposal"]]
    assert (p.proposer, p.filed_under, p.on_behalf_of, p.prepared_by, p.action) == ("alice", "", "", "claude-conn", ACTION)
    assert p.status == "voting"
    (event,) = [e for e in rows(Event) if e.origin == "confirmed"]
    assert (event.op, event.prepared_by) == ("proposal_create", "claude-conn")


def test_a_codex_install_proposed_by_a_delegate_is_a_pending_action_and_then_the_members_own_proposal():
    world.build()
    grant("propose")
    install = {"op": "codex_install", "args": {"url": "https://example.org/layered.json", "checksum": "sha256:" + "ab" * 32}}
    a = ok("proposal_create", "claude-conn", title="Install the layered codex", action=install,
           on_behalf_of="alice")["pending"]
    lines = {line["label"]: line["value"] for line in card(a)["summary"]}
    assert lines["Codex source"] == '"https://example.org/layered.json"'
    assert lines["Codex checksum"] == '"sha256:' + "ab" * 32 + '"' and Proposal.count() == 0
    p = Proposal[approved(a)["result"]["proposal"]]
    assert (p.proposer, p.prepared_by, p.action) == ("alice", "claude-conn", install) and p.status == "voting"


def test_a_delegates_federal_vote_opens_nothing_until_the_member_approves_it():
    capital("north")
    reply("north", "federation_message", proposal="P-1")
    grant("propose")
    a = ok("federal_vote_propose", "claude-conn", title="Adopt tax policy 3", action=ACTION, on_behalf_of="alice")["pending"]
    assert FederalVote.count() == 0 and not [c for c in fake_chain.state["calls"] if "federal_vote.open" in (c[2] or "")]
    effect = {line["label"]: line["value"] for line in card(a)["summary"]}["What changes"]
    assert "every quarter" in effect and "as the realm itself" in effect
    done = approved(a)
    vote = FederalVote[done["result"]["vote"]]
    assert vote.status == "open"


def test_a_delegates_quarter_move_is_never_done_without_the_members_tap(monkeypatch):
    quarter(monkeypatch, "north", "south")
    reply("south", "federation_message", member="alice")
    grant("membership")
    a = ok("self_change_quarter", "claude-conn", quarter="south", on_behalf_of="alice")["pending"]
    assert Member["alice"] is not None and Forward.count() == 0
    lines = {line["label"]: line["value"] for line in card(a)["summary"]}
    assert lines["What changes"] == "You become a guest here and your profiles in this quarter are deleted"
    approved(a)
    assert Member["alice"] is None and Forward.count() == 1


def test_identity_proofs_and_payout_accounts_are_never_done_without_the_members_tap():
    world.build()
    grant("identity", "payout")
    a = ok("identity_submit", "claude-conn", kind="passport", on_behalf_of="alice")["pending"]
    b = file()
    assert Identity.count() == 0 and PaymentAccount.count() == 0
    approved(a), approved(b)
    assert Identity.count() == 1 and PaymentAccount.count() == 1


def test_removing_a_payout_account_says_where_the_realm_pays_afterwards():
    world.build()
    grant("payout")
    first, second = (ok("payment_account_add", "alice", **{**ACCOUNT, "address": x})["account"] for x in ("one", "two"))
    a = ok("payment_account_remove", "claude-conn", account=first, on_behalf_of="alice")["pending"]
    lines = {line["label"]: line["value"] for line in card(a)["summary"]}
    assert lines["What changes"] == 'Realm payments to you in "REALM" will go to "two" instead of "one"'
    approved(a)
    assert PaymentAccount[first] is None and PaymentAccount[second] is not None


def test_a_new_payout_account_behind_an_existing_one_says_so():
    world.build()
    grant("payout")
    ok("payment_account_add", "alice", **ACCOUNT)
    a = file(**{**ACCOUNT, "address": "second"})
    effect = {line["label"]: line["value"] for line in card(a)["summary"]}["What changes"]
    assert "keep going to" in effect and "comes after it" in effect


@pytest.mark.parametrize("name,args", (("payment_account_add", ACCOUNT), ("identity_submit", {"kind": "passport"}),
                                       ("self_change_quarter", {"quarter": "south"}),
                                       ("federal_vote_propose", {"title": "x", "action": ACTION}),
                                       ("proposal_create", {"title": "x", "action": ACTION})))
def test_under_a_grant_made_before_groups_all_of_them_are_refused_as_before(name, args):
    world.build()
    ok("delegation_grant", "alice", delegate="claude-conn", operations=["self.data_manage", "proposal.create"],
       expires_in=DAY)
    assert denied(name, "claude-conn", on_behalf_of="alice", **args)
    assert PendingAction.count() == 0 and Proposal.count() == 0


def test_I_ID_13_no_delegate_of_the_member_votes_on_a_proposal_the_member_approved_from_a_pending_action():
    world.build()
    grant("propose", "vote", delegate="claude-conn")
    grant("vote", delegate="agent-2")
    grant("vote", grantor="bob", delegate="claude-conn")
    a = ok("proposal_create", "claude-conn", title="Raise the threshold", action=ACTION, on_behalf_of="alice")["pending"]
    p = approved(a)["result"]["proposal"]
    vote = dict(proposal=p, choice="yes")
    assert "filed by claude-conn" in denied("proposal_vote", "claude-conn", on_behalf_of="alice", **vote)
    assert "filed by claude-conn" in denied("proposal_vote", "claude-conn", on_behalf_of="bob", **vote)
    assert "filed by a delegate of alice" in denied("proposal_vote", "agent-2", on_behalf_of="alice", **vote)
    ok("proposal_vote", "alice", **vote)
    ok("proposal_vote", "bob", **vote)


# ---- A governed call, a read, a secret


@pytest.fixture
def confirm_ops(monkeypatch):
    """Operations that ask the member each time, for what no operation of this release does: a governed call, a read,
    a reply with a secret, a party to the matter."""
    seen = []
    rows_ = {"t_governed": ("confirm", "payout"), "t_read": ("confirm", "identity"), "t_secret": ("confirm", "payout"),
             "t_party": ("confirm", "payout"), "t_direct_secret": ("direct", "profile")}
    for name, (tier, group) in rows_.items():
        monkeypatch.setitem(poa.OWN, name, (tier, group, None, None))
    monkeypatch.setattr(poa, "ENABLED", poa.ENABLED | set(rows_))

    @op.op("self.data_manage", governed=lambda a: Department["Infrastructure"])
    def t_governed(c, args):
        seen.append((c.user, c.by, c.grant, c.prepared_by))
        return {"ran": True}

    @op.op("self.data_view", read=True)
    def t_read(c, args):
        seen.append(("read", c.user, c.by))
        return {"private": "what the member keeps", "for": c.user}

    @op.op("self.data_manage")
    def t_secret(c, args):
        return {"id": 7, "code": "S3CRET", "invite": {"id": "I-1", "code": "ABC"}, "nested": [{"private_data": "p"}]}

    @op.op("self.data_manage", secret=("token",))
    def t_direct_secret(c, args):
        return {"token": "S3CRET-TOKEN", "n": 1}

    @op.op("self.data_manage", parties=lambda a: {fake_chain.state.get("party", "")})
    def t_party(c, args):
        return {}

    yield seen
    for name in rows_:
        op.OPS.pop(name, None)


def test_an_approved_governed_call_follows_the_members_own_rule(confirm_ops):
    depts = world.build()
    grant("payout")
    a = ok("t_governed", "claude-conn", on_behalf_of="alice")["pending"]
    assert next_step(a) == "Runs at once when you approve"
    assert approved(a)["result"]["ran"] and confirm_ops == [("alice", "alice", "", "claude-conn")]
    world.policy(depts["Infrastructure"], 2, 3)
    b = ok("t_governed", "claude-conn", on_behalf_of="alice")["pending"]
    assert "Files a proposal under Infrastructure" in next_step(b)
    p = Proposal[approved(b)["result"]["proposal"]]
    assert (p.proposer, p.filed_under, p.prepared_by, p.status) == ("alice", "", "claude-conn", "voting")
    assert len(confirm_ops) == 1


def test_an_approval_of_a_call_with_a_party_to_the_matter_fails_the_action(confirm_ops):
    world.build()
    grant("payout")
    fake_chain.state["party"] = "alice"
    assert "party" in denied("t_party", "claude-conn", on_behalf_of="alice")  # not filed
    fake_chain.state["party"] = "bob"
    a = ok("t_party", "claude-conn", on_behalf_of="alice")["pending"]
    fake_chain.state["party"] = "alice"  # alice becomes a party to the matter before she approves
    assert "party" in approved(a)["result"]["error"] and card(a)["status"] == "failed"


def test_a_secret_in_the_reply_is_in_the_approvals_reply_alone(confirm_ops):
    world.build()
    grant("payout")
    a = ok("t_secret", "claude-conn", on_behalf_of="alice")["pending"]
    done = approved(a)
    assert done["result"]["code"] == "S3CRET" and done["result"]["invite"]["code"] == "ABC"
    for who in ("alice", "claude-conn"):
        kept = card(a, who)["result"]
        assert kept == {"ok": True, "id": 7, "code": "", "invite": {"id": "I-1", "code": ""},
                        "nested": [{"private_data": ""}]}
    assert "S3CRET" not in json.dumps(PendingAction[a].serialize())
    (event,) = [e for e in rows(Event) if e.origin == "confirmed"]
    assert "S3CRET" not in json.dumps(event.result)


def test_an_approved_read_is_collected_once_by_its_delegate_and_stores_nothing(confirm_ops):
    world.build()
    grant("identity")
    a = ok("call_for", "claude-conn", method="t_read", args={}, grantor="alice")["pending"]  # a read, filed as an update
    assert "needs the member's approval" in denied("read_for", "claude-conn", method="t_read", args={}, grantor="alice")
    assert "is pending" in denied("action_collect", "claude-conn", action=a)
    done = approved(a)
    assert done["status"] == "approved" and done["result"] is None and confirm_ops == []
    assert "was not prepared by you" in denied("action_collect", "alice", action=a)
    assert "was not prepared by you" in denied("action_collect", "bob", action=a)
    out = ok("action_collect", "claude-conn", action=a)
    assert out["result"]["private"] == "what the member keeps" and confirm_ops == [("read", "alice", "alice")]
    assert card(a)["status"] == "done" and card(a)["result"] is None
    assert "is done" in denied("action_collect", "claude-conn", action=a)
    (event,) = [e for e in rows(Event) if e.op == "t_read" and e.origin == "confirmed"]
    assert (event.user, event.by, event.prepared_by) == ("alice", "claude-conn", "claude-conn")
    assert event.result == {"collected": a}
    assert "what the member keeps" not in json.dumps([e.result for e in rows(Event)])


@pytest.mark.parametrize("how", ("expired", "revoked"))
def test_an_approved_read_is_not_collected_once_it_lapsed_or_its_grant_ended(confirm_ops, how):
    world.build()
    g1 = grant("identity")
    a = ok("t_read", "claude-conn", on_behalf_of="alice")["pending"]
    approved(a)
    if how == "expired":
        later(DAY)
    else:
        ok("delegation_revoke", "alice", delegation=g1)
    assert f"is {'expired' if how == 'expired' else 'void'}" in denied("action_collect", "claude-conn", action=a)
    assert confirm_ops == []


# ---- The generic call and the typed methods


def candid(name, **args):
    return ggg_api.call_record(name, args)["arg"]


def call_for(name, caller="claude-conn", grantor="alice", note=None, **args):
    fake_chain.state["caller"] = caller
    return do(g.ggg_call_for({"method": f"ggg_{name}", "arg": candid(name, **args), "on_behalf_of": P(grantor),
                              "note": note}))


def test_ggg_call_for_files_a_pending_action_and_ggg_action_approve_runs_it():
    world.build()
    grant("payout")
    out = call_for("payment_account_add", note="Savings", **ACCOUNT)
    pending_action = out["Ok"]["Pending"]
    assert (pending_action["grantor"], pending_action["delegate"], pending_action["note"]) == (
        P("alice"), P("claude-conn"), "Savings")
    assert next(iter(pending_action["status"])) == "Pending" and pending_action["result"] is None
    assert pending_action["call"]["method"] == "ggg_payment_account_add"
    assert Reader(chain.candid_decode(pending_action["call"]["arg"])).args()
    assert {"label": "address", "value": "aaaaa-bbbbb"} in pending_action["summary"]
    fake_chain.state["caller"] = "alice"
    got = do(g.ggg_action_approve({"action": pending_action["id"], "hash": pending_action["hash"], "on_behalf_of": None}))
    shown = got["Ok"]
    assert next(iter(shown["status"])) == "Done" and shown["result"]
    assert PaymentAccount.count() == 1
    fake_chain.state["caller"] = "claude-conn"
    assert g.ggg_action_get(pending_action["id"])["Ok"]["result"] == shown["result"]
    assert g.ggg_action_list({"role": {"Delegate": None}, "status": None, "cursor": None, "limit": None})["Ok"][
        "items"][0]["id"] == pending_action["id"]


def test_ggg_call_for_runs_a_direct_call():
    world.build()
    grant("profile")
    done = call_for("profile_update", nickname="Ali")
    assert "Done" in done["Ok"] and Reader(chain.candid_decode(done["Ok"]["Done"])).args()
    assert world.User["alice"].nickname == "Ali"
    (event,) = [e for e in rows(Event) if e.op == "profile_update"]
    assert (event.user, event.by, event.origin) == ("alice", "claude-conn", "user")


def test_ggg_call_for_files_the_proposal_of_a_governed_call():
    finance_for_carla()
    g1 = grant("money", grantor="carla")
    out = call_for("transfer_create", grantor="carla", fund="ROADS", to="buildco", amount=10)
    p = out["Ok"]["Proposed"]
    assert (Proposal[p].proposer, Proposal[p].filed_under, Proposal[p].prepared_by) == ("carla", g1, "")


def test_ggg_read_for_reads_for_the_grantor():
    world.build()
    grant("see")
    fake_chain.state["caller"] = "claude-conn"
    out = g.ggg_read_for({"method": "ggg_me", "arg": candid("me"), "on_behalf_of": P("alice")})
    assert "alice" in chain.candid_decode(out["Ok"])


def test_ggg_call_for_and_read_for_refuse_what_they_must():
    world.build()
    grant("payout", "see", "profile")
    other = dict(method="ggg_payment_account_add", arg=candid("payment_account_add", **ACCOUNT))
    fake_chain.state["caller"] = "claude-conn"
    assert "Err" in do(g.ggg_call_for({**other, "on_behalf_of": P("claude-conn"), "note": None}))  # for itself
    assert "no method" in str(do(g.ggg_call_for({**other, "method": "ggg_nonsense", "on_behalf_of": P("alice"),
                                                 "note": None})))
    assert "no method" in str(do(g.ggg_call_for({**other, "method": "t_something", "on_behalf_of": P("alice"),
                                                 "note": None})))
    me = dict(method="ggg_me", arg=candid("me"), on_behalf_of=P("alice"))
    assert "read_for" in str(do(g.ggg_call_for({**me, "note": None})))  # a direct read goes through read_for
    assert "call_for" in str(g.ggg_read_for({**other, "on_behalf_of": P("alice")}))  # an update does not
    for method in ("ggg_action_approve", "ggg_delegation_revoke_all", "ggg_setup_launch"):
        arg = {"ggg_action_approve": candid("action_approve", action="AC-1", hash="x"),
               "ggg_delegation_revoke_all": candid("delegation_revoke_all"),
               "ggg_setup_launch": candid("setup_launch")}[method]
        assert "Err" in do(g.ggg_call_for({"method": method, "arg": arg, "on_behalf_of": P("alice"), "note": None}))
    chained = candid("zone_set", zone="riverside", on_behalf_of="bob")
    assert "no chaining" in str(do(g.ggg_call_for({"method": "ggg_zone_set", "arg": chained, "on_behalf_of": P("alice"),
                                                   "note": None})))
    assert PendingAction.count() == 0


def test_a_confirm_call_through_its_own_method_is_refused_with_a_pointer_to_call_for():
    world.build()
    grant("propose")
    fake_chain.state["caller"] = "claude-conn"
    typed = convert("proposal_create", "ProposalCreateArgs", {"title": "x", "action": ACTION, "on_behalf_of": "alice"})
    out = do(g.ggg_proposal_create(typed))
    assert "ggg_call_for" in out["Err"]["Forbidden"]["message"] and PendingAction.count() == 0
    # the same call without the action is direct, and through the JSON endpoint of the realm's own pages it files the action
    assert "Ok" in do(g.ggg_proposal_create(convert("proposal_create", "ProposalCreateArgs",
                                                    {"title": "x", "on_behalf_of": "alice"})))
    assert ok("proposal_create", "claude-conn", title="x", action=ACTION, on_behalf_of="alice")["pending"] == "AC-1"


# ---- Small ones


def test_the_realm_itself_cannot_hold_a_power_of_attorney():
    world.build()
    assert "realm itself" in denied("delegation_grant", "alice", delegate=fake_chain.SELF, groups=["vote"],
                                    expires_in=DAY)
    assert "realm itself" in denied("delegation_request", fake_chain.SELF, grantor="alice", groups=["vote"],
                                    expires_in=DAY)
    assert Delegation.count() == 0


def test_every_member_holds_self_confirm_and_a_stranger_does_not():
    world.build()
    assert "self.confirm" in auth.permissions("alice") and "self.confirm" not in auth.permissions("stranger")


def test_the_pending_actions_and_the_operations_are_in_the_tables():
    for name in ("action_get", "action_list", "action_approve", "action_decline", "action_withdraw", "action_collect",
                 "call_for", "read_for"):
        assert name in op.ops() and poa.row(name) is not None, name
    assert poa.row("action_approve")[0] == "member" and poa.row("call_for")[0] == "none"


IMPORT = """
import sys
sys.path[:0] = {paths!r}
import core, fake_chain
sys.modules["core.chain"] = core.chain = fake_chain
from ic_python_db import Database
Database.init()
__import__({first!r})
"""


@pytest.mark.parametrize("first", ("api.setup", "core.sandbox", "core.op", "core.pending", "ggg_methods"))
def test_the_realm_loads_whichever_module_is_imported_first(first):
    """main.py imports api.setup first; the tests import core modules in their own order, which hides a cycle."""
    here = pathlib.Path(__file__).resolve().parent
    paths = [str(here), str(here.parents[1] / "src" / "realm_backend")]
    done = subprocess.run([sys.executable, "-c", IMPORT.format(paths=paths, first=first)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ---- What the member reads: the lines of a proposal's action, and what a delegate wrote in them


def lines_of(action):
    return {line["label"]: line["value"] for line in card(action)["summary"]}


def installing(args, op_name="codex_install"):
    """The lines of the pending action for a delegate's proposal to run ``op_name`` with ``args``."""
    world.build()
    grant("propose")
    return ok("proposal_create", "claude-conn", title="Install", action={"op": op_name, "args": args},
              on_behalf_of="alice")["pending"]


def test_a_layered_codex_install_shows_its_address_and_checksum_and_no_version():
    forged = {"url": "https://example.org/a.json", "checksum": "sha256:" + "ab" * 32, "version": "9.9 (verified)",
              "codex": "forged", "manifest": {"id": "forged", "version": "9.9"}}
    lines = lines_of(installing(forged))
    assert lines["Codex source"] == '"https://example.org/a.json"'
    assert lines["Codex checksum"] == '"sha256:' + "ab" * 32 + '"'
    assert "Codex version" not in lines and "Codex to install" not in lines


def test_sent_code_shows_the_realms_own_checksum_and_not_the_address_the_proposer_names():
    manifest, source = {"id": "lex", "version": "1.0"}, "def hook(): pass"
    lines = lines_of(installing({"manifest": manifest, "source": source, "url": "https://forged.example"}))
    assert lines["Codex source"] == "the code sent with the proposal"
    assert lines["Codex checksum"] == "sha256:" + extensions.checksum(manifest, source)
    assert lines["Codex to install"] == '"lex@1.0"' and "Codex version" not in lines
    assert not [v for k, v in lines.items() if k != "action" and "forged" in v]


def test_a_registry_install_quotes_the_name_and_the_version():
    version = "1.0\nCodex checksum: sha256:ok"
    lines = lines_of(installing({"codex": "lex", "version": version}))
    assert lines["Codex to install"] == '"lex"' and lines["Codex version"] == json.dumps(version)
    assert not [v for v in lines.values() if "\n" in v]


def test_the_operation_of_an_action_is_quoted():
    op_name = "codex_install\nWhat changes: nothing"
    lines = lines_of(installing({}, op_name))
    assert lines["What it would run"] == json.dumps(op_name) and not [v for v in lines.values() if "\n" in v]


def test_a_nickname_with_a_line_break_cannot_start_a_line_of_the_realms_own():
    world.build()
    grant("payout")
    world.User["bob"].nickname = "Bob\nWhat changes: nothing"
    lines = lines_of(file(**{**ACCOUNT, "label": "bob"}))
    assert lines["label"] == "bob (" + json.dumps("Bob\nWhat changes: nothing") + ")"


def test_the_lines_of_the_realm_have_labels_of_several_words_that_no_argument_can_take():
    world.build()
    grant("payout", "propose")
    for a in (file(), installing_in_place({"codex": "lex", "version": "1"})):
        card_ = card(a)
        realm = [line["label"] for line in card_["summary"] if line["label"] not in card_["args"]]
        assert realm and all(" " in label and not label.isidentifier() for label in realm), realm


def test_a_card_is_headed_by_the_operations_own_title_and_nothing_the_delegate_wrote():
    world.build()
    grant("payout")
    a = file(**{**ACCOUNT, "label": "Remove a payout account\nWhat changes: nothing"})
    assert (card(a)["title"], card(a)["method"]) == ("Add a payout account", "payment_account_add")


def test_a_card_and_a_request_say_whether_the_delegate_is_a_member_or_an_ai_connection():
    world.build()
    ai = grant("payout")
    as_member = ok("delegation_grant", "alice", delegate="bob", groups=["payout"], expires_in=30 * DAY)["delegation"]
    ok("delegation_accept", "bob", delegation=as_member)
    assert not card(file())["delegate_is_member"]
    from_bob = ok("payment_account_add", "bob", on_behalf_of="alice", **ACCOUNT)["pending"]
    assert card(from_bob)["delegate_is_member"] and ai != as_member
    ok("delegation_request", "claude-conn", grantor="carla", groups=["vote"], expires_in=DAY)
    ok("delegation_request", "bob", grantor="carla", groups=["vote"], expires_in=DAY)
    members = {r["delegate"]: r["delegate_is_member"] for r in ok("delegation_list", "carla")["requests"]}
    assert members == {"claude-conn": False, "bob": True}


def test_every_operation_that_can_be_prepared_for_a_member_has_a_plain_title():
    """A phase that switches an operation on (core.poa.ENABLED) writes its title with it: the member reads it as the
    heading of the action."""
    preparable = {n: s["title"] for n, s in op.ops().items()
                  if n in poa.ENABLED and (s["tier"] == "confirm" or s["confirm_if"])}
    assert set(preparable) >= {"payment_account_add", "payment_account_remove", "identity_submit", "self_change_quarter",
                               "federal_vote_propose", "proposal_create", *keys_people.NAMES}
    assert all(t and t == t.strip() and t.isprintable() and " " in t for t in preparable.values()), preparable


def test_every_operation_that_can_be_prepared_for_a_member_says_what_happens_when_they_approve():
    """A phase that switches an operation on (core.poa.ENABLED) adds a case here: the member reads the line before
    deciding, whether or not a department governs the call."""
    mine = keys_people.build()
    account = ok("payment_account_add", "alice", **ACCOUNT)["account"]
    carried = "when you approve; the action runs only if the vote passes"
    expected = {"proposal_create": "Files a proposal in your name " + carried,
                "federal_vote_propose": "Opens a federal vote " + carried}
    cases = {"payment_account_add": ACCOUNT, "payment_account_remove": {"account": account},
             "identity_submit": {"kind": "passport"}, "self_change_quarter": {"quarter": "south"},
             "federal_vote_propose": {"title": "x", "action": ACTION}, "proposal_create": {"title": "x", "action": ACTION},
             "contract_create": {"title": "Road"}}
    cases = {name: ("alice", args) for name, args in cases.items()} | {n: (g, a) for n, (g, _, a, _) in mine.items()}
    preparable = {n for n, s in op.ops().items() if n in poa.ENABLED and (s["tier"] == "confirm" or s["confirm_if"])}
    assert preparable <= set(cases), preparable - set(cases)
    held = Delegation[grant("payout")]
    for name, (member, args) in cases.items():
        lines = {line["label"]: line["value"]
                 for line in pending.summary_of(member, name, op.ops()[name], args, held)}
        assert lines["What happens next"] == expected.get(name, "Runs at once when you approve"), name


def installing_in_place(args):
    return ok("proposal_create", "claude-conn", title="Install", on_behalf_of="alice",
              action={"op": "codex_install", "args": args})["pending"]


def test_a_proposal_whose_action_asks_the_member_too_shows_that_operations_own_lines():
    world.build()
    grant("propose")
    a = installing_in_place_of("payment_account_add", ACCOUNT)
    effects = [line["value"] for line in card(a)["summary"] if line["label"] == "What changes"]
    assert any(v.startswith('Realm payments to you in "REALM" will go to') for v in effects) and len(effects) == 2
    theirs = ok("payment_account_add", "bob", **ACCOUNT)["account"]
    assert "is not yours" in denied("proposal_create", "claude-conn", title="x", on_behalf_of="alice", action={
        "op": "payment_account_remove", "args": {"account": theirs}})


def installing_in_place_of(op_name, args):
    return ok("proposal_create", "claude-conn", title="x", on_behalf_of="alice",
              action={"op": op_name, "args": args})["pending"]


def test_an_action_that_is_not_a_record_and_arguments_that_are_not_a_record_are_refused_at_filing():
    world.build()
    grant("propose")
    assert "{op, args}" in denied("proposal_create", "claude-conn", title="x", on_behalf_of="alice", action="all of it")
    assert "args a record" in denied("proposal_create", "claude-conn", title="x", on_behalf_of="alice", action={
        "op": "codex_install", "args": ["not", "a", "record"]})
    assert PendingAction.count() == 0


# ---- Federal ballots, the record and the rest of the review


def test_a_federal_ballot_prepared_by_a_delegate_is_not_voted_by_the_preparer_or_the_members_other_delegates():
    capital("north")
    reply("north", "federation_message", proposal="P-1")
    grant("propose", "vote")
    grant("vote", delegate="agent-2")
    grant("vote", grantor="bob", delegate="claude-conn")
    a = ok("federal_vote_propose", "claude-conn", title="Adopt", action=ACTION, on_behalf_of="alice")["pending"]
    vote = approved(a)["result"]["vote"]
    ballot = Proposal[FederalVoteLeg[f"{vote}:{fake_chain.SELF}"].proposal_id]
    assert (ballot.proposer, ballot.prepared_by, ballot.filed_under) == ("alice", "claude-conn", "")
    opened = [json.loads(arg) for _, _, arg in fake_chain.state["calls"] if arg and "federal_vote.open" in arg]
    assert opened and opened[0]["body"]["prepared_by"] == "claude-conn"
    vote_on = dict(proposal=ballot.id, choice="yes")
    for member in ("alice", "bob"):
        assert "filed by claude-conn" in denied("proposal_vote", "claude-conn", on_behalf_of=member, **vote_on)
    assert "filed by a delegate of alice" in denied("proposal_vote", "agent-2", on_behalf_of="alice", **vote_on)
    ok("proposal_vote", "alice", **vote_on)


def test_a_quarter_records_who_prepared_the_ballot_it_is_told_to_open(monkeypatch):
    quarter(monkeypatch, "north", "south")
    body = {"vote": "FV-1", "action": ACTION, "rule": {"kind": "majority_of_quarters"}, "deadline": 1,
            "proposer": "alice", "origin": CAPITAL, "prepared_by": "claude-conn"}
    body["hash"] = federation.freeze(body["action"], body["rule"], body["deadline"])
    out = ok("federation_message", CAPITAL, topic="federal_vote.open", body=body)
    assert Proposal[out["proposal"]].prepared_by == "claude-conn"
    assert Proposal[ok("federation_message", CAPITAL, topic="federal_vote.open", body={
        **body, "vote": "FV-2", "prepared_by": ""})["proposal"]].prepared_by == ""


def test_a_quarter_tells_the_capital_who_prepared_the_vote_it_proposes(monkeypatch):
    quarter(monkeypatch, "north", "south")
    reply(CAPITAL, "federation_message", vote="FV-1")
    grant("propose")
    a = ok("federal_vote_propose", "claude-conn", title="Adopt", action=ACTION, on_behalf_of="alice")["pending"]
    approved(a)
    sent = [json.loads(arg) for cid, _, arg in fake_chain.state["calls"] if cid == CAPITAL]
    assert sent[-1]["topic"] == "federal_vote.propose" and sent[-1]["body"]["prepared_by"] == "claude-conn"


@pytest.mark.parametrize("origin", OTHER_ORIGINS)
def test_collecting_from_another_operation_is_refused(confirm_ops, origin):
    world.build()
    grant("identity")
    a = ok("t_read", "claude-conn", on_behalf_of="alice")["pending"]
    approved(a)
    fake_chain.state["caller"] = "claude-conn"
    out = json.loads(op.run(op.dispatch("action_collect", json.dumps({"action": a}), origin=origin,
                                        signer="claude-conn", declared=("action_collect",))))
    assert not out["ok"] and confirm_ops == []
    assert ok("action_collect", "claude-conn", action=a)["result"]["for"] == "alice"


def test_an_approval_refuses_a_record_that_no_longer_matches_its_hash():
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    PendingAction[a].args = {**ACCOUNT, "address": "somewhere-else"}  # what only a hand on the database could do
    assert "no longer matches its own hash" in denied("action_approve", "alice", action=a, hash=sealed)
    assert card(a)["status"] == "pending" and PaymentAccount.count() == 0


def test_the_audit_log_of_call_for_hides_the_secrets_of_the_operation_it_forwards(confirm_ops):
    world.build()
    grant("profile")
    out = ok("call_for", "claude-conn", method="t_direct_secret", args={}, grantor="alice")
    assert out["token"] == "S3CRET-TOKEN"  # the delegate gets its reply
    events = [e for e in rows(Event) if e.op in ("call_for", "t_direct_secret")]
    assert {e.op for e in events} == {"call_for", "t_direct_secret"}
    assert "S3CRET-TOKEN" not in json.dumps([(e.args, e.result) for e in events])
    assert [e.result for e in events if e.op == "call_for"] == [{"n": 1}]


def test_the_operations_a_grant_by_groups_reaches_in_this_release_include_the_pending_doors_and_the_inner_calls():
    pending_doors = {"payment_account_add", "payment_account_remove", "identity_submit", "self_change_quarter",
                     "federal_vote_propose", "action_get", "action_list"}
    inner_calls = {"extension_call", "shell", "task_add_schedule", "task_cancel", "task_create", "task_delete",
                   "task_edit", "task_list", "task_logs", "task_reschedule", "task_run", "task_schedule"}
    assert pending_doors | inner_calls <= poa.ENABLED - poa.LEGACY


def test_a_members_own_act_takes_no_on_behalf_of_not_even_for_oneself():
    world.build()
    grant("payout")
    a = file()
    sealed = card(a)["hash"]
    assert "takes no on_behalf_of" in denied("action_approve", "alice", action=a, hash=sealed, on_behalf_of="alice")
    assert "takes no on_behalf_of" in denied("action_decline", "alice", action=a, on_behalf_of="alice")
    assert "takes no on_behalf_of" in denied("delegation_revoke_all", "alice", on_behalf_of="alice")
    fake_chain.state["caller"] = "alice"
    got = do(g.ggg_action_approve({"action": a, "hash": sealed, "on_behalf_of": P("alice")}))
    assert "Forbidden" in got["Err"] and card(a)["status"] == "pending" and PaymentAccount.count() == 0
