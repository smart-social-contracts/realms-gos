"""A proposal's voting rule on the real wasm (ggg I-GOV-3, I-GOV-7; realms-gos#547): stored when it is filed through
GGG's Candid method, never weaker than the scope's default, applied as stored, decided by the realm's casting vote at a
tie, and migrated for the proposals the last release left open. Numbers outside the range GGG gives them are refused.
"""

import os

import pytest
from ic.candid import Types
from ic.principal import Principal
from pic import CONTROLLER, Canister

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
PERCENT = 1_000_000
ONE = 100 * PERCENT
LINK = Types.Record({"kind": Types.Variant({"Replaces": Types.Null, "RelatesTo": Types.Null}), "proposal": Types.Text})
BASIS = Types.Variant({"Cast": Types.Null, "Eligible": Types.Null})
CREATE_ARGS = Types.Record({
    "title": Types.Text, "description": Types.Text, "department": Types.Opt(Types.Text), "kind": Types.Opt(Types.Text),
    "matter": Types.Opt(Types.Text), "threshold": Types.Opt(Types.Nat), "quorum": Types.Opt(Types.Nat),
    "threshold_of": Types.Opt(BASIS), "action": Types.Variant({"Motion": Types.Null}),
    "effective_at": Types.Opt(Types.Nat64), "links": Types.Opt(Types.Vec(LINK)),
    "on_behalf_of": Types.Opt(Types.Principal)})
GOVERNANCE_ARGS = Types.Record({
    "voting_window": Types.Opt(Types.Nat64), "voting_threshold": Types.Opt(Types.Nat),
    "voting_quorum": Types.Opt(Types.Nat),
    "casting_vote": Types.Opt(Types.Variant({"Set": Types.Principal, "Clear": Types.Null}))})
GOVERNED = Types.Variant({"Proposed": Types.Text, "Done": Types.Opt(Types.Text)})


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def file(realm, who, department=None, threshold=None, quorum=None, basis=None, kind=None, matter=None):
    """``ggg_proposal_create`` with Candid: the rule stated is optional. Returns the proposal's id, or the GGG error."""
    arg = {"title": "x", "description": "", "department": [department] if department else [],
           "kind": [kind] if kind else [], "matter": [matter] if matter else [],
           "threshold": [threshold] if threshold is not None else [], "quorum": [quorum] if quorum is not None else [],
           "threshold_of": [{basis: None}] if basis else [], "action": {"Motion": None}, "effective_at": [],
           "links": [], "on_behalf_of": []}
    out = realm.ggg("ggg_proposal_create", CREATE_ARGS, arg, Types.Text, who)
    return out["Ok"] if "Ok" in out else out["Err"]


def govern(realm, **rules):
    """``ggg_governance_update`` as the controller; ``casting_vote`` is a principal, or "clear"."""
    cast = rules.pop("casting_vote", None)
    arg = {"voting_window": [], "voting_threshold": [], "voting_quorum": [],
           "casting_vote": [] if cast is None else [{"Clear": None} if cast == "clear" else {"Set": cast}]}
    arg.update({k: [v] for k, v in rules.items()})
    out = realm.ggg("ggg_governance_update", GOVERNANCE_ARGS, arg, GOVERNED, CONTROLLER)
    return out["Ok"] if "Ok" in out else out["Err"]


def vote(realm, who, proposal, choice):
    ok(realm, "proposal_vote", who, proposal=proposal, choice=choice)


def get(realm, who, proposal):
    return ok(realm, "proposal_get", who, query=True, proposal=proposal)["proposal"]


def launch(realm, people):
    """A launched realm of four members, the founder, Alice, Bob and Mallory, and two departments with the four of them in
    their seats: the Council decides 2 of 4, the Panel 2 of 3."""
    ctl, founder = people["ctl"], people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    members = [people[name] for name in ("alice", "bob", "mallory")]
    for member in members:
        ok(realm, "realm_join", member)
    for department, n in (("Council", 4), ("Panel", 3)):
        seat = f"{department.lower()}.member"
        ok(realm, "department_create", ctl, department=department)
        ok(realm, "position_create", ctl, department=department, position=seat, headcount=4)
        for member in members + [founder]:
            ok(realm, "appoint", ctl, position=seat, user=member)
        ok(realm, "department_set_policy", ctl, department=department, m=2, n=n)


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    return realm


