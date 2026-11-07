"""Proposal links on the real wasm (ggg#4, realms-gos#515): filed, read from the other side, refused, through the
realm's operations and through GGG's Candid methods, which are what a GGG caller uses."""

import pytest
from ic.candid import Types
from pic import Canister

LINK = Types.Record({"kind": Types.Variant({"Replaces": Types.Null, "RelatesTo": Types.Null}), "proposal": Types.Text})
BASE_ARGS = {
    "title": Types.Text, "description": Types.Text, "department": Types.Opt(Types.Text),
    "action": Types.Variant({"Motion": Types.Null}), "effective_at": Types.Opt(Types.Nat64)}
CREATE_ARGS = Types.Record({**BASE_ARGS, "links": Types.Opt(Types.Vec(LINK)), "on_behalf_of": Types.Opt(Types.Principal)})
# What a caller built before links existed sends: a record without the field.
OLD_CREATE_ARGS = Types.Record({**BASE_ARGS, "on_behalf_of": Types.Opt(Types.Principal)})
GET_ARGS = Types.Record({"id": Types.Text, "cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32)})
PAGE = Types.Record({"items": Types.Vec(LINK), "next": Types.Opt(Types.Text)})
PROPOSAL = Types.Record({
    "id": Types.Text, "title": Types.Text, "description": Types.Text, "proposer": Types.Principal,
    "on_behalf_of": Types.Opt(Types.Principal), "department": Types.Opt(Types.Text),
    "action": Types.Variant({
        "Motion": Types.Null, "Call": Types.Record({"method": Types.Text, "arg": Types.Vec(Types.Nat8)}),
        "Code": Types.Record({"url": Types.Text, "checksum": Types.Text})}),
    "status": Types.Variant({name: Types.Null for name in (
        "Draft", "Voting", "Accepted", "ExecutionScheduled", "Rejected", "Expired", "Executed", "Failed", "Withdrawn")}),
    "opens_at": Types.Opt(Types.Nat64), "deadline": Types.Opt(Types.Nat64), "effective_at": Types.Opt(Types.Nat64),
    "threshold": Types.Nat, "quorum": Types.Nat,
    "tally": Types.Record({"yes": Types.Nat, "no": Types.Nat, "abstain": Types.Nat, "eligible": Types.Opt(Types.Nat)}),
    "result": Types.Opt(Types.Variant({"Reply": Types.Vec(Types.Nat8), "Error": Types.Text})),
    "links": Types.Vec(LINK)})
VOTE = Types.Record({
    "id": Types.Text, "proposal": Types.Text, "voter": Types.Principal, "cast_by": Types.Opt(Types.Principal),
    "choice": Types.Variant({"Yes": Types.Null, "No": Types.Null, "Abstain": Types.Null}), "cast_at": Types.Nat64})
DETAIL = Types.Record({"proposal": PROPOSAL, "votes": Types.Vec(VOTE), "next": Types.Opt(Types.Text),
                       "replaced_by": Types.Opt(Types.Text)})


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def create(realm, who, links=None, old_caller=False):
    arg = {"title": "x", "description": "", "department": [], "action": {"Motion": None}, "effective_at": [],
           "on_behalf_of": []}
    if not old_caller:
        arg["links"] = [] if links is None else [links]
    return realm.ggg("ggg_proposal_create", OLD_CREATE_ARGS if old_caller else CREATE_ARGS, arg, Types.Text, who)


def linked_from(realm, who, proposal, cursor=None, limit=None):
    arg = {"id": proposal, "cursor": [] if cursor is None else [cursor], "limit": [] if limit is None else [limit]}
    return realm.ggg("ggg_proposal_linked_from", GET_ARGS, arg, Types.Opt(PAGE), who, query=True)


def detail(realm, who, proposal):
    """``ggg_proposal_get`` with Candid: the proposal and its ``replaced_by``."""
    arg = {"id": proposal, "cursor": [], "limit": []}
    return realm.ggg("ggg_proposal_get", GET_ARGS, arg, Types.Opt(DETAIL), who, query=True)["Ok"][0]


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
    replaced, replacing = detail(realm, alice, old), detail(realm, alice, new)
    assert replaced["replaced_by"] == [new] and replaced["proposal"]["status"] == {"Withdrawn": None}
    assert replaced["proposal"]["links"] == [] and replacing["replaced_by"] == []
    assert replacing["proposal"]["links"] == [{"kind": {"Replaces": None}, "proposal": old},
                                              {"kind": {"RelatesTo": None}, "proposal": related}]
    assert detail(realm, alice, related)["replaced_by"] == []
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


def test_a_caller_built_before_links_existed_files_a_proposal_without_them(realm, people):
    """Its ProposalCreateArgs has no `links` field at all, as in GGG's conformance suite: absent means none."""
    alice = people["alice"]
    made = create(realm, alice, old_caller=True)["Ok"]
    seen = detail(realm, alice, made)
    assert seen["proposal"]["links"] == [] and seen["replaced_by"] == []
    assert linked_from(realm, alice, made) == {"Ok": [{"items": [], "next": []}]}
