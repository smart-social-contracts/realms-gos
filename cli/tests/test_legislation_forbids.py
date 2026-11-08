"""The ``F`` expression language of forbids and the Cedar it becomes (docs/codices/layered-codex.md §5.6,
§5.8, §6, §9.4, §12.3; realms-gos#484): the forbids of §5.6 and §4 produce the Cedar of §5.8, Python and
Cedar agree on generated facts (the literals ±1, 0 and the 64-bit extremes among them), a Period
comparison is true for another unit, alone and under ``~`` with other facts, and every forbid never errors.

Cedar runs here through ``cedarpy`` (Cedar 4.12, the canister's line) for validation and decisions, and
through the pinned Cedar CLI 4.14.1 with cvc5 1.3.1 (``scripts/cedar_tools.sh``) for the ``never-errors``
proof, when ``CEDAR_CLI`` and ``CVC5`` point at them.
"""

import os
import subprocess

import cedarpy
import pytest

from realms.legislation import (
    Codex,
    F,
    LegislationError,
    Refused,
    days,
    forbid,
    months,
    pct,
    subset_ast,
    when,
    years,
)
from realms.cli.commands.legislation_analysis import (
    Draw,
    agree,
    never_errors_problems,
    symcc,
)
from realms.legislation.cedar import FactSchema
from realms.legislation.expressions import I64_MAX, I64_MIN

LAYERS = ("eu", "eu/es", "eu/es/vc", "eu/es/vc/46250-valencia")
VALENCIA = "eu/es/vc/46250-valencia"
OPERATIONS = (
    "tax_policy_propose",
    "tax_policy_draft",
    "procedure_define",
    "tax_assess",
    "document_share",
)
RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
TAX_ENTRY = {"code": "String", "rate": RATE}
# GGG's fact schema for the design's operations (ggg docs/layered-codex.md §3.3), as D4 will supply it,
# plus a `document_share` with every kind of fact, for the edge cases.
FACTS = {
    "tax_policy_propose": {"facts": {"policy": "String"}},
    "tax_policy_propose.taxes": {
        "facts": TAX_ENTRY,
        "also": ["tax_policy_draft.taxes"],
    },
    "tax_policy_draft.taxes": {"facts": TAX_ENTRY},
    "procedure_define": {
        "facts": {
            "decision_deadline": {"type": "Period", "optional": True},
            "longer_deadline_legislation": {"type": "String", "optional": True},
        }
    },
    "document_share": {
        "facts": {
            "kind": "String",
            "rate": RATE,
            "surcharge": {
                "type": "Long",
                "optional": True,
                "min": 0,
                "max": 100_000_000,
            },
            "pages": {"type": "Long", "min": 0, "max": 10_000},
            "unbounded": "Long",
            "urgent": "Bool",
            "tags": "Set<String>",
            "retention": {"type": "Period", "optional": True},
            "in": {"type": "Long", "optional": True},
        }
    },
}

ES_TAX = """# legislation/eu/es/tax.py (§5.6)
from realms.legislation import F, S, above, fixed, forbid, overridable, pct, when

class Tax(above("tax")):
    capital = overridable(False, legislation="TRLRHL Art. 72.3")
    surface_public_transport = overridable(False, legislation="TRLRHL Art. 72.3")
    services_beyond_art26 = overridable(False, legislation="TRLRHL Art. 72.3")
    rustic_over_80pct = overridable(False, legislation="TRLRHL Art. 72.3")

    ibi_rate_required = forbid("tax_policy_propose",
        F.code.in_(["ibi_urban", "ibi_rustic"]) & F.rate.is_none(),
        each="taxes", legislation="TRLRHL Art. 72")

    ibi_urban_range = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & ~F.rate.between(pct("0.40"), pct("1.30")),
        each="taxes", legislation="TRLRHL Art. 72.1, 72.3")

    ibi_urban_cap = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10")
            + when(S.capital, pct("0.07")) + when(S.surface_public_transport, pct("0.07"))
            + when(S.services_beyond_art26, pct("0.06"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    ibi_rustic_cap = forbid("tax_policy_propose",
        (F.code == "ibi_rustic") & F.rate.is_some() & (F.rate > pct("0.90")
            + when(S.capital, pct("0.06")) + when(S.surface_public_transport, pct("0.05"))
            + when(S.services_beyond_art26, pct("0.06")) + when(S.rustic_over_80pct, pct("0.15"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    @fixed(legislation="TRLRHL Arts. 61, 65, 71 and 74")
    def compute_tax_assess(self, entry, subject):
        return []
"""
VALENCIA_TAX = """# legislation/eu/es/vc/46250-valencia/tax.py (§5.6)
from realms.legislation import above, pct

class Tax(above("tax")):
    capital = True
    surface_public_transport = True
    large_family_bonus = {"general": pct("60"), "special": pct("90")}
"""
ES_PROCEDURE = """# legislation/eu/es/procedure.py (§4)
from realms.legislation import F, above, fixed, forbid, months, overridable, working_days

class Procedure(above("procedure")):
    uses = []

    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

    over_six_months = forbid("procedure_define",
        F.decision_deadline.is_some() & (F.decision_deadline > months(6))
        & F.longer_deadline_legislation.is_none(),
        legislation="Ley 39/2015 Art. 21.2")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))
"""
# Every form of the language on one operation: the agreement test draws its facts from these.
ES_DATA_PROTECTION = """
from realms.legislation import F, S, above, forbid, months, overridable, pct, when, years

class DataProtection(above("data_protection")):
    cap = overridable(pct("1.30"), legislation="x")
    minimum_retention = overridable(months(6), legislation="x")
    sensitive = overridable(["health", "religion"], legislation="x")
    strict = overridable(True, legislation="x")

    two_facts_added = forbid("document_share",
        F.rate.is_some() & F.surcharge.is_some() & (F.rate + F.surcharge > S.cap),
        legislation="x")
    negated_sum = forbid("document_share",
        F.rate.is_some() & F.surcharge.is_some() & ~(F.rate - F.surcharge <= pct("0.5")) & (F.kind == "n"),
        legislation="x")
    pages_cap = forbid("document_share",
        (F.kind == "report") & (F.pages > 100 + when(F.urgent, F.pages - 50, 0)),
        legislation="x")
    tagged = forbid("document_share",
        F.tags.contains("secret") & ~(F.urgent | F.kind.in_(S.sensitive)),
        legislation="x")
    short_retention = forbid("document_share",
        F.retention.is_some() & (F.retention < S.minimum_retention) & S.strict,
        legislation="x")
    not_a_year = forbid("document_share",
        F.retention.is_some() & ~(F.retention == years(1)) & (F.kind == "archive"),
        legislation="x")
    negated_period_with_others = forbid("document_share",
        ~(F.retention.is_some() & (F.retention >= months(1))) & F.rate.is_none(),
        legislation="x")
    unbounded_between = forbid("document_share",
        (F.kind == "between") & F.unbounded.between(-5, S.cap),
        legislation="x")
"""
# A fact named with a word Cedar reserves: only getattr() can write it, which the syntax-tree check
# of `realms legislation check` does not allow in a class body (the loader does).
RESERVED_NAME = """
from realms.legislation import F, above, forbid

class Tax(above("tax")):
    reserved_name = forbid("document_share",
        getattr(F, "in").is_some() & (getattr(F, "in") != 3),
        legislation="x")
"""


def codex(realm=VALENCIA, layers=LAYERS, facts=FACTS):
    return Codex(layers, realm=realm, operations=OPERATIONS, facts=facts)


