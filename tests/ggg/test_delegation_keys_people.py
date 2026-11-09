"""Power of attorney for keys, joining, power over people and power of attorney onward (phase 7c, realms-gos#633): each
of these operations is prepared by a delegate under a group grant and runs only on the member's approval, says in the
realm's words what changes hands, and an old grant still does not reach it."""
import json

import pytest

import fake_chain
import world
from core import op, pending, poa
from ggg import Delegation, Department, Event, KeyEnvelope, Notification, PendingAction, Position, User
from keys_people import DAY, DERIVED, NAMES, SCOPE, build
from kit import denied, ok, rows
from test_crypto import REPORT
from test_delegation_affairs import lend
from test_pending_actions import approved, card

KEY_WARNING = "This gives {name} a permanent copy of your key. Revoking power of attorney does not take it back."


def lent(*names, delegate="claude-conn"):
    """A realm in which ``delegate`` holds, from each member that the cases of ``names`` are for, a grant of the groups
    they name; the cases."""
    cases = build()
    groups = {}
    for name in names:
        grantor, group, _, _ = cases[name]
        groups.setdefault(grantor, []).append(group)
    for grantor, named in groups.items():
        lend(grantor, *dict.fromkeys(named), delegate=delegate)
    return cases


def prepare(name, case, delegate="claude-conn", **changes):
    """The delegate prepares the call of ``case`` for the member, with ``changes`` to its arguments: the action's id."""
    grantor, _, args, _ = case
    out = ok(name, delegate, on_behalf_of=grantor, **{**args, **changes})
    assert out["pending"].startswith("AC-")
    return out["pending"]


def lines_of(action, delegate="claude-conn"):
    """What the realm says about the action, as its delegate reads it: the grantor's card says the same."""
    return {line["label"]: line["value"] for line in card(action, delegate)["summary"]}


def prepared(name):
    """A realm with the case of ``name`` and the grant it needs; the action the delegate prepared for it."""
    return prepare(name, lent(name)[name])


# ---- Each asks the member, and runs on the member's approval alone


@pytest.mark.parametrize("name", NAMES)
def test_a_delegate_prepares_it_and_the_members_approval_runs_it(name):
    case = lent(name)[name]
    grantor, _, _, ran = case
    a = prepare(name, case)
    assert not ran() and PendingAction[a].status == "pending" and card(a, grantor)["method"] == name
    done = approved(a, grantor)
    assert done["status"] == "done" and ran(), done
    (event,) = [e for e in rows(Event) if e.origin == "confirmed" and e.op == name]
    assert (event.user, event.by, event.prepared_by) == (grantor, grantor, "claude-conn")


@pytest.mark.parametrize("name", NAMES)
def test_under_a_grant_made_before_groups_each_is_refused_as_before(name):
    grantor, _, args, ran = build()[name]
    Delegation(id="D-old", grantor=grantor, delegate="old-agent", status="active", expires_at=fake_chain.now() + DAY,
               scope={"operations": [op.OPS[name]["perm"]]})
    assert "does not cover" in denied(name, "old-agent", on_behalf_of=grantor, **args)
    assert PendingAction.count() == 0 and not ran()


@pytest.mark.parametrize("name", NAMES)
def test_a_group_that_does_not_name_it_cannot_prepare_it(name):
    grantor, _, args, ran = build()[name]
    lend(grantor, "vote")
    assert "does not cover" in denied(name, "claude-conn", on_behalf_of=grantor, **args)
    assert PendingAction.count() == 0 and not ran()


