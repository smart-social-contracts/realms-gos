import pytest

import fake_chain
from api import codex, taxes
import world
from core.db import each
from core.ratio import BASIS_POINT, PERCENT, RATE_MAX
from ggg import Delegation, Department, Invoice, Land, Member, Proposal, TaxDeclaration, TaxPolicy, Token
from kit import call, denied, ok
from test_ratios import stored_as_version_1
from test_treasury import balance_reply, setup_finance

# The taxes of Agora as typed entries: who is taxed (subject), what is charged (amount, rate or brackets) and the
# settings legislation reads (facts).
PROPERTY = {"code": "property", "kind": "tax", "subject": "Land",
            "facts": {"unit_value": 100, "due_days": 100,
                      "brackets": [{"up_to": 50_000, "rate": 40 * BASIS_POINT}, {"up_to": None, "rate": 80 * BASIS_POINT}]}}
RESIDENCE = {"code": "residence", "kind": "fee", "subject": "User", "amount": 5, "facts": {"exempt_profiles": ["exempt"]}}
REVENUE = {"code": "revenue", "kind": "tax", "subject": "Declaration", "rate": 200 * BASIS_POINT, "facts": {}}
DOCUMENT = {"symbol": "REALM"}
TAXES = [PROPERTY, RESIDENCE, REVENUE]
CENTS = 2  # the decimals the amounts below are in: 120 is 1.20


def setup_taxes():
    setup_finance()
    Token["REALM"].decimals = CENTS


def adopt_policy(taxes=TAXES, document=DOCUMENT, by="bob"):
    p = ok("tax_policy_draft", by, document=document, taxes=taxes, description="Agora taxes")["policy"]
    ok("tax_policy_propose", by, policy=p)
    return p


def invoices_of(payer):
    return {i.amount for i in each(Invoice, "payer", payer)}


def owed_on(area):
    return taxes.owed(PROPERTY, area * PROPERTY["facts"]["unit_value"], CENTS)


def test_story_agora_taxes():
    setup_taxes()
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
    brackets = [{"up_to": 50_000, "rate": 40 * BASIS_POINT}, {"up_to": None, "rate": 100 * BASIS_POINT}]
    raised = [{**PROPERTY, "facts": {**PROPERTY["facts"], "brackets": brackets}}]
    drafted = {"document": DOCUMENT, "taxes": raised}
    for name, args in (("tax_policy_draft", drafted), ("tax_policy_propose", {"policy": "TAX-POLICY-2"})):
        p = ok(name, "bob", **args)["proposal"]
        for voter in world.PEOPLE:
            ok("proposal_vote", voter, proposal=p, choice="yes")
        assert Proposal[p].status == "executed"  # the last vote ended the vote and ran the action
    new = TaxPolicy["TAX-POLICY-2"]
    assert (new.status, new.proposal, TaxPolicy[first].status) == ("adopted", p, "superseded")


def test_I_FIN_4_adopted_tax_policy_is_frozen():
    setup_taxes()
    p = adopt_policy()
    assert "only a draft" in call("tax_policy_propose", "bob", policy=p)["error"]
    q = adopt_policy([RESIDENCE])
    assert (TaxPolicy[p].status, TaxPolicy[q].status) == ("superseded", "adopted")
    assert (TaxPolicy[p].document, TaxPolicy[p].taxes) == (DOCUMENT, TAXES)


def test_reference_calculation():
    assert taxes.owed(RESIDENCE, 1, CENTS) == 5
    assert taxes.owed(REVENUE, 182_000, CENTS) == 3_640
    assert taxes.owed(PROPERTY, 30_000, CENTS) == 120
    assert taxes.owed(PROPERTY, 80_000, CENTS) == 200 + 240
    assert taxes.owed({**REVENUE, "facts": {"exempt_below": 10_000}}, 9_999, CENTS) == 0
    assert taxes.owed(PROPERTY, 0, CENTS) == 0
    assert taxes.owed({**REVENUE, "rate": 120 * PERCENT}, 1_000, CENTS) == 1_200  # a rate may pass 100 %
    assert taxes.owed({"code": "x", "kind": "tax", "subject": "User", "facts": {}}, 1_000, CENTS) == 0  # a codex's