def valencia():
    c = codex()
    c.load("eu/es", "tax", ES_TAX)
    c.load(VALENCIA, "tax", VALENCIA_TAX)
    c.load("eu/es", "procedure", ES_PROCEDURE)
    return c


def with_edges():
    c = valencia()
    c.load("eu/es", "data_protection", ES_DATA_PROTECTION)
    return c


def forbid_chapter(body, imports="F, S, above, forbid, months, pct, when"):
    """A Spain tax chapter holding one forbid ``rule``."""
    return f"from realms.legislation import {imports}\nclass Tax(above('tax')):\n    rule = {body}\n"


def refused(text, source="eu/es", domain="tax", c=None):
    """Load ``text`` into a fresh codex (or ``c``) and bind its forbids; the refusal, as printed."""
    c = c if c is not None else codex()
    with pytest.raises(Refused) as e:
        c.load(source, domain, text)
        c.forbids()
    return str(e.value)


# ── §5.8: the generated Cedar ─────────────────────────────────────────────────

IBI_URBAN_CAP = """@id("eu/es/tax/ibi_urban_cap")
@legislation("TRLRHL Art. 72.3")
forbid (principal,
        action in [Realm::Action::"tax_policy_propose.taxes",
                   Realm::Action::"tax_policy_draft.taxes"],
        resource)
  when { context.facts.code == "ibi_urban"
         && context.facts has rate
         && context.facts.rate > 1100000
              + (if true then 70000 else 0)
              + (if true then 70000 else 0)
              + (if false then 60000 else 0) };"""
OVER_SIX_MONTHS = """@id("eu/es/procedure/over_six_months")
@legislation("Ley 39/2015 Art. 21.2")
forbid (principal, action == Realm::Action::"procedure_define", resource)
  when { context.facts has decision_deadline
         && (context.facts.decision_deadline.unit != "Month" || context.facts.decision_deadline.count > 6)
         && !(context.facts has longer_deadline_legislation) };"""
IBI_URBAN_RANGE = """  when { context.facts.code == "ibi_urban"
         && context.facts has rate
         && !(context.facts.rate >= 400000 && context.facts.rate <= 1300000) };"""
IBI_RATE_REQUIRED = """  when { ["ibi_urban","ibi_rustic"].contains(context.facts.code)
         && !(context.facts has rate) };"""


def test_the_forbids_of_5_6_and_4_produce_the_cedar_of_5_8():
    text = valencia().cedar()
    assert IBI_URBAN_CAP in text and OVER_SIX_MONTHS in text
    assert IBI_URBAN_RANGE in text and IBI_RATE_REQUIRED in text
    assert text.count("@id(") == 5 and text.endswith(";\n") and "\n\n\n" not in text
    rustic = text[text.index('@id("eu/es/tax/ibi_rustic_cap")') :]
    assert (
        "context.facts.rate > 900000\n              + (if true then 60000 else 0)\n"
        in rustic
    )
    assert (
        "+ (if true then 50000 else 0)\n              + (if false then 60000 else 0)\n              + (if false then 150000 else 0) };"
        in rustic
    )


def test_the_output_is_canonical_and_in_a_fixed_order():
    assert valencia().cedar() == valencia().cedar()
    ids = [f.id for f in valencia().forbids()]
    # Layers top to bottom, domains by name, a chapter's forbids as written.
    assert ids == [
        "eu/es/procedure/over_six_months",
        "eu/es/tax/ibi_rate_required",
        "eu/es/tax/ibi_urban_range",
        "eu/es/tax/ibi_urban_cap",
        "eu/es/tax/ibi_rustic_cap",
    ]
    c = valencia()
    c.load(
        VALENCIA,
        "procedure",
        "from realms.legislation import F, above, forbid\nclass Procedure(above('procedure')):\n"
        "    no_legislation_needed = forbid('procedure_define', F.longer_deadline_legislation.is_some())\n",
    )
    assert [f.id for f in c.forbids()][
        -1
    ] == "eu/es/vc/46250-valencia/procedure/no_legislation_needed"
    own = c.cedar().split("\n\n")[-1]
    assert own.startswith(
        '@id("eu/es/vc/46250-valencia/procedure/no_legislation_needed")\nforbid ('
    )  # no @legislation


def test_settings_fold_to_their_values_in_the_realm():
    c = codex()
    c.load("eu/es", "tax", ES_TAX)  # no València layer: Spain's defaults, all false
    text = c.cedar()
    assert (
        "context.facts.rate > 1100000\n              + (if false then 70000 else 0)\n              + (if false then 70000 else 0)"
        in text
    )
    cap = [f for f in c.forbids() if f.id.endswith("ibi_urban_cap")][0]
    assert dict(cap.settings) == {
        "capital": False,
        "surface_public_transport": False,
        "services_beyond_art26": False,
    }
    assert (
        cap.actions == ("tax_policy_propose.taxes", "tax_policy_draft.taxes")
        and cap.legislation == "TRLRHL Art. 72.3"
    )
    assert cap.evaluate({"code": "ibi_urban", "rate": 1_100_001}) and not cap.evaluate(
        {"code": "ibi_urban", "rate": 1_100_000}
    )
    valencian = [f for f in valencia().forbids() if f.id.endswith("ibi_urban_cap")][0]
    assert not valencian.evaluate(
        {"code": "ibi_urban", "rate": 1_240_000}
    ) and valencian.evaluate({"code": "ibi_urban", "rate": 1_240_001})
    assert not valencian.evaluate(
        {"code": "ibi_urban"}
    )  # no rate: is_some() fails, nothing is read


def test_the_generic_output_leaves_the_settings_open():
    c = valencia()
    text, schema = c.cedar(generic=True), c.cedar_schema(
        generic=True, context={"op": "String"}
    )
    assert (
        "+ (if context.settings.tax.capital then 70000 else 0)\n              + (if context.settings.tax.surface_public_transport then 70000 else 0)"
        in text
    )
    assert (
        "settings: { tax: { capital: Bool, rustic_over_80pct: Bool, services_beyond_art26: Bool, surface_public_transport: Bool } }"
        in schema
    )
    assert (
        'action "procedure_define" in ["call"] appliesTo { principal: [User], resource: [Realm], context: { op: String, facts: { decision_deadline?: Period, longer_deadline_legislation?: String } } };'
        in schema
    )
    assert "settings" not in schema.split('action "procedure_define"')[1].split("\n")[0]
    folded = c.cedar_schema(context={"op": "String"})
    assert (
        "settings" not in folded
        and 'action "call" appliesTo { principal: [User], resource: [Realm], context: { op: String } };'
        in folded
    )
    assert (
        c.cedar_schema().count("context: {}") == 1
    )  # no context fields given: the call action's context is empty


@pytest.mark.parametrize("generic", [False, True])
def test_the_cedar_validates_strictly_against_the_generated_schema(generic):
    c = with_edges()
    result = cedarpy.validate_policies(
        c.cedar(generic), c.cedar_schema(generic, context={"op": "String"})
    )
    assert result.validation_passed, [str(e) for e in result.errors]


def test_a_chapter_with_forbids_passes_the_syntax_tree_check_too():
    for text, file in (
        (ES_TAX, "eu/es/tax.py"),
        (ES_PROCEDURE, "eu/es/procedure.py"),
        (ES_DATA_PROTECTION, "eu/es/data_protection.py"),
    ):
        subset_ast.check(text, file)


# ── Python and Cedar agree (§6 rule 7) ────────────────────────────────────────


def test_python_and_cedar_agree_on_10000_generated_fact_sets():
    assert agree(with_edges(), generic=False, per_action=2500) == (10_000, [])