def test_what_a_delegate_reads_or_only_removes_power_runs_at_once():
    build()
    fake_chain.state["replies"][("aaaaa-aa", "vetkd_public_key")] = '(record { 2_345 = blob "\\0a" })'
    lend("alice", "keys", "poa")
    lend("carla", "office:Health", "see")
    on = dict(on_behalf_of="alice")
    assert ok("crypto_public_key", "claude-conn", **on)["public_key"] == "0a"
    assert ok("crypto_envelope", "claude-conn", scope="user:alice:old", **on)["wrapped_dek"] == "env:a"
    assert ok("crypto_scopes", "claude-conn", **on)["scopes"] == ["user:alice:old"]
    assert ok("crypto_access", "claude-conn", scope="user:alice:old", **on)["principals"] == ["alice", "bob"]
    listed = ok("document_list", "claude-conn", department="Health", on_behalf_of="carla")["documents"]
    got = ok("document_get", "claude-conn", document=listed[0]["id"], on_behalf_of="carla")
    assert got["wrapped_dek"] == "env:carla"
    # Ending a grant only removes power, and declining a request is no grant at all.
    request = ok("delegation_request", "bob", grantor="alice", groups=["vote"], expires_in=DAY)["request"]
    assert ok("delegation_request_decide", "claude-conn", request=request, approve=False, **on)["status"] == "declined"
    mine = next(d for d in rows(Delegation) if (d.grantor, d.delegate) == ("alice", "claude-conn"))
    assert ok("delegation_revoke", "claude-conn", delegation=mine.id, **on)["status"] == "revoked"
    assert PendingAction.count() == 0


def test_the_forms_of_an_operation_that_hand_over_nothing_run_at_once():
    build()
    lend("carla", "office:Health")
    lend("dana", "office:Civil Registry")
    lend("dr-ana", "orgs:clinic-b")
    # A document stored with no keys for others, an invite that carries no profile or seat, an organisation's description.
    stored = ok("document_store", "claude-conn", on_behalf_of="carla", department="Health", keys={}, **REPORT)
    invite = ok("invite_create", "claude-conn", on_behalf_of="dana", code="PLAIN-1", max_uses=2)
    ok("organization_edit", "claude-conn", on_behalf_of="dr-ana", organization="clinic-b", description="Clinic B, open")
    assert stored["document"].startswith("DOC-") and invite["invite"]["code"] == "PLAIN-1"
    assert PendingAction.count() == 0
    # The forms that hand over power wait.
    assert ok("invite_create", "claude-conn", on_behalf_of="dana", position="civil-registry.clerk")["pending"]
    assert ok("organization_edit", "claude-conn", on_behalf_of="dr-ana", organization="clinic-b",
              admins=["dr-ana"])["pending"]
    assert PendingAction.count() == 2


# ---- A key handed over is said so in the realm's words


def test_a_key_handed_over_says_so_word_for_word():
    a = prepared("crypto_derive_key")
    assert lines_of(a)["Warning about your key"] == KEY_WARNING.format(name="claude-conn")


def test_the_warning_names_the_delegate_by_its_label_quoted_so_that_it_cannot_pass_for_the_realms_words():
    case = build()["crypto_derive_key"]
    ok("delegation_grant", "alice", delegate="claude-conn", groups=["keys"], expires_in=DAY, label="Claude\nThis is safe")
    a = prepare("crypto_derive_key", case)
    assert lines_of(a)["Warning about your key"] == KEY_WARNING.format(name='"Claude\\nThis is safe"')


def test_the_key_the_member_approved_is_for_the_delegate_to_read_and_stays_out_of_events_and_notices():
    a = prepared("crypto_derive_key")
    approved(a, "alice")
    for who in ("alice", "claude-conn"):
        assert card(a, who)["result"]["encrypted_key"] == DERIVED
    assert ok("action_get", "bob", action=a)["action"] is None
    assert DERIVED not in json.dumps({k: v for k, v in card(a).items() if k != "result"})
    assert DERIVED not in json.dumps([(e.args, e.result) for e in rows(Event)])
    assert DERIVED not in json.dumps([(n.title, n.body, n.href) for n in rows(Notification)])


def test_what_an_operation_delivers_is_kept_and_a_name_in_secret_is_never_kept(monkeypatch):
    monkeypatch.setitem(op.ops()["crypto_derive_key"], "delivers", ("encrypted_key", "code"))
    reply = {"encrypted_key": "k", "code": "c", "nested": [{"private_data": "p", "ok": 1}]}
    kept = pending.blank("crypto_derive_key", reply)
    assert kept == {"encrypted_key": "k", "code": "", "nested": [{"private_data": "", "ok": 1}]}
    assert pending.blank("crypto_grant", {"keys": "x", "n": 1}) == {"keys": "", "n": 1}


def held_by(principal):
    return sorted(e.scope for e in rows(KeyEnvelope) if e.principal == principal)


