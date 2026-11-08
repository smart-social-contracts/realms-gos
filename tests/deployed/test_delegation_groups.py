"""Power of attorney by groups on the real wasm (realms-gos#557, phase 3): a grant that names groups through GGG's Candid
methods, the vocabulary a plain member reads, and the upgrade of a realm whose grants were made before groups.

The upgrade runs from the previous release's own wasm, named by ``REALM_PREVIOUS_WASM`` (skipped when unset): its
grants name permissions, its events have no ``grant``, and its delegated updates are what the instruction budget of
this release is measured against.
"""

import json
import os

import pytest
from ic.candid import Types
from pic import CONTROLLER, Canister

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")
DAY = 86400
NS = 10**9
CHOICE = Types.Variant({"Yes": Types.Null, "No": Types.Null, "Abstain": Types.Null})
VOTE_ARGS = Types.Record({"proposal": Types.Text, "choice": CHOICE, "on_behalf_of": Types.Opt(Types.Principal)})
VOTE = Types.Record({"id": Types.Text, "proposal": Types.Text, "voter": Types.Principal,
                     "cast_by": Types.Opt(Types.Principal), "choice": CHOICE, "cast_at": Types.Nat64})
GROUPS_ARGS = Types.Record({"cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32),
                            "on_behalf_of": Types.Opt(Types.Principal)})
TIERED = Types.Record({"operation": Types.Text, "permission": Types.Text,
                       "tier": Types.Variant({"Direct": Types.Null, "Confirm": Types.Null}),
                       "confirm_if": Types.Opt(Types.Text)})
GROUP = Types.Record({"id": Types.Text, "label": Types.Text, "summary": Types.Text, "risk": Types.Text,
                      "longest": Types.Nat64, "confirms": Types.Bool, "operations": Types.Vec(TIERED)})
GROUP_PAGE = Types.Record({"items": Types.Vec(GROUP), "next": Types.Opt(Types.Text)})
REVOKE_ARGS = Types.Record({"cursor": Types.Opt(Types.Text), "limit": Types.Opt(Types.Nat32),
                            "on_behalf_of": Types.Opt(Types.Principal)})
REVOKED = Types.Record({"revoked": Types.Nat, "next": Types.Opt(Types.Text)})

# Instructions of an update, measured in the canister: a vote cast by Alice, and the same vote cast for her by her
# delegate, the first time (which adds the notice) and again (which updates it).
MEASURE = """
import json
from core.op import call

def votes(proposal, user, signer, choices, **extra):
    spent = []
    for choice in choices:
        start = ic.performance_counter(0)
        out = call("proposal_vote", {{"proposal": proposal, "choice": choice, **extra}}, user=signer)
        spent.append(ic.performance_counter(0) - start)
        assert out["ok"], out
    return spent

votes("P-1", "{alice}", "{alice}", ["no"])  # compiles what the first vote reads
own = votes("P-1", "{alice}", "{alice}", ["yes", "no"])
claude = votes("P-1", "{alice}", "{claude}", ["yes", "no", "yes"], on_behalf_of="{alice}")
print(json.dumps({{"own": own, "delegated": claude}}))
"""