def test_python_and_cedar_agree_with_the_settings_left_open():
    assert agree(with_edges(), generic=True, per_action=1000) == (4_000, [])


class Rewritten:
    """A codex or a forbid whose Cedar is ``rewrite`` of what it generated: the Cedar says one thing, Python another."""

    def __init__(self, inner, rewrite):
        self.inner, self.rewrite = inner, rewrite

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def cedar(self, generic=False):
        text = self.inner.cedar(generic)
        return (
            self.rewrite(text)
            if isinstance(text, str)
            else "".join(self.rewrite(t) for t in text)
        )

    def forbids(self):
        return [
            Rewritten(f, self.rewrite) if hasattr(f, "cedar") else f
            for f in self.inner.forbids()
        ]


def test_agree_reports_where_cedar_decides_otherwise_than_python():
    c = with_edges()
    assert agree(c, generic=False, per_action=300)[1] == []
    checked, problems = agree(
        Rewritten(c, lambda cedar: cedar.replace("<= 1300000", "<= 1300001")),
        generic=False,
        per_action=2500,
    )
    assert checked == 10_000 and problems
    assert any(
        "Cedar forbids" in p and "Python" in p and "ibi_urban_range" in p
        for p in problems
    ), problems[:3]


def test_agree_reports_a_forbid_that_makes_cedar_error():
    c = with_edges()

    def erroring(cedar):
        return cedar.replace(
            "context.facts.rate >= 400000",
            "context.facts.rate + 9223372036854775807 >= 400000",
        )

    problems = agree(Rewritten(c, erroring), generic=False, per_action=2500)[1]
    assert any("Cedar errors on" in p for p in problems), problems[:3]


def test_the_generated_facts_reach_the_edges_and_both_decisions():
    c = with_edges()
    draw = Draw(2026, c.forbids())
    assert {
        0,
        I64_MIN,
        I64_MAX,
        1_100_000,
        1_099_999,
        1_100_001,
        1_300_000,
        1_300_001,
    } <= set(draw.ints)
    # Folded comparison endpoints and Period counts, each ±1.
    assert {
        1_240_000,
        1_239_999,
        1_240_001,
        1_010_000,
        1_009_999,
        5,
        6,
        7,
        1,
        2,
    } <= set(draw.ints)
    on_action = [f for f in c.forbids() if "document_share" in f.actions]
    facts = [draw.facts(c.facts.actions["document_share"]) for _ in range(1000)]
    for f in on_action:  # every forbid fires on some draws and stays quiet on others
        fired = sum(f.evaluate(facts_) for facts_ in facts)
        assert 20 < fired < 980, (f.id, fired)


# ── Periods (§6, the Period row and polarity) ─────────────────────────────────


def period_forbid(expression, imports="F, above, forbid, months, years"):
    c = codex()
    c.load(
        "eu/es",
        "procedure",
        f"from realms.legislation import {imports}\nclass Procedure(above('procedure')):\n    rule = {expression}\n",
    )
    return c.forbids()[0]


def test_a_period_comparison_is_true_for_another_unit():
    alone = period_forbid(
        "forbid('procedure_define', F.decision_deadline.is_some() & (F.decision_deadline > months(6)), legislation='x')"
    )
    assert (
        '&& (context.facts.decision_deadline.unit != "Month" || context.facts.decision_deadline.count > 6) };'
        in alone.cedar()
    )
    assert alone.evaluate({"decision_deadline": months(7)}) and not alone.evaluate(
        {"decision_deadline": months(6)}
    )
    assert alone.evaluate({"decision_deadline": days(60)}) and alone.evaluate(
        {"decision_deadline": years(1)}
    )
    assert alone.evaluate(
        {"decision_deadline": {"count": 1, "unit": "WorkingDay"}}
    )  # as GGG stores it
    assert not alone.evaluate({"decision_deadline": None}) and not alone.evaluate({})


def test_a_negated_period_comparison_still_counts_another_unit_towards_the_forbid():
    negated = period_forbid(
        "forbid('procedure_define', F.decision_deadline.is_some() & ~(F.decision_deadline > months(6)), legislation='x')"
    )
    assert (
        '&& !(context.facts.decision_deadline.unit == "Month" && context.facts.decision_deadline.count > 6) };'
        in negated.cedar()
    )
    assert negated.evaluate({"decision_deadline": months(6)}) and not negated.evaluate(
        {"decision_deadline": months(7)}
    )
    assert negated.evaluate(
        {"decision_deadline": days(60)}
    )  # not "more than six months": another unit, the forbid fires
    # §6's example: a ~ over other facts.
    doc = period_forbid(
        "forbid('procedure_define', ~(F.decision_deadline.is_some() & (F.decision_deadline >= months(1))), legislation='x')"
    )
    assert doc.cedar().endswith(
        '  when { !(context.facts has decision_deadline && (context.facts.decision_deadline.unit == "Month" '
        "&& context.facts.decision_deadline.count >= 1)) };"
    )
    assert (
        doc.evaluate({"decision_deadline": days(60)})
        and doc.evaluate({})
        and not doc.evaluate({"decision_deadline": months(2)})
    )
    # Twice negated is the plain form again.
    twice = period_forbid(
        "forbid('procedure_define', F.decision_deadline.is_some() & ~~(F.decision_deadline > months(6)), legislation='x')"
    )
    assert (
        '!(!(context.facts.decision_deadline.unit != "Month" || context.facts.decision_deadline.count > 6))'
        in twice.cedar()
    )
    assert twice.evaluate({"decision_deadline": days(1)}) and twice.evaluate(
        {"decision_deadline": months(7)}
    )


def test_a_period_setting_compares_like_the_period_a_rule_names():
    c = with_edges()
    short = [f for f in c.forbids() if f.id.endswith("short_retention")][0]
    assert (
        '&& (context.facts.retention.unit != "Month" || context.facts.retention.count < 6)\n         && true };'
        in short.cedar()
    )
    assert "(context.facts.retention.unit != context.settings.data_protection.minimum_retention.unit || "
    "context.facts.retention.count < context.settings.data_protection.minimum_retention.count)" in short.cedar(
        generic=True
    )
    assert (
        short.evaluate({"retention": months(5)})
        and short.evaluate({"retention": days(5)})
        and not short.evaluate({"retention": months(6)})
    )
    assert not short.evaluate(
        {"retention": months(5)}, {"minimum_retention": months(5), "strict": True}
    )


def test_a_period_comparison_cannot_be_the_condition_of_when():
    with pytest.raises(LegislationError, match="can't be the condition of when"):
        when(F.decision_deadline > months(6), 1, 0)
    with pytest.raises(LegislationError, match="can't be the condition of when"):
        when(~(F.decision_deadline > months(6)), 1, 0)
    # Against a Period setting the comparison is only known to be one once the setting is read.
    out = refused(
        "from realms.legislation import F, S, above, forbid, months, overridable, when\n"
        "class DataProtection(above('data_protection')):\n"
        "    minimum_retention = overridable(months(6), legislation='x')\n"
        "    rule = forbid('document_share', F.retention.is_some() & (F.pages > when(F.retention > S.minimum_retention, 1, 0)), legislation='x')\n",
        domain="data_protection",
    )
    assert "can't be the condition of when()" in out


def test_a_period_compares_only_with_the_period_the_rule_names():
    with pytest.raises(LegislationError, match="a Period is compared with a fact"):
        F.decision_deadline + 1 > months(6)
    assert "compares with the Period the rule names" in refused(
        "from realms.legislation import F, above, forbid, months\nclass Procedure(above('procedure')):\n"
        "    rule = forbid('procedure_define', F.decision_deadline.is_some() & (F.decision_deadline > 6), legislation='x')\n",
        domain="procedure",
    )
    assert "compares with the Period the rule names" in refused(
        "from realms.legislation import F, above, forbid, months\nclass Tax(above('tax')):\n"
        "    rule = forbid('tax_policy_propose', F.code == months(6), each='taxes', legislation='x')\n"
    )