def test_a_rate_charges_to_the_cent_and_an_exact_half_goes_away_from_zero():
    tenth = {**REVENUE, "rate": 10 * PERCENT}
    assert taxes.owed(tenth, 2_345, CENTS) == 235  # 2.345 -> 2.35
    assert taxes.owed(tenth, 2_344, CENTS) == 234  # 2.344 -> 2.34
    unit = 10**8  # one token of an 8-decimal currency: a cent is a million smallest units
    assert taxes.owed({**REVENUE, "rate": 1_500_000}, unit, 8) == 2_000_000  # 0.015 -> 0.02
    assert taxes.owed({**REVENUE, "rate": 1_100_000}, unit, 8) == 1_000_000  # 0.011 -> 0.01
    brackets = {"brackets": [{"up_to": 1_000, "rate": 15 * PERCENT}, {"up_to": None, "rate": 25 * PERCENT}]}
    assert taxes.owed({**PROPERTY, "facts": brackets}, 1_010, CENTS) == 150 + 3  # 152.5 -> 153


def test_the_brackets_of_a_progressive_tax_add_up_exactly_and_round_once():
    halves = {"brackets": [{"up_to": 10, "rate": 25 * PERCENT}, {"up_to": None, "rate": 25 * PERCENT}]}
    # 10 units at 25 % are 2.5 and the next 10 are 2.5: 5 exactly, where rounding each bracket would make 3 and 3.
    assert taxes.owed({**PROPERTY, "facts": halves}, 20, CENTS) == 5


def test_policy_is_checked():
    setup_taxes()
    brackets = [{"up_to": 5, "rate": 1}]
    bad = [[], [RESIDENCE, RESIDENCE], [{**RESIDENCE, "code": ""}],
           [{**RESIDENCE, "subject": "Planet"}], [{**RESIDENCE, "kind": ""}], [{**RESIDENCE, "method": "lottery"}],
           [{**RESIDENCE, "facts": ["not", "a", "map"]}],
           [{**RESIDENCE, "rate": 1}], [{**PROPERTY, "rate": 1}],  # two ways to charge
           [{**REVENUE, "rate": RATE_MAX + 1}], [{**RESIDENCE, "amount": -1}],
           [{**PROPERTY, "facts": {"brackets": brackets}}]]  # brackets that never end
    for entries in bad:
        assert not call("tax_policy_draft", "bob", document=DOCUMENT, taxes=entries)["ok"], entries
    assert not call("tax_policy_draft", "bob", document=["symbol"], taxes=TAXES)["ok"]
    assert TaxPolicy.count() == 0


def test_a_rate_may_pass_a_hundred_percent_and_a_tax_may_leave_its_charge_to_the_codex():
    setup_taxes()
    adopt_policy([{**REVENUE, "rate": 150 * PERCENT}, {**RESIDENCE, "code": "by_codex", "amount": None}])
    assert [t.get("rate") or t.get("amount") for t in TaxPolicy["TAX-POLICY-1"].taxes] == [150 * PERCENT, None]


def test_flat_tax_skips_exempt_and_inactive_members():
    setup_taxes()
    world.profile("exempt")
    world.person("elena", "exempt")
    Member["mallory"].status = "suspended"
    adopt_policy([RESIDENCE])
    ok("tax_assess", "bob", period="2026", tax="residence")
    assert not invoices_of("User:elena") and not invoices_of("User:mallory") and invoices_of("User:alice") == {5}


def test_tax_assess_runs_in_batches_and_bills_nobody_twice():
    setup_taxes()
    Land(id="L-1", owner="User:alice", width=10, height=30)
    Land(id="L-2", owner="User:bob", width=20, height=40)
    Land(id="L-3", owner="User:alice", width=10, height=10)
    adopt_policy()
    first = ok("tax_assess", "bob", period="2026", count=2)
    assert first["next"] == 3 and first["count"] == 2 + 2
    second = ok("tax_assess", "bob", period="2026", count=2, **{"from": first["next"]})
    assert (second["count"], second["skipped"]) == (2, 1)
    while second["next"]:
        second = ok("tax_assess", "bob", period="2026", count=2, **{"from": second["next"]})
    assert invoices_of("User:alice") == {owed_on(400), 5} and invoices_of("User:bob") == {owed_on(800), 5}
    assert all(len(invoices_of(f"User:{p}")) == 1 for p in world.PEOPLE if p not in ("alice", "bob"))
    assert TaxDeclaration.count() == 2 + len(world.PEOPLE)


