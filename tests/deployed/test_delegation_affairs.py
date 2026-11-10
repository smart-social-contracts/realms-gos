"""Office work under a group grant on the real wasm (realms-gos#626, phase 7a): a member seated in a department lets a
delegate do that office's work and read its inbox, through GGG's Candid methods. The grant names the department
(``group:office:Registry``), the delegate reads the office's inbox and not the member's own, and the member's other
delegate, who holds "See my affairs", reads the member's own inbox and not the office's.
"""

import json

import pytest
from ic.candid import Types, decode, encode
from test_delegation_groups import launch, ok
from test_pending_actions import BLOB, CALL_FOR, CALL_FOR_REPLY, READ_FOR, TEXT, plain

DAY = 86400
NS = 10**9
REF = Types.Record({"kind": TEXT, "id": TEXT})
LIST = Types.Record({"recipient": Types.Opt(REF), "unread": Types.Opt(Types.Bool), "cursor": Types.Opt(TEXT),
                     "limit": Types.Opt(Types.Nat32), "on_behalf_of": Types.Opt(Types.Principal)})
NOTICE = Types.Record({"id": TEXT, "recipient": REF, "topic": TEXT, "title": TEXT, "body": TEXT,
                       "href": Types.Opt(TEXT), "sender": Types.Opt(TEXT), "visibility": TEXT,
                       "read_by": Types.Vec(Types.Principal), "at": Types.Nat64})
NOTICES = Types.Record({"items": Types.Vec(NOTICE), "next": Types.Opt(TEXT)})
GRANT = Types.Record({"delegate": Types.Principal, "scope": Types.Vec(TEXT), "starts_at": Types.Opt(Types.Nat64),
                      "expires_at": Types.Nat64, "label": TEXT, "description": TEXT})
DELEGATION = Types.Record({
    "id": TEXT, "grantor": Types.Principal, "delegate": Types.Principal, "scope": Types.Vec(TEXT),
    "status": Types.Variant({name: Types.Null for name in ("Pending", "Active", "Revoked", "Expired", "Declined")}),
    "label": TEXT, "description": TEXT, "requires_acceptance": Types.Bool, "granted_by": Types.Principal,
    "accepted_at": Types.Opt(Types.Nat64), "starts_at": Types.Opt(Types.Nat64), "expires_at": Types.Nat64,
    "revoked_at": Types.Opt(Types.Nat64), "revoked_by": Types.Opt(Types.Principal)})
LIST_ARGS = Types.Record({"cursor": Types.Opt(TEXT), "limit": Types.Opt(Types.Nat32)})
# The instructions of the office read a delegate makes, with the seat check it meets: about 35M on PocketIC.
OFFICE_READ = """
from core.op import call
out = call("invite_list", {{"on_behalf_of": "{alice}"}}, user="{claude}")
assert out["ok"], out
"""
OFFICE_READ_BUDGET = 100_000_000


def titles(realm, delegate, alice, office=None):
    """What the delegate reads of Alice's own inbox, or of ``office``'s, or the refusal."""
    recipient = [{"kind": "Department", "id": office}] if office else []
    arg = {"recipient": recipient, "unread": [], "cursor": [], "limit": [], "on_behalf_of": [alice]}
    out = realm.ggg("ggg_notification_list", LIST, arg, NOTICES, delegate, query=True)
    return [n["title"] for n in out["Ok"]["items"]] if "Ok" in out else out["Err"]


