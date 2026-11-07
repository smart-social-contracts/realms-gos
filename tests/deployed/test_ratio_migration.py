"""A realm holding ratios in whole percents and basis points keeps their meaning across an upgrade to
hundred-millionths (realms-gos#514), on the real wasm.

The rows the last release saved are written back through the controller's ``__shell__``. An entity migrates when it
is read from storage, and only an upgrade, which empties the canister's heap, makes the next read come from there.
"""

import json

import pytest
from pic import Canister

PERCENT = 1_000_000
BASIS_POINT = 10_000

# The ratios as the last release held them, in whole percents and in basis points.
OLD_GOVERNANCE = {"threshold": 70, "quorum_percent": 10}
OLD_POLICY = {"m": 2, "n": 3, "quorum_percent": 50, "veto": []}
OLD_SHARES = {"ROADS": 30}
OLD_RUBRIC = {"price": 60, "quality": 40}
OLD_TAXES = {"symbol": "DOM", "taxes": [
    {"code": "revenue", "base": "declared", "method": "proportional", "rate_bp": 200},
    {"code": "property", "base": "flat", "method": "progressive",
     "brackets": [{"up_to": 5_000, "rate_bp": 40}, {"up_to": None, "rate_bp": 80}]}]}
# The same ratios in hundred-millionths, as this release holds them.
GOVERNANCE = {"voting_threshold": 70 * PERCENT, "voting_quorum": 10 * PERCENT}
POLICY = {"m": 2, "n": 3, "quorum": 50 * PERCENT, "veto": []}
SHARES = [{"fund": "ROADS", "share": 30 * PERCENT}]
RUBRIC = {"price": 60 * PERCENT, "quality": 40 * PERCENT}
TAXES = {"symbol": "DOM", "taxes": [
    {"code": "revenue", "base": "declared", "method": "proportional", "rate": 200 * BASIS_POINT},
    {"code": "property", "base": "flat", "method": "progressive",
     "brackets": [{"up_to": 5_000, "rate": 40 * BASIS_POINT}, {"up_to": None, "rate": 80 * BASIS_POINT}]}]}

# A proposal the last release left voting, with the shares of its action as it filed them.
OLD_ACTION = {"op": "allocation_rule_draft", "args": {"source_fund": "ROOT", "rules": {"ROADS": 20}}}

ROWS = '''
import json
from ic_python_db import Database
import ggg

db = Database.get_instance()
TABLE = ((ggg.Proposal, "P-1"), (ggg.Department, "Finance"), (ggg.Realm, "1"), (ggg.AllocationRule, "AR-1"),
        (ggg.Tender, "T-1"), (ggg.TaxPolicy, "TAX-POLICY-1"), (ggg.Proposal, "P-2"))


saved = {}


def version_1(entity, alias, drop=(), **fields):
    """Save the row as the last release did: version 1, the fields in ``drop`` absent, ``fields`` in place."""
    key = entity[alias]._id
    row = {k: v for k, v in db.load(entity.__name__, key).items() if k not in drop}
    saved[f"{entity.__name__}:{alias}"] = {**row, "__version__": 1, **fields}
    db.save(entity.__name__, key, saved[f"{entity.__name__}:{alias}"])
'''
SAVE_OLD = ROWS + f'''
version_1(ggg.Proposal, "P-1", drop=("quorum",), threshold=60, quorum_percent=25)
version_1(ggg.Department, "Finance", policy={OLD_POLICY!r})
version_1(ggg.Realm, "1", manifest_data={{**ggg.Realm["1"].manifest_data, "governance": {OLD_GOVERNANCE!r}}})
version_1(ggg.AllocationRule, "AR-1", rules={OLD_SHARES!r})
version_1(ggg.Tender, "T-1", rubric={OLD_RUBRIC!r})
version_1(ggg.TaxPolicy, "TAX-POLICY-1", document={OLD_TAXES!r})
version_1(ggg.Proposal, "P-2", drop=("quorum",), threshold=50, quorum_percent=0, action={OLD_ACTION!r})
print(json.dumps(saved))
'''
READ = ROWS + '''
print(json.dumps({f"{entity.__name__}:{alias}": db.load(entity.__name__, entity[alias]._id) for entity, alias in TABLE}))
'''


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


def rows(realm):
    """The stored rows, read the way the canister reads them: an old one is migrated, and saved, by this read."""
    return json.loads(run(realm, READ))