def test_tax_assess_leaves_self_declared_taxes_to_tax_declare():
    setup_taxes()
    Land(id="L-1", owner="User:alice", width=10, height=30)
    adopt_policy()
    assert "self-declared" in call("tax_assess", "bob", period="2026", tax="revenue")["error"]
    assert TaxDeclaration.count() == 0
    ok("tax_assess", "bob", period="2026")
    assert not [d for d in each(TaxDeclaration, "declarant", "User:alice") if d.base == "revenue"]


def test_tax_assess_needs_an_adopted_policy():
    setup_taxes()
    assert "no tax policy" in call("tax_assess", "bob", period="2026")["error"]


def test_tax_declare_rules_and_delegation():
    setup_taxes()
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


def test_a_member_reads_the_tax_policy_and_only_their_own_declarations():
    setup_taxes()
    assert ok("tax_policy_view", "mallory")["policy"] is None
    draft = ok("tax_policy_draft", "bob", document=DOCUMENT, taxes=TAXES)["policy"]
    seen = ok("tax_policy_view", "mallory")
    assert (seen["policy"], [d["id"] for d in seen["drafts"]]) == (None, [draft])

    ok("tax_policy_propose", "bob", policy=draft)
    ok("tax_assess", "bob", period="2026")
    ok("tax_declare", "alice", period="2026-Q3", base="revenue", amount=100)
    seen = ok("tax_policy_view", "mallory")
    assert (seen["policy"]["id"], seen["policy"]["document"], seen["policy"]["taxes"]) == (draft, DOCUMENT, TAXES)
    assert seen["drafts"] == []
    assert [(d["base"], d["status"]) for d in seen["declarations"]] == [("residence", "assessed")]

    mine = ok("tax_policy_view", "alice")["declarations"]
    assert sorted(d["base"] for d in mine) == ["residence", "revenue"]
    assert all(TaxDeclaration[d["id"]].declarant == "User:alice" for d in mine)


def test_tax_policy_view_gives_the_currency_and_decimals_and_caps_the_drafts():
    setup_taxes()
    seen = ok("tax_policy_view", "mallory")
    assert (seen["currency"], seen["decimals"]) == ("REALM", CENTS)
    drafts = [ok("tax_policy_draft", "bob", document=DOCUMENT, taxes=TAXES)["policy"] for _ in range(12)]
    assert [d["id"] for d in ok("tax_policy_view", "mallory")["drafts"]] == drafts[:1:-1]  # ten, newest first
    ok("tax_policy_propose", "bob", policy=drafts[0])
    assert ok("tax_policy_view", "mallory")["currency"] == "REALM"


def test_tax_policy_view_shows_an_organizations_declarations_to_its_administrators_only():
    setup_taxes()
    adopt_policy()
    ok("tax_declare", "dr-ana", organization="clinic-b", period="2026-Q3", base="revenue", amount=100)
    assert len(ok("tax_policy_view", "dr-ana", organization="clinic-b")["declarations"]) == 1
    assert "do not administer" in denied("tax_policy_view", "alice", organization="clinic-b")
    assert ok("tax_policy_view", "dr-ana")["declarations"] == []


def test_tax_policy_view_declarations_are_paginated():
    setup_taxes()
    adopt_policy()
    for quarter in range(1, 4):
        ok("tax_declare", "alice", period=f"2026-Q{quarter}", base="revenue", amount=1)
    first = ok("tax_policy_view", "alice", count=2)
    assert len(first["declarations"]) == 2 and first["next"]
    assert len(ok("tax_policy_view", "alice", count=2, **{"from": first["next"]})["declarations"]) == 1


def test_tax_policy_view_needs_a_signed_in_member():
    world.build()
    assert "sign in" in denied("tax_policy_view", fake_chain.ANONYMOUS)


def codex_answering(answer, asked=None):
    """A ``codex.compute`` that answers every question with ``answer``, noting what it was asked."""
    def compute(name, context, decimals=None):
        if asked is not None:
            asked.append((name, context["entry"]["code"], context["subject"]))
        return answer
        yield  # a generator, as the real one is
    return compute