def invites(realm, delegate, alice):
    """The invites of Alice's office, read by the delegate through ggg_read_for: the Candid reply, or the refusal."""
    arg = encode([{"type": LIST_ARGS, "value": {"cursor": [], "limit": []}}])
    read = {"method": "ggg_invite_list", "on_behalf_of": alice, "arg": arg}
    return realm.ggg("ggg_read_for", READ_FOR, read, BLOB, delegate, query=True)


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A launched realm in which Alice holds a seat in the Registry, with a notice for the Registry and one for her."""
    from pic import Canister

    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    ctl, alice = people["ctl"], people["alice"]
    ok(realm, "department_create", ctl, department="Registry")
    ok(realm, "profile_create", ctl, profile="registry-clerk", allowed_to=["invite.manage"])
    ok(realm, "position_create", ctl, department="Registry", position="registry.clerk", profile="registry-clerk")
    ok(realm, "appoint", ctl, position="registry.clerk", user=alice)
    ok(realm, "notification_send", ctl, recipient="Department:Registry", title="Counter closed on Friday")
    ok(realm, "notification_send", ctl, recipient=f"User:{alice}", title="Your shift")
    return realm


def test_a_member_lends_the_offices_work_through_ggg_delegation_grant(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    arg = {"delegate": claude, "scope": ["group:office:Registry"], "starts_at": [],
           "expires_at": (realm.now() + 30 * DAY) * NS, "label": "", "description": ""}
    out = realm.ggg("ggg_delegation_grant", GRANT, arg, DELEGATION, alice)["Ok"]
    assert out["scope"] == ["group:office:Registry"] and next(iter(out["status"])) == "Active"
    # Bob holds no seat in the Registry, so he cannot name it.
    stranger = realm.ggg("ggg_delegation_grant", GRANT, {**arg, "delegate": people["mallory"]}, DELEGATION,
                         people["bob"])
    assert "Err" in stranger and "you hold no seat in Registry" in json.dumps(stranger, default=str)
    groups = [g["id"] for g in ok(realm, "delegable_operations", alice, query=True)["groups"]]
    assert "office:Registry" in groups and "office" not in groups


def test_the_delegate_reads_the_offices_inbox_and_not_the_members_own(realm, people):
    alice, claude, mallory = people["alice"], people["claude-conn"], people["mallory"]
    ok(realm, "delegation_grant", alice, delegate=mallory, groups=["see"], expires_in=30 * DAY)
    assert titles(realm, claude, alice, "Registry") == ["Counter closed on Friday"]
    assert "does not cover" in str(titles(realm, claude, alice))
    # "See my affairs" reads the member's own inbox, as before, and the office's no more than a stranger does.
    assert titles(realm, mallory, alice) == ["Your shift"]
    assert "does not cover" in str(titles(realm, mallory, alice, "Registry"))


def test_the_delegate_does_the_offices_work_through_ggg_read_for_and_ggg_call_for(realm, people, costs):
    alice, claude, mallory = people["alice"], people["claude-conn"], people["mallory"]
    invite = ok(realm, "invite_create", alice)["invite"]["id"]
    assert decode(bytes(invites(realm, claude, alice)["Ok"]))
    assert "Err" in invites(realm, mallory, alice)  # "See my affairs" is not office work
    revoke = {"method": "ggg_invite_revoke", "on_behalf_of": alice, "note": [],
              "arg": encode([{"type": TEXT, "value": invite}])}
    assert "Err" in realm.ggg("ggg_call_for", CALL_FOR, revoke, CALL_FOR_REPLY, mallory)
    assert not [i for i in ok(realm, "invite_list", alice, query=True)["invites"] if i["revoked"]]
    done = realm.ggg("ggg_call_for", CALL_FOR, revoke, CALL_FOR_REPLY, claude)
    assert "Done" in done["Ok"]
    assert [i["id"] for i in ok(realm, "invite_list", alice, query=True)["invites"] if i["revoked"]] == [invite]
    spent = costs.measure(realm, "office work: a delegate reads the invites of the member's office",
                          OFFICE_READ.format(alice=alice, claude=claude))
    assert spent < OFFICE_READ_BUDGET


def test_the_coverage_ends_with_the_seat(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    registry = ok(realm, "department_get", people["ctl"], query=True, department="Registry")
    ok(realm, "appointment_end", people["ctl"], appointment=registry["positions"][0]["holders"][0]["id"])
    assert "does not cover" in str(titles(realm, claude, alice, "Registry"))
    assert "Err" in invites(realm, claude, alice)


def test_a_contract_is_prepared_with_its_parties_amount_and_term_on_the_real_wasm(realm, people):
    ctl, claude = people["ctl"], people["claude-conn"]
    ok(realm, "delegation_grant", ctl, delegate=claude, groups=["money"], expires_in=30 * DAY)
    prepared = ok(realm, "contract_create", claude, on_behalf_of=ctl, title="Chairs", value=1200, parties=["BuildCo"],
                  ends_at=realm.now() + 30 * DAY)
    lines = {line["label"]: plain(line["value"]) for line in ok(realm, "action_get", ctl, query=True,
                                                               action=prepared["pending"])["action"]["summary"]}
    assert lines["Contract parties"] == '"BuildCo"' and lines["Contract amount"].startswith("1200 ")
    assert lines["Contract term"].startswith("from 2021-") and " until 2021-" in lines["Contract term"]