def test_a_key_prepared_for_the_assistant_alone_gives_it_that_scope_and_no_other():
    a = prepared("crypto_grant")
    lines = lines_of(a)
    assert lines["Keys go to"] == "claude-conn"
    assert lines["What changes"] == f'claude-conn will be able to read "{SCOPE}", and nothing else of yours'
    assert held_by("claude-conn") == []
    approved(a, "alice")
    assert held_by("claude-conn") == [SCOPE]


def test_an_ai_connection_reads_back_the_one_envelope_it_was_given_and_nothing_else_of_the_members():
    a = prepare("crypto_grant", lent("crypto_grant")["crypto_grant"])
    approved(a, "alice")
    assert ok("crypto_envelope", "claude-conn", scope=SCOPE)["wrapped_dek"] == "env:c"
    # an envelope it was not given, and the lists of keys that stay a member's to read
    assert "unknown" in denied("crypto_envelope", "claude-conn", scope="user:alice:old")
    assert "needs self.data_manage" in denied("crypto_scopes", "claude-conn")
    assert "needs self.data_manage" in denied("crypto_access", "claude-conn", scope=SCOPE)
    assert "needs self.data_manage" in denied("crypto_grant", "claude-conn", scope=SCOPE, keys={"claude-conn": "x"})


def test_an_ai_connection_reads_a_document_whose_key_it_was_given_and_no_other():
    build()
    lend("carla", "keys", "office:Health")
    documents = [d["id"] for d in ok("document_list", "carla", department="Health")["documents"]]
    given, other = documents[0], documents[1]
    scope = f"dept:Health:doc:{given}"
    a = ok("crypto_grant", "claude-conn", on_behalf_of="carla", scope=scope, keys={"claude-conn": "env:doc"})["pending"]
    approved(a, "carla")
    got = ok("document_get", "claude-conn", document=given)
    assert got["wrapped_dek"] == "env:doc" and got["document"]["ref"] == REPORT["ref"]
    assert "not shared with you" in denied("document_get", "claude-conn", document=other)


def test_a_member_who_is_a_delegate_reads_the_scope_it_was_given_and_no_other():
    case = build()["crypto_grant"]
    held = lend("alice", "keys", delegate="dana")
    ok("delegation_accept", "dana", delegation=held)
    a = prepare("crypto_grant", case, delegate="dana", keys={"dana": "env:d"})
    assert lines_of(a, "dana")["What changes"] == f'dana will be able to read "{SCOPE}", and nothing else of yours'
    approved(a, "alice")
    assert ok("crypto_scopes", "dana")["scopes"] == [SCOPE]
    assert ok("crypto_envelope", "dana", scope=SCOPE)["wrapped_dek"] == "env:d"
    assert "unknown" in denied("crypto_envelope", "dana", scope="user:alice:old")


def test_a_key_for_others_is_not_described_as_the_assistants_own():
    case = lent("crypto_grant")["crypto_grant"]
    a = prepare("crypto_grant", case, keys={"bob": "env:b", "claude-conn": "env:c"})
    assert lines_of(a)["What changes"] == f'bob ("Bob"), claude-conn will be able to read "{SCOPE}"'


def test_a_key_the_member_does_not_manage_or_that_is_no_mapping_is_not_prepared():
    lent("crypto_grant")
    on = dict(on_behalf_of="alice", keys={"x": "y"})
    assert "do not manage" in denied("crypto_grant", "claude-conn", scope="dept:Health:doc:DOC-1", **on)
    assert "keys are principals" in denied("crypto_grant", "claude-conn", on_behalf_of="alice", scope=SCOPE, keys="x")
    assert PendingAction.count() == 0


def test_taking_keys_away_says_who_loses_access_and_if_it_is_you():
    cases = lent("crypto_revoke")
    a = prepare("crypto_revoke", cases["crypto_revoke"])
    assert lines_of(a)["Loses access"] == 'bob ("Bob")'
    assert lines_of(a)["What changes"] == 'bob ("Bob") lose their key to "user:alice:old"'
    mine = prepare("crypto_revoke", cases["crypto_revoke"], principals=["alice"])
    assert lines_of(mine)["What changes"].endswith(", and so do you")