# The instructions of a vote cast for a member, three times what PocketIC measures for this release (at most 65M
# the first time, which compiles what the notice reads, and about 50M after; the member's own vote costs 36M).
DELEGATED_VOTE_BUDGET = 150_000_000


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def refused(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert not out["ok"], (op, out)
    return out["error"]


def launch(realm, people):
    """A launched realm with Alice and Bob as members, and a proposal of Bob's, P-1, open for votes."""
    founder = people["founder"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_launch", founder)
    for member in (people["alice"], people["bob"]):
        ok(realm, "realm_join", member)
    ok(realm, "proposal_create", people["bob"], title="Bike lane")


def measured(realm, people):
    code = MEASURE.format(alice=people["alice"], claude=people["claude-conn"])
    return json.loads(realm.shell(code).strip().splitlines()[-1])


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """This release's wasm, in a launched realm."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    launch(realm, people)
    return realm


@pytest.fixture(scope="module")
def upgraded(wasm_of, people):
    """A realm of the previous release whose member has a grant that names permissions, and a delegate's vote on record
    from before the upgrade; then upgraded to this release."""
    realm = Canister(PREVIOUS, installer=people["installer"])
    launch(realm, people)
    alice, claude = people["alice"], people["claude-conn"]
    realm.old_grant = ok(realm, "delegation_grant", alice, delegate=claude, operations=["proposal.vote", "proposal.create"],
                         expires_in=30 * DAY)["delegation"]
    ok(realm, "proposal_vote", claude, proposal="P-1", choice="yes", on_behalf_of=alice)
    realm.before = measured(realm, people)
    realm.last_event = int(ok(realm, "events", CONTROLLER, query=True)["events"][0]["id"])
    realm.wait(600)  # PocketIC refuses an upgrade right after the install of a wasm this big
    realm.upgrade(wasm_of("production"))
    return realm


def test_a_vote_under_a_group_grant_goes_through_ggg_proposal_vote(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    ok(realm, "delegation_grant", alice, delegate=claude, groups=["vote"], expires_in=30 * DAY)
    arg = {"proposal": "P-1", "choice": {"Yes": None}, "on_behalf_of": [alice]}
    out = realm.ggg("ggg_proposal_vote", VOTE_ARGS, arg, VOTE, claude)
    assert "Ok" in out, out
    assert str(out["Ok"]["voter"]) == alice and str(out["Ok"]["cast_by"][0]) == claude
    # The group covers voting and nothing else of Alice's.
    other = realm.call("profile_update", {"nickname": "Ali", "on_behalf_of": alice}, sender=claude)
    assert not other["ok"] and "does not cover" in other["error"]
    notices = [n for n in ok(realm, "notification_list", alice, query=True)["notifications"] if n["topic"] == "poa"]
    assert notices and notices[0]["title"] == f"{claude} did proposal_vote P-1"


def test_the_delegate_of_a_grant_reads_its_activity_with_no_permission_of_its_own(realm, people):
    alice, bob, claude = people["alice"], people["bob"], people["claude-conn"]
    mine = [g for g in ok(realm, "delegation_list", alice, query=True)["as_grantor"] if g["delegate"] == claude][-1]["id"]
    page = ok(realm, "delegation_activity", claude, query=True, delegation=mine)
    assert page["events"] and {e["grant"] for e in page["events"]} == {mine}
    other = ok(realm, "delegation_grant", bob, delegate=people["mallory"], groups=["vote"], expires_in=DAY)
    error = refused(realm, "delegation_activity", claude, query=True, delegation=other["delegation"])
    assert "needs self.data_view" in error


def test_a_plain_member_reads_the_groups_she_can_grant(realm, people):
    out = realm.ggg("ggg_delegation_groups", GROUPS_ARGS, {"cursor": [], "limit": [], "on_behalf_of": []},
                    GROUP_PAGE, people["bob"], query=True)
    groups = {g["id"]: g for g in out["Ok"]["items"]}
    assert list(groups) == ["see", "profile", "vote", "propose", "filings", "justice", "payout", "identity",
                            "membership"] and out["Ok"]["next"] == []
    vote = groups["vote"]
    assert vote["label"] == "Vote for me" and vote["longest"] == 365 * DAY * NS and not vote["confirms"]
    assert [(o["operation"], o["permission"], o["tier"]) for o in vote["operations"]] == [
        ("proposal_vote", "proposal.vote", {"Direct": None})]
    assert groups["propose"]["confirms"]
    page = realm.ggg("ggg_delegation_groups", GROUPS_ARGS, {"cursor": [], "limit": [2], "on_behalf_of": []},
                     GROUP_PAGE, people["bob"], query=True)["Ok"]
    assert [g["id"] for g in page["items"]] == ["see", "profile"] and page["next"] == ["3"]
    json_groups = ok(realm, "delegable_operations", people["bob"], query=True)["groups"]
    assert [g["id"] for g in json_groups] == list(groups)


def test_revoking_every_grant_is_the_members_own_act(realm, people):
    alice, claude = people["alice"], people["claude-conn"]
    ok(realm, "delegation_grant", alice, delegate=people["mallory"], groups=["*"], expires_in=30 * DAY)
    assert "cannot be done" in refused(realm, "delegation_revoke_all", people["mallory"], on_behalf_of=alice)
    out = realm.ggg("ggg_delegation_revoke_all", REVOKE_ARGS, {"cursor": [], "limit": [], "on_behalf_of": []},
                    REVOKED, alice)
    assert out["Ok"]["revoked"] >= 1 and out["Ok"]["next"] == []
    assert all(g["status"] == "revoked" for g in ok(realm, "delegation_list", alice, query=True)["as_grantor"]
               if g["delegate"] in (claude, people["mallory"]))


def test_the_instructions_of_a_delegated_vote_stay_within_budget(realm, people):
    ok(realm, "delegation_grant", people["alice"], delegate=people["claude-conn"], groups=["vote"],
       expires_in=30 * DAY)
    spent = measured(realm, people)
    print("instructions", spent)
    assert max(spent["delegated"]) < DELEGATED_VOTE_BUDGET and max(spent["own"]) < DELEGATED_VOTE_BUDGET


@pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")
def test_a_grant_made_before_groups_works_as_it_did(upgraded, people):
    alice, claude = people["alice"], people["claude-conn"]
    ok(upgraded, "proposal_vote", claude, proposal="P-1", choice="no", on_behalf_of=alice)
    assert "motion" in refused(upgraded, "proposal_create", claude, title="x", action={"op": "me", "args": {}},
                               on_behalf_of=alice)
    assert "does not cover" in refused(upgraded, "profile_update", claude, nickname="Ali", on_behalf_of=alice)
    listed = {o["op"] for o in ok(upgraded, "delegable_operations", alice, query=True)["operations"]}
    assert "proposal_vote" in listed and "payment_account_add" not in listed and len(listed) == 24
    card = [g for g in ok(upgraded, "delegation_list", alice, query=True)["as_grantor"]
            if g["id"] == upgraded.old_grant][0]
    assert card["scope"] == {"operations": ["proposal.vote", "proposal.create"]} and card["status"] == "active"


@pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")
def test_events_written_before_the_upgrade_read_grant_as_empty(upgraded, people):
    alice = people["alice"]
    events = ok(upgraded, "events", CONTROLLER, query=True, count=500)["events"]
    before = [e for e in events if int(e["id"]) <= upgraded.last_event and e["by"] == people["claude-conn"]]
    if any(e.get("grant") for e in before):
        pytest.skip("REALM_PREVIOUS_WASM already records the grant on events (it is from after #587): this test needs "
                    "a release from before, whose events have none")
    assert before and all(not e.get("grant") for e in before)
    ok(upgraded, "proposal_vote", people["claude-conn"], proposal="P-1", choice="yes", on_behalf_of=alice)
    after = ok(upgraded, "events", CONTROLLER, query=True)["events"][0]
    assert after.get("grant") == upgraded.old_grant
    activity = ok(upgraded, "delegation_activity", alice, query=True, delegation=upgraded.old_grant)["events"]
    assert activity[0]["id"] == after["id"] and all(int(e["id"]) > upgraded.last_event for e in activity)
    notices = [n for n in ok(upgraded, "notification_list", alice, query=True)["notifications"] if n["topic"] == "poa"]
    assert len(notices) == 1


@pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")
def test_what_the_upgrade_adds_to_a_delegated_vote(upgraded, people):
    after = measured(upgraded, people)
    print("instructions before the upgrade", upgraded.before, "after", after)
    assert max(after["delegated"]) < DELEGATED_VOTE_BUDGET