@pytest.fixture(scope="module")
def realm(wasm_of, people):
    """A realm with a proposal, a department policy, voting rules, an allocation rule, a tender and a tax policy,
    saved as the last release saved them, then upgraded."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    ctl, founder, alice = people["ctl"], people["founder"], people["alice"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ok(realm, "setup_launch", founder)
    ok(realm, "realm_join", alice)
    ok(realm, "department_create", ctl, department="Finance")
    for fund in ("ROOT", "ROADS"):
        ok(realm, "fund_create", ctl, fund=fund)
    ok(realm, "governance_update", ctl, voting_window=0)  # a proposal closes when its outcome is certain
    ok(realm, "proposal_create", alice, title="Bike lane")
    ok(realm, "proposal_create", alice, title="More for the roads", action={
        "op": "allocation_rule_draft", "args": {"source_fund": "ROOT", "rules": [{"fund": "ROADS", "share": 20 * PERCENT}]}})
    ok(realm, "allocation_rule_draft", ctl, source_fund="ROOT", rules=SHARES)
    ok(realm, "allocation_rule_propose", ctl, rule="AR-1")  # a budget view shows the adopted rule
    ok(realm, "tender_create", ctl, title="Bike lane", requester="Finance", opens_at=1, closes_at=2, rubric=RUBRIC)
    ok(realm, "tax_policy_draft", ctl, document=TAXES)
    # What a new realm saves: the rows are of this release's shape, and the old rows below are those of the last.
    assert {row["__version__"] for row in rows(realm).values()} == {2}
    realm.old_rows = json.loads(run(realm, SAVE_OLD))  # read before anything loads, and so migrates, them
    realm.upgrade()
    return realm


def test_the_rows_start_in_the_form_of_the_last_release(realm):
    old = realm.old_rows
    assert {row["__version__"] for row in old.values()} == {1}
    assert "quorum" not in old["Proposal:P-1"] and old["Proposal:P-1"]["quorum_percent"] == 25
    assert old["Department:Finance"]["policy"] == OLD_POLICY and old["TaxPolicy:TAX-POLICY-1"]["document"] == OLD_TAXES
    assert old["Proposal:P-2"]["action"] == OLD_ACTION


def test_every_ratio_keeps_its_meaning_after_the_upgrade(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    proposal = ok(realm, "proposal_get", alice, query=True, proposal="P-1")["proposal"]
    assert (proposal["threshold"], proposal["quorum"]) == (60 * PERCENT, 25 * PERCENT)
    assert ok(realm, "department_get", ctl, query=True, department="Finance")["department"]["policy"] == POLICY
    funds = {f["id"]: f for f in ok(realm, "budget_view", alice, query=True)["funds"]}
    assert funds["ROOT"]["rule"]["rules"] == SHARES
    assert ok(realm, "tender_get", alice, query=True, tender="T-1")["tender"]["rubric"] == RUBRIC
    assert ok(realm, "tax_policy_view", alice, query=True)["drafts"][0]["document"] == TAXES
    assert rows(realm)["Realm:1"]["manifest_data"]["governance"] == GOVERNANCE


def test_the_migrated_rows_are_saved_at_the_new_version(realm):
    assert {row["__version__"] for row in rows(realm).values()} == {2}


def test_a_proposal_filed_before_the_upgrade_runs_its_action_with_the_shares_it_was_filed_with(realm, people):
    """The action waits in the proposal with its shares as whole percents: 20 is 20 %, not 20 hundred-millionths."""
    assert rows(realm)["Proposal:P-2"]["action"]["args"]["rules"] == [{"fund": "ROADS", "share": 20 * PERCENT}]
    for voter in (people["alice"], people["founder"]):
        ok(realm, "proposal_vote", voter, proposal="P-2", choice="yes")
    proposal = ok(realm, "proposal_get", people["alice"], query=True, proposal="P-2")["proposal"]
    assert proposal["status"] == "executed" and proposal["result"]["ok"], proposal
    drafts = ok(realm, "budget_view", people["alice"], query=True)["rule_drafts"]
    assert [d["rules"] for d in drafts] == [[{"fund": "ROADS", "share": 20 * PERCENT}]]


def test_a_second_upgrade_changes_nothing(realm, people):
    before = rows(realm)
    realm.upgrade()
    assert rows(realm) == before
    proposal = ok(realm, "proposal_get", people["alice"], query=True, proposal="P-1")["proposal"]
    assert (proposal["threshold"], proposal["quorum"]) == (60 * PERCENT, 25 * PERCENT)