def test_taking_a_documents_keys_away_and_deleting_it_name_the_document_and_who_loses_it():
    cases = lent("document_revoke", "document_delete")
    revoking = lines_of(prepare("document_revoke", cases["document_revoke"]))
    assert revoking["Document"].startswith("DOC-2 ") and revoking["Loses access"] == 'dr-ana ("Dr-Ana")'
    assert revoking["What changes"] == 'dr-ana ("Dr-Ana") can no longer open DOC-2'
    deleting = lines_of(prepare("document_delete", cases["document_delete"]))
    assert deleting["Document"].startswith("DOC-3 ") and "This cannot be undone" in deleting["What changes"]


def test_sharing_and_storing_with_keys_say_who_can_read_afterwards():
    cases = lent("document_share", "document_store")
    sharing = lines_of(prepare("document_share", cases["document_share"]))
    assert sharing["Shared with"] == 'User bob ("Bob")' and sharing["Keys go to"] == 'bob ("Bob")'
    assert sharing["What changes"] == 'bob ("Bob") will be able to read DOC-1'
    storing = lines_of(prepare("document_store", cases["document_store"]))
    assert storing["What changes"] == 'Stores a new document, and bob ("Bob") will be able to read it'


def test_a_document_of_another_department_is_not_prepared():
    build()
    world.seat("finance.clerk", "bob", Department["Finance"], "document.manage")
    lend("bob", "office:Finance")
    assert "Health's staff" in denied("document_share", "claude-conn", on_behalf_of="bob", document="DOC-1",
                                      recipient="User:bob", keys={"bob": "x"})
    assert PendingAction.count() == 0


def test_publishing_says_it_is_public_and_cannot_be_taken_back():
    effect = lines_of(prepared("document_publish"))["What changes"]
    assert effect.endswith("cannot be taken back") and "anyone, signed in or not" in effect


def test_replacing_private_data_says_so():
    assert "what is stored now is lost" in lines_of(prepared("private_data_update"))["What changes"]


# ---- Joining


def test_joining_with_a_code_says_what_the_code_carries_and_that_it_is_consent():
    world.build()
    world.profile("reader", "user.view")
    Position["civil-registry.clerk"].headcount = 2  # the seat Bob holds has room for one more
    ok("invite_create", "dana", profile="reader", position="civil-registry.clerk", code="SEAT-1")
    lend("alice", "membership")
    a = ok("realm_join", "claude-conn", on_behalf_of="alice", code="SEAT-1")["pending"]
    lines = lines_of(a)
    assert lines["Profile you take"] == '"reader"' and lines["Seat you take"] == '"clerk" of Civil Registry'
    assert lines["What changes"] == ("You join the realm with the profile and seat of the code, which is your consent "
                                     "to its terms")
    done = approved(a)
    assert done["status"] == "done", done["result"]
    assert "reader" in [p.id for p in User["alice"].profiles]


def test_a_code_that_is_not_valid_is_not_prepared():
    world.build()
    lend("alice", "membership")
    assert "not valid" in denied("realm_join", "claude-conn", on_behalf_of="alice", code="NOPE")
    assert PendingAction.count() == 0


# ---- Power over people


def test_an_invite_that_carries_a_profile_or_a_seat_says_so_and_that_the_code_is_shown_once():
    world.build()
    world.profile("reader", "user.view")
    lend("dana", "office:Civil Registry")
    a = ok("invite_create", "claude-conn", on_behalf_of="dana", profile="reader", position="civil-registry.clerk",
           max_uses=2, expires_in=DAY)["pending"]
    lines = lines_of(a)
    assert lines["Profile it carries"] == '"reader"' and lines["Seat it carries"] == '"clerk" of Civil Registry'
    assert lines["What changes"] == (
        f"Whoever holds the code can use it 2 times to take the profile and seat above, until "
        f"{pending.moment(fake_chain.now() + DAY)}. You are shown the code once, when you approve")
    assert approved(a, "dana")["result"]["invite"]["code"]  # the member reads the code in the reply to their approval
    assert card(a, "claude-conn")["result"]["invite"]["code"] == ""  # the delegate never does


def test_seating_someone_for_an_organisation_says_who_acts_for_it():
    lines = lines_of(prepared("representative_add"))
    assert lines["Person seated"] == 'bob ("Bob")' and lines["Seat to fill"] == 'a new seat "Nurse"'
    assert lines["What changes"] == 'bob ("Bob") will act for "clinic-b" in a new seat "Nurse"'


