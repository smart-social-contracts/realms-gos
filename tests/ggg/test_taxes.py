import pytest

import fake_chain
from api import taxes
import world
from core.db import each
from ggg import Delegation, Department, Invoice, Land, Member, TaxDeclaration, TaxPolicy
from kit import call, denied, later, ok
from test_treasury import balance_reply, setup_finance

PROPERTY = {"code": "property", "base": "land_area", "unit_value": 100, "method": "progressive",
            "brackets": [{"up_to": 50_000, "rate_bp": 40}, {"up_to": None, "rate_bp": 80}], "due_days": 100}
RESIDENCE = {"code": "residence", "base": "flat", "method": "fixed", "amount": 5, "exempt_profiles": ["exempt"]}
REVENUE = {"code": "revenue", "base": "declared", "method": "proportional", "rate_bp": 200}
POLICY = {"symbol": "REALM", "taxes": [PROPERTY, RESIDENCE, REVENUE]}


def adopt_policy(document=POLICY, by="bob"):
    p = ok("tax_policy_draft", by, document=document, description="Agora taxes")["policy"]
    ok("tax_policy_propose", by, policy=p)
    return p


def invoices_of(payer):
    return {i.amount for i in each(Invoice, "payer", payer)}


def test_story_agora_taxes():
    setup_finance()
    Land(id="L-1", owner="User:alice", width=10, height=30)
    Land(id="L-2", owner="User:bob", width=20, height=40)
    first = adopt_policy()
    out = ok("tax_assess", "bob", period="2026")
    assert out["count"] == 2 + len(world.PEOPLE)
    assert invoices_of("User:alice") == {120, 5}
    assert invoices_of("User:bob") == {50_000 * 40 // 10_000 + 30_000 * 80 // 10_000, 5}
    again = ok("tax_assess", "bob", period="2026")
    assert (again["policy"], again["count"], again["skipped"]) == (first, 0, out["count"])

    d = ok("tax_declare", "dr-ana", organization="clinic-b", period="2026-Q3", base="revenue", amount=182_000)
    assert d["owed"] == 3_640 and Invoice[d["invoice"]].payer == "Organization:clinic-b"
    assert "already declared" in call("tax_declare", "dr-ana", organization="clinic-b", period="2026-Q3",
                                      base="revenue", amount=1)["error"]

    tax = next(i for i in each(Invoice, "payer", "User:alice") if i.amount == 120)
    balance_reply(120)
    ok("invoice_check", "alice", invoice=tax.id)
    assert TaxDeclaration[tax.reason.split(":")[1]].status == "settled"

    world.policy(Department["Administration"], 2, 3)
    brackets = [{"up_to": 50_000, "rate_bp": 40}, {"up_to": None, "rate_bp": 100}]
    raised = {**POLICY, "taxes": [{**PROPERTY, "brackets": brackets}]}
    for name, args in (("tax_policy_draft", {"document": raised}), ("tax_policy_propose", {"policy": "TAX-POLICY-2"})):
        p = ok(name, "bob", **args)["proposal"]
        for voter in world.PEOPLE:
            ok("proposal_vote", voter, proposal=p, choice="yes")
        later(8 * 86400)
        ok("proposal_finalize", "ctl", proposal=p)
        assert ok("proposal_execute", "ctl", proposal=p)["status"] == "executed"
    new = TaxPolicy["TAX-POLICY-2"]
    assert (new.status, new.proposal, TaxPolicy[first].status) == ("adopted", p, "superseded")


def test_I_FIN_4_adopted_tax_policy_is_frozen():
    setup_finance()
    p = adopt_policy()
    assert "only a draft" in call("tax_policy_propose", "bob", policy=p)["error"]
    q = adopt_policy({"taxes": [RESIDENCE]})
    assert (TaxPolicy[p].status, TaxPolicy[q].status) == ("superseded", "adopted")
    assert TaxPolicy[p].document == POLICY


def test_reference_calculation():
    assert taxes.owed(RESIDENCE, 1) == 5
    assert taxes.owed(REVENUE, 182_000) == 3_640
    assert taxes.owed(PROPERTY, 30_000) == 120
    assert taxes.owed(PROPERTY, 80_000) == 200 + 240
    assert taxes.owed({**REVENUE, "exempt_below": 10_000}, 9_999) == 0
    assert taxes.owed(PROPERTY, 0) == 0


def test_policy_is_checked():
    setup_finance()
    bad = [{"taxes": []}, {"taxes": [{**RESIDENCE, "method": "lottery"}]},
           {"taxes": [{**REVENUE, "rate_bp": 10_001}]},
           {"taxes": [{**PROPERTY, "brackets": [{"up_to": 5, "rate_bp": 1}]}]},
           {"taxes": [RESIDENCE, RESIDENCE]}]
    for document in bad:
        assert not call("tax_policy_draft", "bob", document=document)["ok"], document
    assert TaxPolicy.count() == 0


def test_flat_tax_skips_exempt_and_inactive_members():
    setup_finance()
    world.profile("exempt")
    world.person("elena", "exempt")
    Member["mallory"].status = "suspended"
    adopt_policy({"taxes": [RESIDENCE]})
    ok("tax_assess", "bob", period="2026", tax="residence")
    assert not invoices_of("User:elena") and not invoices_of("User:mallory") and invoices_of("User:alice") == {5}


def test_tax_assess_needs_an_adopted_policy():
    setup_finance()
    assert "no tax policy" in call("tax_assess", "bob", period="2026")["error"]


def test_tax_declare_rules_and_delegation():
    setup_finance()
    adopt_policy()
    assert "assessed by the realm" in call("tax_declare", "alice", period="2026", base="property", amount=1)["error"]
    assert "do not administer" in call("tax_declare", "alice", organization="clinic-b", period="2026",
                                       base="revenue", amount=1)["error"]
    Delegation(id="d1", grantor="dr-ana", delegate="claude-conn", status="active",
               scope={"operations": ["tax.declare"]}, expires_at=fake_chain.now() + 3600)
    d = ok("tax_declare", "claude-conn", organization="clinic-b", period="2026-Q4", base="revenue", amount=50_000,
           on_behalf_of="dr-ana")
    assert TaxDeclaration[d["declaration"]].declarant == "Organization:clinic-b"
    assert "sign in" in denied("tax_declare", fake_chain.ANONYMOUS, period="2026", base="revenue", amount=1)


@pytest.mark.parametrize("name", ["tax_policy_draft", "tax_policy_propose", "tax_assess"])
def test_members_cannot_manage_or_assess_taxes(name):
    world.build()
    assert "needs" in denied(name, "alice")