# ── Never errors (§6 rule 9): fact arithmetic is bounded ──────────────────────


def test_fact_arithmetic_is_bounded_by_the_fact_schemas_ranges():
    c = with_edges()
    added = [f for f in c.forbids() if f.id.endswith("two_facts_added")][0]
    assert added.cedar().endswith(
        "  when { context.facts has rate\n         && context.facts has surcharge\n"
        "         && (context.facts.rate < 0 || context.facts.rate > 100000000 || context.facts.surcharge < 0 "
        "|| context.facts.surcharge > 100000000 || context.facts.rate + context.facts.surcharge > 1300000) };"
    )
    assert added.evaluate(
        {"rate": 2**62, "surcharge": 2**62}
    )  # out of range: the forbid fires instead of erroring
    assert added.evaluate(
        {"rate": 1_000_000, "surcharge": 300_001}
    ) and not added.evaluate({"rate": 1_000_000, "surcharge": 300_000})
    assert added.evaluate({"rate": -1, "surcharge": 0})
    negated = [f for f in c.forbids() if f.id.endswith("negated_sum")][0]
    assert (
        "&& !(context.facts.rate >= 0 && context.facts.rate <= 100000000 && context.facts.surcharge >= 0 "
        "&& context.facts.surcharge <= 100000000 && context.facts.rate - context.facts.surcharge <= 500000)"
    ) in negated.cedar()
    assert negated.evaluate(
        {"rate": I64_MAX, "surcharge": 1, "kind": "n"}
    )  # out of range still counts towards the forbid
    assert not negated.evaluate(
        {"rate": 500_000, "surcharge": 0, "kind": "n"}
    ) and negated.evaluate({"rate": 500_001, "surcharge": 0, "kind": "n"})
    pages = [f for f in c.forbids() if f.id.endswith("pages_cap")][0]
    assert (
        "(context.facts.pages < 0 || context.facts.pages > 10000 || context.facts.pages > 100\n              + (if context.facts.urgent then context.facts.pages - 50 else 0))"
        in pages.cedar()
    )


def test_arithmetic_on_a_fact_with_no_range_is_refused():
    out = refused(
        forbid_chapter("forbid('document_share', F.unbounded + 1 > 5, legislation='x')")
    )
    assert (
        "F.unbounded is read inside arithmetic, but the fact schema gives it no range"
        in out
    )
    with pytest.raises(
        LegislationError, match="can't be compared in the condition of when"
    ):
        when(F.pages + 1 > 5, 1, 0)


def test_arithmetic_that_could_overflow_within_the_ranges_is_refused():
    big = 2**62
    assert "may overflow Cedar's 64-bit integer" in refused(
        forbid_chapter(
            f"forbid('document_share', F.pages + {big} + {big} > 1, legislation='x')"
        )
    )
    # Cedar adds left to right: the first two terms overflow even though the sum of all three fits.
    assert "may overflow Cedar's 64-bit integer" in refused(
        forbid_chapter(
            f"forbid('document_share', F.pages + {big} + {big} - {big} - {big} > 1, legislation='x')"
        )
    )
    with pytest.raises(LegislationError, match="does not fit Cedar's 64-bit integer"):
        F.pages > 2**63
    with pytest.raises(LegislationError, match="must fit Cedar's 64-bit integer"):
        F.retention > months(2**63)


@pytest.fixture(scope="module")
def cedar_tools():
    cli, cvc5 = os.environ.get("CEDAR_CLI"), os.environ.get("CVC5")
    if not (cli and cvc5 and os.path.exists(cli) and os.path.exists(cvc5)):
        pytest.skip(
            "set CEDAR_CLI and CVC5 to the pinned Cedar CLI 4.14.1 and cvc5 1.3.1 (scripts/cedar_tools.sh)"
        )
    assert (
        "4.14.1"
        in subprocess.run([cli, "--version"], capture_output=True, text=True).stdout
    )
    return cli, cvc5


def test_every_generated_forbid_never_errors(cedar_tools):
    """SymCC's proof (R1, #481) on the installed Cedar of every forbid of the design's chapters and the
    edge cases, for every action each one names."""
    c = with_edges()
    assert never_errors_problems(c, *cedar_tools) == []
    assert (
        sum(len(f.actions) for f in c.forbids()) == 13 + 4
    )  # 4 forbids on two actions each, the others on one


def test_the_unbounded_sum_would_error_and_the_bounded_one_does_not(cedar_tools):
    """The R1 finding, reproduced on the generator's output: the bounded form of F.rate + F.surcharge
    passes never-errors; the same comparison written by hand without the bounds does not.
    """
    c = with_edges()
    added = [f for f in c.forbids() if f.id.endswith("two_facts_added")][0]
    schema = c.cedar_schema(context={"op": "String"})
    unbounded = added.cedar().replace(
        "(context.facts.rate < 0 || context.facts.rate > 100000000 || context.facts.surcharge < 0 || context.facts.surcharge > 100000000 || ",
        "(",
    )
    assert unbounded != added.cedar()
    proved = {
        name: symcc(*cedar_tools, schema, text, "document_share")[0]
        for name, text in (("bounded", added.cedar()), ("unbounded", unbounded))
    }
    assert proved == {"bounded": True, "unbounded": False}


def test_never_errors_problems_names_the_forbid_that_can_error_on_each_action(
    cedar_tools,
):
    c = with_edges()

    def bounds_removed(cedar):
        return cedar.replace(
            "(context.facts.rate < 0 || context.facts.rate > 100000000 || context.facts.surcharge < 0 || context.facts.surcharge > 100000000 || ",
            "(",
        )

    problems = never_errors_problems(Rewritten(c, bounds_removed), *cedar_tools)
    assert (
        len(problems) == 1
        and "two_facts_added can error on document_share" in problems[0]
    ), problems
    assert "DOES NOT HOLD" in problems[0]
    memo = {}
    assert (
        never_errors_problems(c, *cedar_tools, memo) == [] and len(memo) == 17
    )  # one answer per Cedar text and action


def test_a_forbid_that_cedar_cannot_prove_is_reported_with_the_request_that_errors(
    cedar_tools,
):
    c = with_edges()
    added = [f for f in c.forbids() if f.id.endswith("two_facts_added")][0]
    unbounded = added.cedar().replace(
        "(context.facts.rate < 0 || context.facts.rate > 100000000 || context.facts.surcharge < 0 || context.facts.surcharge > 100000000 || ",
        "(",
    )
    proved, said = symcc(
        *cedar_tools,
        c.cedar_schema(context={"op": "String"}),
        unbounded,
        "document_share",
    )
    assert proved is False and "DOES NOT HOLD" in said


# ── The rules of §6 ───────────────────────────────────────────────────────────


def test_rule_2_plain_and_or_not_and_chained_comparisons_are_refused():
    for bad in (
        lambda: F.a == 1 and F.b == 2,
        lambda: not F.a,
        lambda: pct("0.40") <= F.x <= pct("1.30"),
        lambda: bool(F.x),
    ):
        with pytest.raises(
            LegislationError,
            match="use &, | and ~ in a forbid; see legislation/README.md",
        ):
            bad()
    with pytest.raises(LegislationError, match="with each comparison in parentheses"):
        F.a == 1 & F.b == 2  # noqa: B015 - Python reads it as F.a == (1 & F.b) == 2
    with pytest.raises(LegislationError, match="is not a condition"):
        F.a & 3


