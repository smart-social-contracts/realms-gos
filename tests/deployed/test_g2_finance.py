"""A realm that holds taxes, budgets and invoices in the form of the last release keeps them across an upgrade to GGG's
G2 finance (realms-gos#547), and the new finance works on the real wasm.

The upgrade is from the previous release's own wasm, named by ``REALM_PREVIOUS_WASM`` (skipped when unset), or from this
release's, whose rows are written back through the controller's ``__shell__`` as the last release saved them, which CI can
always build. An entity migrates when it is read from storage, and only an upgrade, which empties the canister's heap,
makes the next read come from there.
"""

import json
import os

import pytest
from pic import Canister

PREVIOUS = os.environ.get("REALM_PREVIOUS_WASM")

BASIS_POINT = 10_000
UNIT = 10**8  # one token of the realm's currency, which has 8 decimals: a cent is 10**6 of the smallest unit
FY2026 = {"period": "FY2026", "starts_at": 1_767_225_600, "ends_at": 1_798_761_600}
FY2027 = {"period": "FY2027", "starts_at": 1_798_761_600, "ends_at": 1_830_297_600}

# The taxes as the last release held them in the policy's document.
OLD_DOCUMENT = {"symbol": "DOM", "taxes": [
    {"code": "ibi", "base": "land_area", "method": "proportional", "rate": 1_500_000, "unit_value": UNIT, "due_days": 60},
    {"code": "residence", "base": "flat", "method": "fixed", "amount": 5 * 10**6},
    {"code": "turnover", "base": "declared", "method": "progressive",
     "brackets": [{"up_to": 1_000 * UNIT, "rate": 200 * BASIS_POINT}, {"up_to": None, "rate": 300 * BASIS_POINT}]}]}
# The versions the entities are saved at, where this release moved them on.
VERSION = {"TaxPolicy": 3, "Budget": 2, "Invoice": 2}
# The same taxes as this release holds them.
DOCUMENT = {"symbol": "DOM"}
ENTRIES = [
    {"code": "ibi", "kind": "tax", "subject": "Land", "rate": 1_500_000, "facts": {"unit_value": UNIT, "due_days": 60}},
    {"code": "residence", "kind": "tax", "subject": "User", "amount": 5 * 10**6, "facts": {}},
    {"code": "turnover", "kind": "tax", "subject": "Declaration",
     "facts": {"brackets": [{"up_to": 1_000 * UNIT, "rate": 200 * BASIS_POINT}, {"up_to": None, "rate": 300 * BASIS_POINT}]}}]

ROWS = '''
import json
from ic_python_db import Database
import ggg

db = Database.get_instance()
TABLE = ((ggg.TaxPolicy, "TAX-POLICY-1"), (ggg.Budget, "B-1"), (ggg.Budget, "B-2"), (ggg.Invoice, "INV-1"),
         (ggg.Invoice, "INV-2"), (ggg.Treasury, "1"), (ggg.Land, "L-1"))
'''
SAVE_OLD = ROWS + f'''
def version_1(entity, alias, drop=(), **fields):
    """Save the row as the last release did: version 1, the fields in ``drop`` absent, ``fields`` in place."""
    key = entity[alias]._id
    row = {{k: v for k, v in db.load(entity.__name__, key).items() if k not in drop}}
    db.save(entity.__name__, key, {{**row, "__version__": 1, **fields}})


version_1(ggg.TaxPolicy, "TAX-POLICY-1", drop=("taxes",), document={OLD_DOCUMENT!r})
version_1(ggg.Budget, "B-1", kind="capital")  # any text was a kind
version_1(ggg.Budget, "B-2")
version_1(ggg.Invoice, "INV-1", drop=("items", "facts"))
version_1(ggg.Invoice, "INV-2", drop=("items", "facts"))
version_1(ggg.Treasury, "1", drop=("debt_outstanding",))
version_1(ggg.Land, "L-1", drop=("assessed_value", "area", "assessed_at", "assessment_ref"))
print(json.dumps({{f"{{e.__name__}}:{{a}}": db.load(e.__name__, e[a]._id) for e, a in TABLE}}))
'''
READ = ROWS + '''
print(json.dumps({f"{e.__name__}:{a}": db.load(e.__name__, e[a]._id) for e, a in TABLE}))
'''


def ok(realm, op, who, query=False, **args):
    out = realm.call(op, args, sender=who, query=query)
    assert out["ok"], (op, out)
    return out


def refused(realm, op, who, **args):
    out = realm.call(op, args, sender=who)
    assert not out["ok"], (op, out)
    return out


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