def test_tax_assess_bills_the_items_the_codex_computes_for_each_subject_of_a_payer(monkeypatch):
    setup_taxes()
    Land(id="L-1", owner="User:alice", width=10, height=30)
    Land(id="L-2", owner="User:alice", width=1, height=1)
    Land(id="L-3", owner="User:bob", width=1, height=1)
    adopt_policy([PROPERTY])
    asked = []
    items = [{"concept": "property", "amount": 150, "legislation": "Art. 71"},
             {"concept": "property_bonus", "amount": -30, "legislation": "Art. 74"}]
    monkeypatch.setattr(codex, "compute", codex_answering(items, asked))
    out = ok("tax_assess", "bob", period="2026")
    assert asked == [("tax_assess", "property", s) for s in ("Land:L-1", "Land:L-2", "Land:L-3")]
    assert out["count"] == 2  # one invoice for each owner, whatever the number of parcels
    alice = next(i for i in each(Invoice, "payer", "User:alice"))
    assert alice.amount == 2 * 120 and alice.items == items + items
    assert sum(i["amount"] for i in alice.items) == alice.amount
    assert Invoice[out["invoices"][1]].amount == 120
    assert TaxDeclaration.count() == 2


def test_tax_assess_passes_the_entry_and_the_subject_to_the_codex(monkeypatch):
    setup_taxes()
    adopt_policy([RESIDENCE])
    seen = []

    def compute(name, context, decimals=None):
        seen.append((context, decimals))
        return [{"concept": "residence", "amount": 5}]
        yield

    monkeypatch.setattr(codex, "compute", compute)
    ok("tax_assess", "bob", period="2026", tax="residence")
    assert seen[0] == ({"entry": TAXES[1], "subject": "User:alice"}, CENTS)  # and the decimals of the token it is billed in
    assert {c["subject"] for c, _ in seen} == {f"User:{p}" for p in world.PEOPLE}


def test_a_codex_that_computes_nothing_leaves_no_invoice(monkeypatch):
    setup_taxes()
    adopt_policy([RESIDENCE])
    monkeypatch.setattr(codex, "compute", codex_answering([]))
    out = ok("tax_assess", "bob", period="2026")
    assert out["count"] == 0 and Invoice.count() == 0
    assert {d.status for d in each(TaxDeclaration, "key", "User:alice|residence|2026")} == {"settled"}


@pytest.mark.parametrize("answer", ["five", {"concept": "x", "amount": 5}, [{"concept": "x", "amount": 5.5}],
                                    [{"concept": "", "amount": 5}], [{"concept": "x", "amount": 5, "tip": 1}],
                                    [{"concept": "x", "amount": -5}], [{"concept": "x", "amount": 2**53}],
                                    [{"concept": "x", "amount": -2**53 - 1}, {"concept": "y", "amount": 2**53 + 6}]])
def test_a_codex_answer_that_is_not_invoice_items_bills_nobody(monkeypatch, answer):
    setup_taxes()
    adopt_policy([RESIDENCE])
    monkeypatch.setattr(codex, "compute", codex_answering(answer))
    assert not call("tax_assess", "bob", period="2026")["ok"]
    assert Invoice.count() == 0 and TaxDeclaration.count() == 0


def codex_answering_by_subject(answers, billed_meanwhile=None):
    """A ``codex.compute`` that answers each subject as ``answers`` says (``None`` for those it lacks). Asked for
    ``billed_meanwhile``'s subject, it also files the declaration another call would have, as a call can between two
    answers."""
    def compute(name, context, decimals=None):
        if context["subject"] == billed_meanwhile:
            TaxDeclaration(id="TD-other", declarant="User:alice", period="2026", base="residence",
                           key="User:alice|residence|2026", status="settled")
        return answers.get(context["subject"])
        yield
    return compute


def test_one_bad_answer_bills_nobody_even_the_payers_asked_before_it(monkeypatch):
    setup_taxes()
    adopt_policy([RESIDENCE])
    good = [{"concept": "residence", "amount": 5}]
    monkeypatch.setattr(codex, "compute", codex_answering_by_subject({"User:alice": good, "User:bob": "five"}))
    assert not call("tax_assess", "bob", period="2026")["ok"]
    assert Invoice.count() == 0 and TaxDeclaration.count() == 0