def test_rule_1_literals_are_int_str_bool_and_lists_of_those():
    for bad, reason in (
        (1.5, "no floats"),
        (None, "cannot be None"),
        ([1], "a list belongs in F.x.in_"),
        (object(), "not an operand"),
    ):
        with pytest.raises(LegislationError, match=reason):
            F.x == bad
    with pytest.raises(LegislationError, match="non-empty list"):
        F.x.in_([])
    with pytest.raises(LegislationError, match="a list of str or a list of int"):
        F.x.in_(["a", 1])
    assert repr(F.x.in_(["a", "b"])) == "F.x.in_(('a', 'b'))"
    assert (
        repr(~(F.a + 1 - 2 > 3) & (F.b == "c") | F.d.between(1, 2))
        == "((~((F.a + 1 - 2) > 3) & (F.b == 'c')) | F.d.between(1, 2))"
    )


def test_rule_3_an_optional_fact_is_compared_after_is_some_in_the_same_chain():
    ok = (
        "F.rate.is_some() & (F.rate > 1)",
        "F.rate.is_none() | (F.rate.is_some() & (F.rate > 1))",
        "(F.kind == 'a') & F.rate.is_some() & (F.rate > 1)",
        "(F.urgent & F.rate.is_some()) & (F.rate > 1)",  # established through a nested & group
    )
    for text in ok:
        c = codex()
        c.load(
            "eu/es",
            "tax",
            forbid_chapter(f"forbid('document_share', {text}, legislation='x')"),
        )
        c.forbids()
    for text in (
        "F.rate > 1",
        "F.rate.is_none() | (F.rate > 1)",
        "(F.rate > 1) & F.rate.is_some()",
        "~F.rate.is_some() | (F.rate > 1)",
    ):
        out = refused(
            forbid_chapter(f"forbid('document_share', {text}, legislation='x')")
        )
        assert (
            "F.rate is optional: compare it after F.rate.is_some() in the same & chain (rule 3)"
            in out
        ), text
    # The then-branch of when() knows what its condition established, as Cedar's validator does.
    then = codex()
    then.load(
        "eu/es",
        "tax",
        forbid_chapter(
            "forbid('document_share', F.pages > when(F.rate.is_some(), F.pages - 1, 0), legislation='x')"
        ),
    )
    assert (
        "(if context.facts has rate then context.facts.pages - 1 else 0)"
        in then.cedar()
    )
    assert "F.rate is optional" in refused(
        forbid_chapter(
            "forbid('document_share', F.pages > when(F.urgent, F.rate, 0), legislation='x')"
        )
    )


def test_rule_4_s_reads_a_setting_of_the_chapters_own_domain():
    c = codex()
    c.load("eu/es", "procedure", ES_PROCEDURE)
    assert "rule: S.capital: procedure has no setting capital" in refused(
        "from realms.legislation import F, S, above, forbid\nclass Procedure(above('procedure')):\n"
        "    uses = ['tax']\n    rule = forbid('procedure_define', S.capital & F.longer_deadline_legislation.is_none(), legislation='x')\n",
        domain="procedure",
        c=codex(),
    )
    assert "S.working_days_after: procedure has no setting working_days_after" in refused(
        "from realms.legislation import F, S, above, forbid\nclass Procedure(above('procedure')):\n"
        "    rule = forbid('procedure_define', S.working_days_after & F.longer_deadline_legislation.is_none(), legislation='x')\n",
        source=VALENCIA,
        domain="procedure",
        c=c,
    )
    # The setting resolves through the realm's layers: a lower layer may fill it (§4.3).
    c2 = codex()
    c2.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable\nclass Tax(above('tax')):\n"
        "    population = overridable(None, legislation='x')\n"
        "    rule = forbid('tax_policy_propose', (F.code == 'ibi_urban') & (S.population > 500000), each='taxes', legislation='x')\n",
    )
    with pytest.raises(Refused, match="S.population is not set in this realm"):
        c2.forbids()
    c2.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    population = 807_693\n",
    )
    assert "&& 807693 > 500000 };" in c2.cedar() and dict(c2.forbids()[0].settings) == {
        "population": 807_693
    }
    c3 = codex()
    c3.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable, pct\nclass Tax(above('tax')):\n"
        "    bonus = overridable({'general': pct('60')}, legislation='x')\n"
        "    rule = forbid('tax_policy_propose', (F.code == 'ibi_urban') & (S.bonus == 1), each='taxes', legislation='x')\n",
    )
    with pytest.raises(Refused, match="S.bonus is a dict"):
        c3.forbids()


@pytest.mark.parametrize(
    "expression, reason",
    [
        ("F.kind > 1", "Long and String do not compare"),
        ("F.kind == 1", "String and Long do not compare"),
        ("F.urgent + 1 > 1", "arithmetic needs integers"),
        ("F.tags.contains(1)", "String and Long do not compare"),
        ("F.kind.contains('a')", "the fact is a String, not a set"),
        ("F.kind.in_([1, 2])", "Set<String> and Set<Long> do not compare"),
        ("F.tags == F.tags", "a set fact is tested with contains"),
        ("F.urgent.between(1, 2)", "Long and Bool do not compare"),
        ("F.pages.between('a', 2)", "between() needs integers"),
        ("F.pages", "is a condition, true when the operation is forbidden"),
        ("F.urgent & F.pages", "F.pages is a Long, not a condition"),
        ("F.nothing == 1", "document_share has no fact nothing"),
        ("F.nothing.is_some()", "document_share has no fact nothing"),
    ],
)
def test_types_are_checked_against_the_fact_schema(expression, reason):
    out = refused(
        forbid_chapter(f"forbid('document_share', {expression}, legislation='x')")
    )
    assert reason in out, out
    assert out.startswith("eu/es/tax.py:3: rule: ")


def test_each_names_a_list_of_the_fact_schema_and_the_operation_has_facts():
    assert (
        'rule: each="rates" names no list of tax_policy_propose: the fact schema has no tax_policy_propose.rates'
        in refused(
            forbid_chapter(
                "forbid('tax_policy_propose', F.code == 'x', each='rates', legislation='x')"
            )
        )
    )
    assert "rule: the fact schema has no facts for tax_assess" in refused(
        forbid_chapter("forbid('tax_assess', True, legislation='x')")
    )
    c = codex(facts=None)
    c.load(
        "eu/es", "tax", forbid_chapter("forbid('tax_assess', True, legislation='x')")
    )  # no schema: not checked at load
    with pytest.raises(
        LegislationError, match="binding forbids needs GGG's fact schema"
    ):
        c.forbids()
    with pytest.raises(LegislationError, match="built from F, S and when"):
        forbid("tax_policy_propose", "<expr>")
    c2 = codex()
    c2.load(
        "eu/es",
        "tax",
        forbid_chapter("forbid('tax_policy_propose', True, legislation='x')"),
    )
    assert c2.cedar().endswith("  when { true };\n") and c2.forbids()[0].evaluate(
        {"policy": "P-1"}
    )