def rows(realm):
    """The stored rows, read the way the canister reads them: an old one is migrated, and saved, by this read."""
    return json.loads(run(realm, READ))


def debt(realm):
    """The treasury's debt as the canister reads it: a row saved before the field existed reads its default."""
    return int(run(realm, 'import ggg; print(ggg.Treasury["1"].debt_outstanding)'))


def versions_of(saved, wanted):
    return {key: wanted[key.split(":")[0]] for key in saved if key.split(":")[0] in wanted}


def versions(realm):
    return {key: row["__version__"] for key, row in rows(realm).items() if key.split(":")[0] in VERSION}


def seed_previous(realm, ctl, alice):
    """The budget, invoices and tax policy through the operations of the last release, which took a budget line of any
    kind, one line at a time, an invoice without items and the taxes in the document."""
    ok(realm, "budget_draft", ctl, fund="ROADS", period="FY2026", category="capital", kind="capital", planned=300 * UNIT)
    ok(realm, "budget_draft", ctl, fund="ROOT", period="FY2026", category="property_tax", kind="revenue", planned=900 * UNIT)
    ok(realm, "budget_propose", ctl, budget="B-1")
    ok(realm, "budget_propose", ctl, budget="B-2")
    for amount in (120 * UNIT, 7 * UNIT):
        ok(realm, "invoice_create", ctl, payer=alice, amount=amount, reason="TaxDeclaration:TD-1" if amount > UNIT * 100 else "")
    ok(realm, "tax_policy_draft", ctl, document=OLD_DOCUMENT)


def seed_current(realm, ctl, alice):
    """The same through this release's operations; the rows are then written back as the last release saved them."""
    ok(realm, "budget_draft", ctl, fund="ROADS", period="FY2026", category="capital", kind="expense", planned=300 * UNIT)
    ok(realm, "budget_draft", ctl, fund="ROOT", period="FY2026", category="property_tax", kind="revenue", planned=900 * UNIT)
    ok(realm, "budget_propose", ctl, period="FY2026")
    for amount in (120 * UNIT, 7 * UNIT):
        ok(realm, "invoice_create", ctl, payer=alice, amount=amount, items=[{"concept": "fee", "amount": amount}],
           reason="TaxDeclaration:TD-1" if amount > UNIT * 100 else "")
    ok(realm, "tax_policy_draft", ctl, document=DOCUMENT, taxes=ENTRIES)


@pytest.fixture(scope="module", params=[
    pytest.param("previous", marks=pytest.mark.skipif(not PREVIOUS, reason="REALM_PREVIOUS_WASM names no wasm")),
    "rewritten"])
def realm(request, wasm_of, people):
    """A realm with an adopted tax policy, two budget lines, two invoices and a parcel, in the form of the last release,
    then upgraded."""
    current = wasm_of("production")
    realm = Canister(PREVIOUS if request.param == "previous" else current, installer=people["installer"])
    ctl, founder, alice = people["ctl"], people["founder"], people["alice"]
    ok(realm, "setup_step", people["installer"], step="install", network="local", founder=founder)
    ok(realm, "setup_step", founder, step="realm", name="Agora", open_registration=True)
    ok(realm, "setup_step", founder, step="trust_policy", require_marketplace_approval=False)
    ok(realm, "setup_step", founder, step="tokens", accounting_currency="DOM", accounting_currency_decimals=8)
    ok(realm, "setup_launch", founder)
    ok(realm, "token_register", ctl, symbol="DOM", ledger=people["installer"], decimals=8, fee=10_000)
    ok(realm, "realm_join", alice)
    ok(realm, "fund_create", ctl, fund="ROOT")
    ok(realm, "fund_create", ctl, fund="ROADS")
    ok(realm, "period_open", ctl, **FY2026)
    ok(realm, "period_open", ctl, **FY2027)
    (seed_previous if request.param == "previous" else seed_current)(realm, ctl, alice)
    ok(realm, "land_register", ctl, owner=alice, x=0, y=0)
    ok(realm, "tax_policy_propose", ctl, policy="TAX-POLICY-1")
    if request.param == "previous":
        realm.old_rows = rows(realm)  # as the last release saved them: nothing has loaded, and so migrated, them yet
    else:
        # What a new realm saves: the rows are of this release's shape, and the old rows below are those of the last.
        assert versions(realm) == {key: VERSION[key.split(":")[0]] for key in versions(realm)} != {}
        realm.old_rows = json.loads(run(realm, SAVE_OLD))
    realm.upgrade(current)
    return realm