def test_a_codex_that_answers_some_subjects_of_a_payer_and_not_the_others_bills_nobody(monkeypatch):
    setup_taxes()
    Land(id="L-1", owner="User:alice", width=1, height=1)
    Land(id="L-2", owner="User:alice", width=1, height=1)
    adopt_policy([PROPERTY])
    good = [{"concept": "property", "amount": 5}]
    monkeypatch.setattr(codex, "compute", codex_answering_by_subject({"Land:L-1": good}))
    out = call("tax_assess", "bob", period="2026")
    assert not out["ok"] and "Land:L-2" in out["error"]
    assert Invoice.count() == 0 and TaxDeclaration.count() == 0


def test_a_payer_billed_while_the_codex_was_asked_is_not_billed_again(monkeypatch):
    setup_taxes()
    adopt_policy([RESIDENCE])
    good = [{"concept": "residence", "amount": 5}]
    answers = {f"User:{p}": good for p in world.PEOPLE}
    monkeypatch.setattr(codex, "compute", codex_answering_by_subject(answers, billed_meanwhile="User:mallory"))
    out = ok("tax_assess", "bob", period="2026")
    assert out["skipped"] == 1 and out["count"] == len(world.PEOPLE) - 1
    assert not invoices_of("User:alice")


def test_without_an_answer_the_codex_leaves_the_entry_its_own_charge(monkeypatch):
    setup_taxes()
    adopt_policy([RESIDENCE])
    monkeypatch.setattr(codex, "compute", codex_answering(None))
    ok("tax_assess", "bob", period="2026")
    assert invoices_of("User:alice") == {5} and Invoice[1].items == [{"concept": "residence", "amount": 5}]


def test_an_assessment_in_a_currency_of_eight_decimals_is_rounded_to_the_cent():
    setup_finance()  # REALM has 8 decimals: a cent is 10**6 smallest units
    Land(id="L-1", owner="User:alice", width=1, height=1)
    adopt_policy([{"code": "ibi", "kind": "tax", "subject": "Land", "rate": 1_500_000, "facts": {"unit_value": 10**8}}])
    ok("tax_assess", "bob", period="2026")
    (invoice,) = each(Invoice, "payer", "User:alice")
    assert invoice.amount == 2_000_000 == sum(i["amount"] for i in invoice.items)  # 0.015 is 0.02


def test_a_tax_policy_of_the_previous_release_migrates_to_typed_entries():
    setup_taxes()
    old = {"symbol": "REALM", "taxes": [
        {"code": "property", "base": "land_area", "unit_value": 100, "method": "progressive", "due_days": 100,
         "brackets": [{"up_to": 50_000, "rate": 40 * BASIS_POINT}, {"up_to": None, "rate": 80 * BASIS_POINT}]},
        {"code": "residence", "label": "Residence", "base": "flat", "method": "fixed", "amount": 5,
         "exempt_profiles": ["exempt"]},
        {"code": "revenue", "base": "declared", "method": "proportional", "rate": 200 * BASIS_POINT}]}
    TaxPolicy(id="TAX-POLICY-1", document=old, status="adopted")
    stored_as_version_1(TaxPolicy, "TAX-POLICY-1", drop=("taxes",))
    policy = TaxPolicy["TAX-POLICY-1"]
    assert policy.document == {"symbol": "REALM"}
    assert policy.taxes == [PROPERTY, {**RESIDENCE, "kind": "tax", "facts": {"label": "Residence",
                                                                             "exempt_profiles": ["exempt"]}}, REVENUE]
    Land(id="L-1", owner="User:alice", width=10, height=30)
    ok("tax_assess", "bob", period="2026")  # the migrated policy is assessed as the old one was
    assert invoices_of("User:alice") == {120, 5}


def test_a_draft_left_waiting_in_the_previous_form_is_refused_with_where_the_taxes_went():
    setup_taxes()
    old = {"taxes": [{"code": "residence", "base": "flat", "method": "fixed", "amount": 5}]}
    assert "taxes" in denied("tax_policy_draft", "bob", document=old)
    assert TaxPolicy.count() == 0