def test_a_rule_stated_through_ggg_is_stored_and_decides_the_vote_as_stored(realm, people):
    founder, alice, bob, mallory = (people[n] for n in ("founder", "alice", "bob", "mallory"))
    plain = file(realm, alice)
    raised = file(realm, alice, threshold=50 * PERCENT, basis="Eligible", kind="legislation", matter="tax")
    assert {k: get(realm, alice, raised)[k] for k in ("threshold", "quorum", "threshold_of", "kind", "matter")} == {
        "threshold": 50 * PERCENT, "quorum": 0, "threshold_of": "eligible", "kind": "legislation", "matter": "tax"}
    assert get(realm, alice, plain)["threshold_of"] == "cast"
    for proposal in (plain, raised):  # two yes, a no and an abstention: everyone has voted
        for who, choice in ((founder, "yes"), (alice, "yes"), (bob, "no"), (mallory, "abstain")):
            vote(realm, who, proposal, choice)
    assert get(realm, alice, plain)["status"] == "accepted"  # two yes against one no
    assert get(realm, alice, raised)["status"] == "rejected"  # two of four is not more than half of them
    assert get(realm, alice, raised)["tally"]["eligible"] == 4


def test_a_rule_weaker_than_the_scopes_default_is_refused(realm, people):
    alice = people["alice"]
    ok(realm, "governance_update", people["ctl"], voting_quorum=20 * PERCENT)
    for stated in ({"threshold": 40 * PERCENT}, {"quorum": 10 * PERCENT}, {"department": "Council", "basis": "Cast"},
                   {"department": "Council", "threshold": 40 * PERCENT}):
        assert "InvalidArgument" in file(realm, alice, **stated), stated
    refused = realm.call("proposal_create", {"title": "x", "threshold_of": "other"}, sender=alice)
    assert not refused["ok"] and refused["kind"] == "invalid_argument"
    ok(realm, "governance_update", people["ctl"], voting_quorum=0)


def test_a_department_proposal_takes_m_of_n_over_the_seats_and_a_raised_threshold_asks_for_more(realm, people):
    alice, bob, mallory, founder = (people[n] for n in ("alice", "bob", "mallory", "founder"))
    plain, raised = file(realm, alice, "Council"), file(realm, alice, "Council", threshold=75 * PERCENT)
    assert (get(realm, alice, plain)["threshold"], get(realm, alice, plain)["threshold_of"]) == (50 * PERCENT, "eligible")
    for proposal in (plain, raised):
        vote(realm, alice, proposal, "yes")
        vote(realm, bob, proposal, "yes")
    assert get(realm, alice, plain)["status"] == "accepted"  # m of the four seats
    assert get(realm, alice, raised)["status"] == "voting"  # three quarters of four are three
    vote(realm, mallory, raised, "yes")
    assert get(realm, alice, raised)["status"] == "accepted"


def test_a_departments_threshold_is_a_share_of_the_policys_n_not_of_the_filled_seats(realm, people):
    alice, bob, mallory, founder = (people[n] for n in ("alice", "bob", "mallory", "founder"))
    plain, strict = file(realm, alice, "Panel"), file(realm, alice, "Panel", threshold=ONE)
    for proposal in (plain, strict):
        vote(realm, alice, proposal, "yes")
        vote(realm, bob, proposal, "yes")
    row = get(realm, alice, plain)
    assert (row["status"], row["tally"]["seats"], row["tally"]["eligible"]) == ("accepted", 3, 4)  # 2 of 3, whoever sits
    assert get(realm, alice, strict)["status"] == "voting"
    vote(realm, mallory, strict, "yes")
    assert get(realm, alice, strict)["status"] == "accepted"  # all of the 3, not all of the 4


def test_a_tie_at_the_threshold_is_accepted_only_with_the_casting_members_yes(realm, people):
    founder, alice, bob, mallory = (people[n] for n in ("founder", "alice", "bob", "mallory"))
    assert govern(realm, casting_vote=founder) == {"Done": []}
    decided, undecided = file(realm, alice), file(realm, alice)
    assert get(realm, alice, decided)["casting_vote"] == founder
    for proposal, yes, abstaining in ((decided, founder, bob), (undecided, bob, founder)):
        vote(realm, yes, proposal, "yes")
        vote(realm, alice, proposal, "no")
        vote(realm, abstaining, proposal, "abstain")
        vote(realm, mallory, proposal, "abstain")
    assert get(realm, alice, decided)["status"] == "accepted"  # one yes and one no, and the founder's is the yes
    assert get(realm, alice, undecided)["status"] == "rejected"  # the founder did not vote yes
    assert govern(realm, casting_vote="clear") == {"Done": []}
    assert get(realm, alice, file(realm, alice))["casting_vote"] == ""
    assert get(realm, alice, decided)["casting_vote"] == founder  # fixed at creation


def test_a_casting_vote_names_an_active_member(realm, people):
    assert "InvalidArgument" in govern(realm, casting_vote=Principal.self_authenticating(b"nobody").to_str())