def test_the_rows_start_in_the_form_of_the_last_release(realm):
    old = realm.old_rows
    assert all(old[key]["__version__"] < number for key, number in versions_of(old, VERSION).items())
    assert old["TaxPolicy:TAX-POLICY-1"]["document"] == OLD_DOCUMENT and "taxes" not in old["TaxPolicy:TAX-POLICY-1"]
    assert old["Budget:B-1"]["kind"] == "capital" and "items" not in old["Invoice:INV-1"]
    assert "debt_outstanding" not in old["Treasury:1"] and "assessed_value" not in old["Land:L-1"]


def test_the_tax_policy_keeps_its_taxes_as_typed_entries(realm, people):
    policy = ok(realm, "tax_policy_view", people["alice"], query=True)["policy"]
    assert (policy["document"], policy["taxes"]) == (DOCUMENT, ENTRIES)
    assert versions(realm)["TaxPolicy:TAX-POLICY-1"] == 3


def test_the_budget_lines_are_revenue_or_expense(realm, people):
    seen = ok(realm, "budget_view", people["alice"], query=True, period="FY2026")
    lines = {b["category"]: (b["kind"], b["planned"], b["status"]) for f in seen["funds"] for b in f["budgets"]}
    assert lines == {"capital": ("expense", 300 * UNIT, "adopted"), "property_tax": ("revenue", 900 * UNIT, "adopted")}


def test_each_invoice_has_one_item_for_its_whole_amount(realm, people):
    mine = ok(realm, "my_invoices", people["alice"], query=True)["invoices"]
    assert [(i["amount"], i["items"]) for i in mine] == [
        (120 * UNIT, [{"concept": "taxdeclaration", "amount": 120 * UNIT}]), (7 * UNIT, [{"concept": "invoice", "amount": 7 * UNIT}])]
    stored = rows(realm)["Invoice:INV-1"]
    assert stored["facts"] == {} and stored["__version__"] == 2


def test_the_treasury_owes_nothing_and_the_parcel_is_not_assessed(realm, people):
    assert debt(realm) == 0
    parcel = ok(realm, "land_list", people["alice"], query=True)["lands"][0]
    assert (parcel["assessed_value"], parcel["area"], parcel["assessed_at"]) == (None, None, 0)


def test_tax_assess_bills_the_items_of_the_migrated_policy_rounded_to_the_cent(realm, people):
    ctl, alice = people["ctl"], people["alice"]
    ok(realm, "land_assess", ctl, land="L-1", value=95_000 * UNIT, area=120, reference="CAT-17")
    parcel = ok(realm, "land_list", alice, query=True)["lands"][0]
    assert (parcel["assessed_value"], parcel["area"], parcel["assessment_ref"]) == (95_000 * UNIT, 120, "CAT-17")
    out = ok(realm, "tax_assess", ctl, period="2026")
    assert out["count"] == 3  # the tax on land for alice, the tax on users for her and the founder
    by_concept = {}
    for invoice in ok(realm, "my_invoices", alice, query=True)["invoices"]:
        for item in invoice["items"]:
            by_concept.setdefault(item["concept"], []).append(item["amount"])
        assert sum(i["amount"] for i in invoice["items"]) == invoice["amount"]
    # 1.5 % of one unit is 1.5 cents, and an exact half goes away from zero: 2 cents.
    assert by_concept["ibi"] == [2 * 10**6] and by_concept["residence"] == [5 * 10**6]
    again = ok(realm, "tax_assess", ctl, period="2026")
    assert (again["count"], again["skipped"]) == (0, 3)


def test_tax_declare_rounds_a_progressive_tax_to_the_cent_in_each_bracket(realm, people):
    declared = ok(realm, "tax_declare", people["ctl"], period="2026", base="turnover", amount=1_000 * UNIT + 5 * 10**7)
    # 2 % of 1 000 units, and 3 % of 0.5 unit: 1.5 cents, which is 2.
    assert declared["owed"] == 20 * UNIT + 2 * 10**6


def test_budget_extend_carries_the_budget_of_the_period_before_and_only_once(realm, people):
    ctl = people["ctl"]
    assert ok(realm, "budget_extend", ctl, period="FY2027")["extended"] == 2
    assert refused(realm, "budget_extend", ctl, period="FY2027")["kind"] == "conflict"
    seen = ok(realm, "budget_view", people["alice"], query=True, period="FY2027")
    assert sorted(b["status"] for f in seen["funds"] for b in f["budgets"]) == ["extended", "extended"]


