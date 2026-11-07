"""Proposal links on the real wasm (ggg#4, realms-gos#515): filed, read from the other side, refused, through the
realm's operations and through GGG's Candid methods, which are what a GGG caller uses."""

import pytest
from ic.candid import Types, decode, encode
from ic.principal import Principal
from pic import Canister

LINK = Types.Record({"kind": Types.Variant({"Replaces": Types.Null, "RelatesTo": Types.Null}), "proposal": Types.Text})
GGG_ERROR = Types.Variant({
    "NotFound": Types.Null, "Unauthenticated": Types.Null,
    "Forbidden": Types.Record({"permission": Types.Opt(Types.Text), "message": Types.Text}),
    "InvalidArgument": Types.Text, "Conflict": Types.Text, "Unavailable": Types.Text,
    "GenericError": Types.Record({"error_code": Types.Nat, "message": Types.Text})})
CREATE_ARGS = Types.Record({
    "title": Types.Text, "description": Types.Text, "department": Types.Opt(Types.Text),
    "action": Types.Variant({"Motion": Types.Null}), "effective_at": Types.Opt(Types.Nat64),
    "links": Types.Opt(Types.Vec(LINK)), "on_behalf_of": Types.Opt(Types.Principal)})
GET_ARGS = Types.Record({"id": Types.Text, "cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32)})
PAGE = Types.Record({"items": Types.Vec(LINK), "next": Types.Opt(Types.Text)})


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def ggg(realm, method, arg_type, arg, reply_type, who, query):
    """A GGG method called with Candid, as a GGG caller does; returns the decoded ``Ok`` or ``Err``."""
    realm.pic.set_sender(Principal.from_str(who))
    call = realm.pic.query_call if query else realm.pic.update_call
    reply = decode(bytes(call(realm.id, method, encode([{"type": arg_type, "value": arg}]))),
                   [Types.Variant({"Ok": reply_type, "Err": GGG_ERROR})])[0]["value"]
    return reply


def create(realm, who, links=None):
    arg = {"title": "x", "description": "", "department": [], "action": {"Motion": None}, "effective_at": [],
           "links": [] if links is None else [links], "on_behalf_of": []}
    return ggg(realm, "ggg_proposal_create", CREATE_ARGS, arg, Types.Text, who, query=False)


def linked_from(realm, who, proposal, cursor=None, limit=None):
    arg = {"id": proposal, "cursor": [] if cursor is None else [cursor], "limit": [] if limit is None else [limit]}
    return ggg(realm, "ggg_proposal_linked_from", GET_ARGS, arg, Types.Opt(PAGE), who, query=True)


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    realm = Canister(wasm_of("production"), installer=people["installer"])
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    for member in ("alice", "bob"):
        ok(realm, "realm_join", people[member])
    return realm


def test_a_proposal_replaces_a_withdrawn_one_through_candid_and_the_old_one_points_forward(realm, people):
    alice, bob = people["alice"], people["bob"]
    old = create(realm, alice)["Ok"]
    ok(realm, "proposal_withdraw", alice, proposal=old)
    related = create(realm, bob)["Ok"]
    new = create(realm, alice, [{"kind": {"Replaces": None}, "proposal": old},
                                {"kind": {"RelatesTo": None}, "proposal": related}])["Ok"]
    assert ok(realm, "proposal_get", alice, query=True, proposal=old)["proposal"]["replaced_by"] == new
    assert ok(realm, "proposal_get", alice, query=True, proposal=new)["proposal"]["links"] == [
        {"kind": "replaces", "proposal": old}, {"kind": "relates_to", "proposal": related}]
    assert linked_from(realm, alice, old)["Ok"] == [{"items": [{"kind": {"Replaces": None}, "proposal": new}],
                                                       "next": []}]
    assert linked_from(realm, alice, related)["Ok"][0]["items"] == [{"kind": {"RelatesTo": None}, "proposal": new}]


def test_the_checks_hold_on_the_real_wasm(realm, people):
    alice, bob = people["alice"], people["bob"]
    old = create(realm, alice)["Ok"]
    ok(realm, "proposal_withdraw", alice, proposal=old)
    before = ok(realm, "proposal_list", alice, query=True)["proposals"]
    assert "Forbidden" in create(realm, bob, [{"kind": {"Replaces": None}, "proposal": old}])["Err"]
    assert "NotFound" in create(realm, alice, [{"kind": {"RelatesTo": None}, "proposal": "P-999"}])["Err"]
    twice = [{"kind": {"RelatesTo": None}, "proposal": old}] * 2
    assert "InvalidArgument" in create(realm, alice, twice)["Err"]
    assert "InvalidArgument" in create(realm, alice, [{"kind": {"RelatesTo": None}, "proposal": old}] * 17)["Err"]
    live = create(realm, alice)["Ok"]
    assert "Conflict" in create(realm, alice, [{"kind": {"Replaces": None}, "proposal": live}])["Err"]
    made = create(realm, alice, [{"kind": {"Replaces": None}, "proposal": old}])["Ok"]
    assert "Conflict" in create(realm, alice, [{"kind": {"Replaces": None}, "proposal": old}])["Err"]
    after = ok(realm, "proposal_list", alice, query=True)["proposals"]
    assert [p["id"] for p in after] == [p["id"] for p in before] + [live, made]  # a refusal files nothing


def test_the_links_to_a_proposal_are_a_page_at_a_time_oldest_first_and_an_unknown_id_is_none(realm, people):
    alice, bob = people["alice"], people["bob"]
    target = create(realm, alice)["Ok"]
    linkers = [create(realm, bob, [{"kind": {"RelatesTo": None}, "proposal": target}])["Ok"] for _ in range(5)]
    seen, cursor = [], None
    while True:
        page = linked_from(realm, alice, target, cursor, limit=2)["Ok"][0]
        assert page["items"] or not page["next"]
        seen += [link["proposal"] for link in page["items"]]
        cursor = page["next"][0] if page["next"] else None
        if cursor is None:
            break
    assert seen == linkers
    assert linked_from(realm, alice, "P-999") == {"Ok": []}