def test_the_fact_schema_is_checked():
    for bad, reason in (
        ({"op": {"facts": {"x": "Float"}}}, "not one of"),
        (
            {"op": {"facts": {"x": {"type": "Long", "min": 5, "max": 1}}}},
            "min 5 is above max 1",
        ),
        ({"op": {"facts": {"x": {"type": "Long", "min": 5}}}}, "both given"),
        (
            {"op": {"facts": {"x": {"type": "String", "min": 1, "max": 2}}}},
            "bound a Long",
        ),
        (
            {"op": {"facts": {"x": "Long"}, "also": ["other"]}},
            "also names 'other', which is not an action",
        ),
        (
            {
                "op": {"facts": {"x": "Long"}, "also": ["other"]},
                "other": {"facts": {"x": "String"}},
            },
            "has the same facts",
        ),
        ({"op.a.b": {"facts": {}}}, "is not an action name"),
        ({"op": {"rules": {}}}, "an action is {facts"),
        ([], "is a dict of actions"),
    ):
        with pytest.raises(LegislationError, match=reason):
            FactSchema(bad)
    schema = FactSchema(FACTS)
    assert schema.actions["tax_policy_propose.taxes"].also == (
        "tax_policy_draft.taxes",
    )
    assert schema.actions["document_share"].ranges() == {
        "pages": (0, 10_000),
        "rate": (0, 100_000_000),
        "surcharge": (0, 100_000_000),
    }
    assert schema.actions["document_share"].facts[
        "retention"
    ].optional and schema.actions["document_share"].facts["rate"].range == (
        0,
        100_000_000,
    )


def test_names_and_strings_are_written_as_cedar_wants_them():
    c = codex()
    c.load("eu/es", "tax", RESERVED_NAME)
    reserved = c.forbids()[0]
    assert reserved.cedar().endswith(
        '  when { context.facts has "in"\n         && context.facts["in"] != 3 };'
    )
    assert (
        '"in"?: Long' in c.cedar_schema()
        and cedarpy.validate_policies(c.cedar(), c.cedar_schema()).validation_passed
    )
    assert (
        reserved.evaluate({"in": 4})
        and not reserved.evaluate({"in": 3})
        and not reserved.evaluate({})
    )
    between = [f for f in with_edges().forbids() if f.id.endswith("unbounded_between")][
        0
    ]
    assert between.cedar().endswith(
        "&& (context.facts.unbounded >= -5 && context.facts.unbounded <= 1300000) };"
    )
    c2 = codex()
    c2.load(
        "eu/es",
        "tax",
        forbid_chapter(
            """forbid('tax_policy_propose', F.code == 'say "no"\\n\\\\', each='taxes', legislation='Art. 1 "bis"')"""
        ),
    )
    text = c2.cedar()
    assert (
        '@legislation("Art. 1 \\"bis\\"")' in text
        and 'context.facts.code == "say \\"no\\"\\n\\\\"' in text
    )
    assert cedarpy.validate_policies(text, c2.cedar_schema()).validation_passed


def test_a_bool_setting_or_fact_is_a_condition_and_lists_fold_in():
    c = with_edges()
    tagged = [f for f in c.forbids() if f.id.endswith("tagged")][0]
    assert tagged.cedar().endswith(
        '  when { context.facts.tags.contains("secret")\n'
        '         && !(context.facts.urgent || ["health","religion"].contains(context.facts.kind)) };'
    )
    assert (
        "context.settings.data_protection.sensitive.contains(context.facts.kind)"
        in tagged.cedar(generic=True)
    )
    assert "sensitive: Set<String>" in c.cedar_schema(
        generic=True
    ) and "minimum_retention: Period" in c.cedar_schema(generic=True)
    assert tagged.evaluate({"tags": ["secret"], "urgent": False, "kind": "memo"})
    assert not tagged.evaluate({"tags": ["secret"], "urgent": False, "kind": "health"})
    assert not tagged.evaluate({"tags": [], "urgent": False, "kind": "memo"})


def test_a_number_setting_inside_arithmetic_is_folded_in_the_realm_and_open_in_the_generic_output():
    """The generic output is analysis input: an open number setting in arithmetic has no range the
    generator could bound it by, so the analysis supplies one as an assumption (R1 §5.2). The installed
    Cedar folds it and is proved never to error."""
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable, pct\nclass Tax(above('tax')):\n"
        "    margin = overridable(pct('0.07'), legislation='x')\n"
        "    rule = forbid('document_share', F.rate.is_some() & (F.rate > S.margin + pct('1.10')), legislation='x')\n",
    )
    rule = c.forbids()[0]
    assert "&& context.facts.rate > 70000 + 1100000 };" in rule.cedar()
    assert (
        "&& context.facts.rate > context.settings.tax.margin + 1100000 };"
        in rule.cedar(generic=True)
    )
    assert cedarpy.validate_policies(
        c.cedar(generic=True), c.cedar_schema(generic=True)
    ).validation_passed


# ── The review of #546: conditions keep their polarity, arithmetic never leaves a check ───────────


@pytest.mark.parametrize(
    "expression, reason",
    [
        # A2, A3: a Period comparison "negated" by == False or != True lost its polarity.
        (
            "F.decision_deadline.is_some() & ((F.decision_deadline > months(6)) == False)",
            "is a condition: conditions combine with &, | and ~",
        ),
        (
            "F.decision_deadline.is_some() & ((F.decision_deadline > months(6)) != True)",
            "is a condition: conditions combine with &, | and ~",
        ),
        (
            "(F.longer_deadline_legislation.is_some() == True)",
            "is a condition: conditions combine",
        ),
        (
            "(F.longer_deadline_legislation.is_none() & True) == False",
            "is a condition: conditions combine",
        ),
        # A4: a Period comparison as a when() branch, under ~.
        (
            "F.decision_deadline.is_some() & ~when(True, F.decision_deadline > months(6), False)",
            "the branches of when() are integers",
        ),
        (
            "when(True, 'a', 'b') == F.longer_deadline_legislation",
            "the branches of when() are integers",
        ),
        (
            "when(True, 1, 0) == True",
            "True and False compare with a Bool fact or setting only",
        ),
        ("(F.decision_deadline > months(6)) + 1 > 1", "is a condition, not a number"),
    ],
)
def test_a_condition_is_never_an_operand_so_it_keeps_its_polarity(expression, reason):
    text = (
        "from realms.legislation import F, S, above, forbid, months, when\nclass Procedure(above('procedure')):\n"
        f"    rule = forbid('procedure_define', {expression}, legislation='x')\n"
    )
    assert reason in refused(text, domain="procedure")


@pytest.mark.parametrize(
    "expression, reason",
    [
        # B1, B2: the same for bounded arithmetic.
        (
            "F.rate.is_some() & F.surcharge.is_some() & ((F.rate + F.surcharge > 5) == False)",
            "is a condition: conditions combine",
        ),
        (
            "F.rate.is_some() & F.surcharge.is_some() & ~when(True, F.rate + F.surcharge > 5, False)",
            "the branches of when() are integers",
        ),
        # C1, C2: arithmetic inside contains() was neither bounded nor checked.
        (
            "~F.tags.contains(F.unbounded + 1)",
            "contains() takes a literal or a setting",
        ),
        ("~F.tags.contains(F.pages + 1)", "contains() takes a literal or a setting"),
        ("F.tags.contains(F.kind)", "contains() takes a literal or a setting"),
        # C3: a between() bound that overflows.
        (
            "F.pages.between(0, when(True, 9223372036854775807) + 1)",
            "may overflow Cedar's 64-bit integer",
        ),
        (
            "F.pages.between(0, 9223372036854775807 + 1)",
            "does not fit Cedar's 64-bit integer",
        ),
        ("when(F.urgent, 1, 'a') == F.pages", "the branches of when() are integers"),
        (
            "when(F.urgent, True, False) == F.urgent",
            "the branches of when() are integers",
        ),
        # D1: a bounded fact established only inside the when() it is read in.
        (
            "F.pages > when(F.rate.is_some(), F.rate, 0) + 1",
            "F.rate is optional: compare it after F.rate.is_some() in the same & chain",
        ),
        ("F.pages > when(F.rate.is_some(), F.rate, 0)", "F.rate is optional"),
        # Bool compares only a fact or a setting with a literal.
        (
            "F.urgent == F.urgent",
            "a Bool fact or setting compares with True or False only",
        ),
        ("F.urgent > True", "a Bool fact or setting compares with True or False only"),
    ],
)
def test_arithmetic_never_leaves_the_checks_and_bounded_facts_are_present(
    expression, reason
):
    assert reason in refused(
        forbid_chapter(f"forbid('document_share', {expression}, legislation='x')")
    )