def test_changing_the_admins_of_an_organisation_says_the_admins_before_and_after():
    lines = lines_of(prepared("organization_edit"))
    assert lines["Admins now"] == 'dr-ana ("Dr-Ana")' and lines["Admins after"] == 'bob ("Bob"), dr-ana ("Dr-Ana")'
    assert 'can edit "clinic-b" and seat people to act for it' in lines["What changes"]


# ---- Power of attorney onward


def test_granting_onward_names_the_person_the_groups_by_label_and_the_expiry():
    lines = lines_of(prepared("delegation_grant"))
    until = pending.moment(fake_chain.now() + DAY)
    assert lines["Power goes to"] == 'bob ("Bob"), a member; the grant waits until they accept it'
    assert lines["Groups"] == poa.GROUPS["vote"][0] and lines["Lasts until"] == until
    assert lines["What changes"].startswith(f'bob ("Bob") can act for you in Vote for me until {until}')


def test_granting_to_an_ai_connection_and_for_everything_is_said_plainly():
    build()
    lend("alice", "poa")
    a = ok("delegation_grant", "claude-conn", on_behalf_of="alice", delegate="agent-2", groups=["*"],
           expires_in=DAY)["pending"]
    assert lines_of(a)["Power goes to"] == "agent-2, an AI connection"
    assert lines_of(a)["Groups"] == "Everything you can do in this realm, now and later"


def test_accepting_says_you_become_the_grantors_attorney():
    lines = lines_of(prepared("delegation_accept"))
    assert lines["Granted by"] == 'dana ("Dana")'
    assert lines["What changes"].startswith('You become dana ("Dana")\'s attorney for Vote for me, until ')


def test_a_grant_that_cannot_be_made_or_accepted_is_not_prepared():
    lent("delegation_grant")
    on = dict(on_behalf_of="alice")
    ask = dict(expires_in=DAY, **on)
    assert "cannot delegate to yourself" in denied("delegation_grant", "claude-conn", delegate="alice",
                                                   groups=["vote"], **ask)
    assert "not a group" in denied("delegation_grant", "claude-conn", delegate="bob", groups=["nonsense"], **ask)
    assert "D-9" in denied("delegation_accept", "claude-conn", delegation="D-9", **on)
    assert PendingAction.count() == 0


def test_approving_a_request_says_what_it_grants():
    lines = lines_of(prepared("delegation_request_decide"))
    assert lines["Request from"] == 'bob ("Bob"), a member' and lines["Groups"] == poa.GROUPS["vote"][0]
    assert "Your present grant" not in lines["What changes"]


def test_approving_a_request_that_replaces_the_grant_it_was_prepared_under_runs_and_voids_the_rest():
    case = lent("private_data_update", "delegation_grant")["private_data_update"]
    held = next(d.id for d in rows(Delegation) if (d.grantor, d.delegate) == ("alice", "claude-conn"))
    request = ok("delegation_request", "claude-conn", grantor="alice", groups=["vote"], expires_in=7 * DAY)["request"]
    replacing = ok("delegation_request_decide", "claude-conn", on_behalf_of="alice", request=request,
                   approve=True)["pending"]
    other = prepare("private_data_update", case)
    assert lines_of(replacing)["What changes"].endswith(
        f"Your present grant {held} to them ends and this one takes its place; other actions waiting under it are dropped")
    done = approved(replacing)
    assert done["status"] == "done"
    new = done["result"]["delegation"]
    assert Delegation[held].status == "revoked" and Delegation[new].status == "active"
    assert Delegation[new].scope == {"groups": ["vote"]}
    assert card(other)["status"] == "void"
    assert "is void" in denied("action_approve", "alice", action=other, hash=card(other)["hash"])


# ---- Which code the realm trusts


def test_deciding_a_casals_action_says_it_takes_effect_at_once_and_that_the_upgrade_is_read_in_casals():
    effect = lines_of(prepared("orchestration_decide"))["What changes"]
    assert effect.startswith('Approves Casals action "7".') and "takes effect at once" in effect
    assert "read it in Casals before you approve" in effect
    rejecting = ok("orchestration_decide", "claude-conn", on_behalf_of="ctl", action_id=8, decision="reject")["pending"]
    assert lines_of(rejecting)["What changes"] == 'Rejects Casals action "8", which then does not run'
    assert "approve or reject" in denied("orchestration_decide", "claude-conn", on_behalf_of="ctl", action_id=9,
                                         decision="maybe")