def test_a_budget_is_proposed_per_period_with_its_totals_as_facts(realm, people):
    ctl = people["ctl"]
    ok(realm, "budget_draft", ctl, fund="ROADS", period="FY2027", category="capital", kind="expense", planned=400 * UNIT)
    ok(realm, "budget_draft", ctl, fund="ROOT", period="FY2027", category="fees", kind="revenue", planned=50 * UNIT)
    out = ok(realm, "budget_propose", ctl, period="FY2027", years=2)
    assert (out["revenue"], out["expense"], len(out["budgets"])) == (50 * UNIT, 400 * UNIT, 2)
    seen = ok(realm, "budget_view", people["alice"], query=True, period="FY2027")
    assert sorted(b["status"] for f in seen["funds"] for b in f["budgets"]) == ["adopted", "adopted", "extended"]  # the replaced line is closed


def test_a_loan_is_the_debt_of_the_realm_until_repaid(realm, people):
    ctl = people["ctl"]
    term = {"count": 10, "unit": "year"}
    loan = ok(realm, "loan_create", ctl, lender="bank-a", amount=500 * UNIT, term=term, rate=3_200_000, fund="ROADS")["loan"]
    assert debt(realm) == 500 * UNIT
    ok(realm, "loan_repay", ctl, loan=loan, amount=80 * UNIT)
    (listed,) = ok(realm, "loan_list", ctl, query=True, status="active")["loans"]
    assert (listed["id"], listed["outstanding"], listed["symbol"], listed["term"]) == (loan, 420 * UNIT, "DOM", term)
    assert refused(realm, "loan_repay", ctl, loan=loan, amount=421 * UNIT)["kind"] == "conflict"
    assert debt(realm) == 420 * UNIT


def test_a_line_joins_a_pending_invoice(realm, people):
    ctl = people["ctl"]
    out = ok(realm, "invoice_item_add", ctl, invoice="INV-2", item={"concept": "sanitation", "amount": 3 * UNIT})
    assert out["invoice"] == "INV-2"
    (invoice,) = [i for i in ok(realm, "my_invoices", people["alice"], query=True)["invoices"] if i["id"] == "INV-2"]
    assert invoice["amount"] == 10 * UNIT and [i["concept"] for i in invoice["items"]] == ["invoice", "sanitation"]


def test_activating_a_codex_needs_a_pending_one(realm, people):
    out = refused(realm, "codex_activate", people["ctl"], codex="sha256:" + "0" * 64)
    assert out["kind"] == "conflict" and "no codex is pending" in out["error"]
    assert ok(realm, "codex_status", people["alice"], query=True)["pending"] is None


def test_a_number_outside_its_range_is_refused_before_anything_is_written(realm, people):
    ctl = people["ctl"]
    loan = {"lender": "bank-a", "amount": UNIT, "term": {"count": 10, "unit": "year"}, "rate": 1, "fund": "ROADS"}
    loans = ok(realm, "loan_list", ctl, query=True)["loans"]
    for change in ({"term": {"count": 1_000_001, "unit": "year"}}, {"amount": 2**53}, {"rate": 10_000_000_001}):
        assert refused(realm, "loan_create", ctl, **{**loan, **change})["kind"] == "invalid_argument", change
    assert ok(realm, "loan_list", ctl, query=True)["loans"] == loans
    item = {"concept": "x", "amount": 2**53}
    assert refused(realm, "invoice_item_add", ctl, invoice="INV-2", item=item)["kind"] == "invalid_argument"


def test_a_budget_whose_total_is_out_of_range_is_refused_and_files_nothing(realm, people):
    ctl = people["ctl"]
    ok(realm, "period_open", ctl, period="FY2028", starts_at=FY2027["ends_at"], ends_at=FY2027["ends_at"] + 100)
    for category, planned in (("property_tax", 2**53 - 1), ("fees", 1)):
        ok(realm, "budget_draft", ctl, fund="ROOT", period="FY2028", category=category, kind="revenue", planned=planned)
    out = refused(realm, "budget_propose", ctl, period="FY2028")
    assert out["kind"] == "invalid_argument" and "largest amount" in out["error"]
    seen = ok(realm, "budget_view", people["alice"], query=True, period="FY2028")
    assert [b for f in seen["funds"] for b in f["budgets"]] == []  # nothing is in force


def test_a_second_upgrade_changes_nothing(realm, people):
    before = rows(realm)
    realm.upgrade()
    assert rows(realm) == before
    policy = ok(realm, "tax_policy_view", people["alice"], query=True)["policy"]
    assert (policy["document"], policy["taxes"]) == (DOCUMENT, ENTRIES)