def test_c4_a_lower_realms_setting_cannot_switch_off_an_upper_forbid_by_overflow():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable\nclass Tax(above('tax')):\n"
        "    cap = overridable(100, legislation='x')\n"
        "    rule = forbid('document_share', F.pages.between(0, S.cap + 1), legislation='x')\n",
    )
    assert "context.facts.pages <= 100 + 1" in c.cedar()
    c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    cap = 9223372036854775807\n",
    )
    with pytest.raises(Refused, match="may overflow Cedar's 64-bit integer"):
        c.cedar()  # the realm's codex is refused at install, so the forbid is never skipped


def test_a_bool_fact_or_setting_compares_with_true_or_false():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable\nclass Tax(above('tax')):\n"
        "    strict = overridable(True, legislation='x')\n"
        "    rule = forbid('document_share', (F.urgent == True) & (S.strict != False) & ~(False == F.urgent), legislation='x')\n",
    )
    rule = c.forbids()[0]
    assert rule.cedar().endswith(
        "  when { context.facts.urgent == true\n         && true != false\n         && !(context.facts.urgent == false) };"
    )
    assert cedarpy.validate_policies(c.cedar(), c.cedar_schema()).validation_passed
    assert rule.evaluate({"urgent": True}) and not rule.evaluate({"urgent": False})
    assert "context.settings.tax.strict != false" in rule.cedar(generic=True)


def test_contains_takes_a_literal_or_a_setting():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable\nclass Tax(above('tax')):\n"
        "    secret = overridable('secret', legislation='x')\n"
        "    rule = forbid('document_share', F.tags.contains(S.secret) | F.tags.contains('top'), legislation='x')\n",
    )
    rule = c.forbids()[0]
    assert (
        'context.facts.tags.contains("secret")\n         || context.facts.tags.contains("top")'
        in rule.cedar()
    )
    assert "context.facts.tags.contains(context.settings.tax.secret)" in rule.cedar(
        generic=True
    )
    assert rule.evaluate({"tags": ["secret"]}) and not rule.evaluate({"tags": ["x"]})


@pytest.mark.parametrize(
    "member, reason",
    [
        (
            "forbid('tax_policy_propose', F.code == 'a' and F.rate.is_some(), each='taxes', legislation='x')",
            "use &, | and ~ in a forbid",
        ),
        (
            "forbid('tax_policy_propose', not F.rate.is_some(), each='taxes', legislation='x')",
            "use &, | and ~ in a forbid",
        ),
        (
            "forbid('tax_policy_propose', 1 < F.rate < 5, each='taxes', legislation='x')",
            "use &, | and ~ in a forbid",
        ),
        (
            "forbid('tax_policy_propose', F.rate is None, each='taxes', legislation='x')",
            "use &, | and ~ in a forbid",
        ),
        (
            "forbid('tax_policy_propose', F.code in ['a'], each='taxes', legislation='x')",
            "use &, | and ~ in a forbid",
        ),
        (
            "forbid('tax_policy_propose', (1 if F.rate.is_some() else 0) > 0, each='taxes', legislation='x')",
            "write when(c, a, b) in a forbid",
        ),
    ],
)
def test_the_syntax_tree_check_tells_authors_what_the_realm_refuses(member, reason):
    """``realms legislation check`` runs the syntax-tree check first: its message is the one an author
    reads. The realm's bytecode check refuses the same chapters."""
    text = f"from realms.legislation import F, above, forbid\nclass Tax(above('tax')):\n    rule = {member}\n"
    with pytest.raises(Refused) as e:
        subset_ast.check(text, "eu/es/tax.py")
    assert reason in str(e.value)
    with pytest.raises(Refused):
        codex().load("eu/es", "tax", text)


# ── The second review of #546: every rule lives in the node's constructor, no node is mutable ─────


@pytest.mark.parametrize(
    "expression, reason",
    [
        # Nodes built through type() (around the methods that build them) refuse the same.
        (
            "~type(F.tags.contains('a'))(F.tags, F.unbounded + 1)",
            "contains() takes a literal or a setting",
        ),
        (
            "~type(F.tags.contains('a'))(F.tags, F.pages + 1)",
            "contains() takes a literal or a setting",
        ),
        (
            "type(F.pages.in_([1]))(F.pages, F.tags)",
            "in_() takes a list of values or a list setting",
        ),
        (
            "type(F.pages.in_([1]))(F.kind, F.pages.in_([1]).values)",
            "F.kind.in_(): Set<String> and Set<Long> do not compare",
        ),
        (
            "F.pages > type((F.pages > 1).right)(2 ** 70)",
            "does not fit Cedar's 64-bit integer",
        ),
        (
            "F.pages > type((F.pages > 1).right)([1, 'a'])",
            "a list holds strings, or integers",
        ),
        (
            "F.pages > type((F.pages > 1).right)(None)",
            "is not a literal a forbid can hold",
        ),
        (
            "type(F.pages.between(0, 1))(F.pages, F.unbounded + 1, (F.pages > 5).right)",
            "between() takes literals and settings as bounds, not facts",
        ),
        (
            "type(F.pages.between(0, 1))(F.pages, F.urgent | F.urgent, (F.pages > 5).right)",
            "between() takes literals and settings as bounds, not facts or conditions",
        ),
        (
            "type(F.pages.between(0, 1))(S.x, (F.pages > 0).right, (F.pages > 5).right)",
            "between() is for a fact",
        ),
        ("type(F.pages.is_some())(S.x)", "is_some() is for a fact"),
        ("type(F.pages > 1)('<>', F.pages, F.pages)", "is not one of the comparisons"),
        (
            "type(F.pages > 1)('>', F.pages, 3)",
            "is not an expression, as an operand must be",
        ),
        ("type(F.pages + 1)(F.pages, '*', F.pages) > 1", "is not + or -"),
        (
            "type(F.urgent & F.urgent)(F.urgent, True)",
            "is not an expression, as an operand of & must be",
        ),
        ("type(~F.urgent)(1)", "is not an expression, as the operand of ~ must be"),
        ("type(F.pages)('_private') > 1", "is not the name of a fact"),
        ("type(S.x)('') > 1", "is not the name of a setting"),
        # when() branches and between() bounds that are not integers are refused at bind.
        ("F.pages > when(True, S.minimum_retention)", "when() needs integers"),
        ("F.pages > when(True, S.strict)", "when() needs integers"),
        ("F.pages > when(F.urgent, F.urgent)", "when() needs integers"),
        (
            "F.pages > when(True, F.rate.is_some())",
            "the branches of when() are integers",
        ),
        ("F.pages.between(True, 5)", "between() needs integers"),
    ],
)
def test_a_node_built_any_other_way_refuses_what_its_method_refuses(expression, reason):
    text = (
        "from realms.legislation import F, S, above, forbid, months, overridable, when\n"
        "class DataProtection(above('data_protection')):\n"
        "    minimum_retention = overridable(months(6), legislation='x')\n"
        "    strict = overridable(True, legislation='x')\n"
        f"    rule = forbid('document_share', {expression}, legislation='x')\n"
    )
    assert reason in refused(text, domain="data_protection")