def test_a_ratio_out_of_range_is_refused_whichever_way_it_comes(realm, people):
    alice, ctl = people["alice"], people["ctl"]
    assert "InvalidArgument" in govern(realm, voting_threshold=ONE + 1)
    assert "InvalidArgument" in file(realm, alice, threshold=ONE + 1)
    refused = realm.call("governance_update", {"voting_quorum": ONE + 1}, sender=ctl)
    assert not refused["ok"] and refused["kind"] == "invalid_argument"
    refused = realm.call("transfer_create", {"fund": "ROOT", "to": alice, "amount": 2**53}, sender=ctl)
    assert not refused["ok"] and refused["kind"] == "invalid_argument" and "must be between" in refused["error"]


def test_a_periods_count_beyond_a_million_is_refused_in_the_wasm(realm):
    """No operation takes a Period yet; the check is made on the operation's name, from the bounds in the build."""
    out = realm.shell(
        "from core import ranges\nfrom core.db import Fail\n"
        "for count in (1000000, 1000001):\n"
        "    try:\n"
        "        ranges.check('loan_create', {'term': {'count': count, 'unit': 'day'}})\n"
        "        print(count, 'passes')\n"
        "    except Fail as e:\n"
        "        print(count, e.kind)\n")
    assert out.split("\n")[:2] == ["1000000 passes", "1000001 invalid_argument"]


# ---- An upgrade

# Saves a proposal as the last release did: version 2, without the rule fields.
ROWS = '''
from ic_python_db import Database
import ggg

db = Database.get_instance()


def as_last_release_saved(proposal, **fields):
    key = ggg.Proposal[proposal]._id
    row = {k: v for k, v in db.load("Proposal", key).items() if k not in ("kind", "matter", "threshold_of", "casting_vote")}
    db.save("Proposal", key, {**row, "__version__": 2, **fields})
'''


@pytest.fixture(scope="module", params=[
    pytest.param("previous", marks=pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")),
    "rewritten"])
def upgraded(request, wasm_of, people):
    """A realm with a realm-wide and a department proposal open, filed under the last release's rules, then upgraded:
    by the previous release's own wasm, named by ``REALM_PREVIOUS_WASM`` (skipped when unset), or by this release's, whose
    two rows are rewritten as that release saved them, which CI can always build."""
    current = wasm_of("production")
    realm = Canister(PREVIOUS if request.param == "previous" else current, installer=people["installer"])
    launch(realm, people)
    alice = people["alice"]
    ok(realm, "governance_update", people["ctl"], voting_threshold=60 * PERCENT, voting_quorum=10 * PERCENT)
    wide, department = (ok(realm, "proposal_create", alice, title=t, **d)["proposal"] for t, d in (
        ("wide", {}), ("department", {"department": "Panel"})))
    vote(realm, alice, department, "yes")
    if request.param == "rewritten":
        out = realm.shell(ROWS + (
            f"as_last_release_saved('{wide}')\n"
            f"as_last_release_saved('{department}', threshold=3000000, tally={{'yes': 1, 'no': 0, 'abstain': 0, "
            f"'required': 2, 'veto': []}})\n"))
        assert "Traceback" not in out, out
    realm.upgrade(current)  # the heap is emptied: the next read comes from storage, and migrates the row
    realm.open = wide, department
    return realm


def test_the_proposals_of_the_last_release_are_decided_by_the_rule_they_were_filed_under(upgraded, people):
    alice, bob = people["alice"], people["bob"]
    wide, department = upgraded.open
    row = get(upgraded, alice, wide)
    assert (row["threshold"], row["quorum"], row["threshold_of"], row["casting_vote"]) == (
        60 * PERCENT, 10 * PERCENT, "cast", "")
    row = get(upgraded, alice, department)  # 2 of 3 with four seats held: it took 2 approvals, and still does
    assert (row["threshold"], row["quorum"], row["threshold_of"]) == (66_666_666, 0, "eligible")
    assert "required" not in row["tally"] and row["tally"]["yes"] == 1 and row["tally"]["seats"] == 3
    vote(upgraded, bob, department, "yes")
    assert get(upgraded, alice, department)["status"] == "accepted"
    for who in ("founder", "bob", "mallory"):
        vote(upgraded, people[who], wide, "yes")
    vote(upgraded, alice, wide, "yes")  # every member said yes: the vote is over
    assert get(upgraded, alice, wide)["status"] == "accepted"
    new = ok(upgraded, "proposal_create", alice, title="new", threshold_of="eligible")["proposal"]
    assert get(upgraded, alice, new)["threshold_of"] == "eligible"