def test_list_literals_and_folded_list_settings_are_tuples():
    c = with_edges()
    required = [f for f in c.forbids() if f.id.endswith("ibi_rate_required")][0]
    assert required.expression.children[0].values.value == ("ibi_urban", "ibi_rustic")
    assert isinstance(required.expression.children[0].values.value, tuple)
    tagged = [f for f in c.forbids() if f.id.endswith("tagged")][0]
    assert tagged.settings["sensitive"] == ("health", "religion") and isinstance(
        tagged.settings["sensitive"], tuple
    )
    assert '["health","religion"].contains(context.facts.kind)' in tagged.cedar()
    with pytest.raises(LegislationError, match="never changes"):
        required.expression.children[0].values.value = ("ibi_urban",)
    with pytest.raises(AttributeError):
        required.expression.children[0].values.value.remove("ibi_rustic")


def test_a_fact_at_the_whole_64_bit_range_in_arithmetic():
    """A fact whose range is all of i64 can be added to 0 (the sum fits), not subtracted from 0."""
    facts = dict(FACTS)
    facts["document_share"] = {
        "facts": dict(
            FACTS["document_share"]["facts"],
            wide={"type": "Long", "min": I64_MIN, "max": I64_MAX},
        )
    }
    c = Codex(LAYERS, realm=VALENCIA, operations=OPERATIONS, facts=facts)
    c.load(
        "eu/es",
        "tax",
        forbid_chapter("forbid('document_share', F.wide + 0 > 1, legislation='x')"),
    )
    assert (
        f"when {{ context.facts.wide < {I64_MIN} || context.facts.wide > {I64_MAX} || context.facts.wide + 0 > 1 }};"
        in c.cedar()
    )
    assert "may overflow" in refused(
        forbid_chapter("forbid('document_share', 0 - F.wide > 1, legislation='x')"),
        c=Codex(LAYERS, realm=VALENCIA, operations=OPERATIONS, facts=facts),
    )
    assert cedarpy.validate_policies(c.cedar(), c.cedar_schema()).validation_passed


def test_negative_literal_terms_and_the_64_bit_minimum_validate():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        forbid_chapter(
            f"forbid('document_share', (F.pages + -5 - -3 > 0) & (F.unbounded > {I64_MIN}), legislation='x')"
        ),
    )
    text = c.cedar()
    assert (
        "context.facts.pages + -5 - -3 > 0" in text
        and f"context.facts.unbounded > {I64_MIN}" in text
    )
    assert cedarpy.validate_policies(text, c.cedar_schema()).validation_passed
    rule = c.forbids()[0]
    assert rule.evaluate({"pages": 3, "unbounded": 0}) and not rule.evaluate(
        {"pages": 1, "unbounded": 0}
    )
    assert not rule.evaluate({"pages": 3, "unbounded": I64_MIN})


# ── The third review of #546: exact types at every door, no str() of an untrusted object ──────────


class _Int(int):
    def __str__(self):
        return "1 && false"

    __repr__ = __str__


class _Str(str):
    def __iter__(self):
        return iter("nothing")

    def __str__(self):
        return "nothing"


class _List(list):
    pass


def test_type_in_a_chapter_takes_one_argument():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, above, forbid\nclass Tax(above('tax')):\n"
        "    rule = forbid('tax_policy_propose', type(F.code)('code') == 'a', each='taxes', legislation='x')\n",
    )
    assert "no nested classes" in refused(
        "from realms.legislation import above\nclass Tax(above('tax')):\n    kind = type(1)\n"
    )
    assert "type(name, bases, dict) is not allowed in a chapter" in refused(
        "from realms.legislation import above\nclass Tax(above('tax')):\n    x = type('E', (int,), {})(1)\n"
    )


def test_the_sdk_and_the_generator_take_the_exact_types_only():
    from realms.legislation import Period, Ref, pct
    from realms.legislation.cedar import Writer, quoted, value_type
    from realms.legislation.chapter import is_data
    from realms.legislation.expressions import Literal
    from realms.legislation.sealed import freeze

    for bad in (
        _Int(5),
        _Str("x"),
        _List([1]),
        [_Int(5)],
        (_Str("x"),),
        {"a": _Int(1)},
        {_Str("k"): 1},
    ):
        assert not is_data(bad), bad
    assert is_data({"a": [1, "b", True, None, months(1), Ref("a", "b")]})
    assert (
        type(freeze(_List([1]))) is _List
    )  # not data: left alone, and is_data refuses it
    for bad, reason in (
        (lambda: Literal(_Int(5)), "its type is _Int"),
        (lambda: Literal(_Str("x")), "its type is _Str"),
        (lambda: Literal((_Str("x"),)), "a list holds strings"),
        (lambda: value_type(_Int(5), "S.cap"), "its type is _Int"),
        (lambda: value_type([_Str("a")], "S.codes"), "its type is _Str"),
        (lambda: quoted(_Str("x")), "its type is _Str"),
        (lambda: Writer.literal(_Int(5)), "its type is _Int"),
        (lambda: Writer.literal(_Str("x")), "its type is _Str"),
        (lambda: Period(_Int(6), "Month"), "a Period's count is an integer"),
        (lambda: Period(6, _Str("Month")), "is not a GGG Period unit"),
        (lambda: Ref(_Str("a"), "b"), "takes two strings"),
        (lambda: pct(_Str("1.10")), "write a rate as a string"),
        (
            lambda: forbid(_Str("tax_policy_propose"), True),
            "a forbid names a GGG operation",
        ),
        (
            lambda: forbid("tax_policy_propose", True, legislation=_Str("x")),
            'legislation="..." is a string',
        ),
        (
            lambda: forbid("tax_policy_propose", True, each=_Str("taxes")),
            "each= names the list",
        ),
    ):
        with pytest.raises(LegislationError, match=reason):
            bad()
    assert (
        Writer.literal(5) == "5"
        and Writer.literal(True) == "true"
        and Writer.literal(("a", "b")) == '["a","b"]'
    )


def test_an_injected_setting_is_refused_when_the_forbid_is_bound():
    """Had a subclass got past the loader, binding refuses it: nothing is ever written with str()."""
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import F, S, above, forbid, overridable, pct\nclass Tax(above('tax')):\n"
        "    cap = overridable(pct('1.30'), legislation='x')\n    name = overridable('ibi_urban', legislation='x')\n"
        "    rule = forbid('tax_policy_propose', (F.code == S.name) & F.rate.is_some() & (F.rate > S.cap), each='taxes', legislation='x')\n",
    )
    assert "context.facts.rate > 1300000 };" in c.cedar()
    cls = c.resolved("tax")
    type.__setattr__(
        cls, "cap", _Int(1300000)
    )  # the test's own bypass of the sealed class
    with pytest.raises(
        Refused,
        match="S.cap = 1 && false: not a value a forbid can read, its type is _Int",
    ):
        c.cedar()
    type.__setattr__(cls, "cap", 1300000)
    type.__setattr__(cls, "name", _Str("ibi_urban"))
    with pytest.raises(Refused, match="its type is _Str"):
        c.cedar()


def test_two_forbids_never_share_an_id():
    from realms.legislation.cedar import policies

    c = valencia()
    bound = c.forbids()
    assert policies(bound) == c.cedar()
    with pytest.raises(
        LegislationError, match="two forbids share the id eu/es/tax/ibi_urban_cap"
    ):
        policies(bound + [bound[3]])
