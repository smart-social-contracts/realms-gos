"""The ``F`` expression language of forbids and the Cedar it becomes (docs/codices/layered-codex.md §5.6,
§5.8, §6, §9.4, §12.3; realms-gos#484): the forbids of §5.6 and §4 produce the Cedar of §5.8, Python and
Cedar agree on generated facts (the literals ±1, 0 and the 64-bit extremes among them), a Period
comparison is true for another unit, alone and under ``~`` with other facts, and every forbid never errors.

Cedar runs here through ``cedarpy`` (Cedar 4.12, the canister's line) for validation and decisions, and
through the pinned Cedar CLI 4.14.1 with cvc5 1.3.1 (``scripts/cedar_tools.sh``) for the ``never-errors``
proof, when ``CEDAR_CLI`` and ``CVC5`` point at them.
"""

import os
import random
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
from realms.legislation.cedar import FactSchema
from realms.legislation.expressions import I64_MAX, I64_MIN
from realms.legislation.periods import UNITS

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

ENTITIES = [
    {
        "uid": {"type": "Realm::User", "id": "u"},
        "attrs": {"permissions": ["all"], "banned": False},
        "parents": [],
    },
    {"uid": {"type": "Realm::Realm", "id": "1"}, "attrs": {}, "parents": []},
]
PERMIT_ALL = "permit (principal, action, resource);\n"


def plain(value):
    """A fact or setting value as the Cedar context carries it."""
    if hasattr(value, "to_json"):
        return value.to_json()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def literals(node, found):
    """Every int and str literal in a tree, and in the folded settings."""
    from realms.legislation import expressions as e
    from realms.legislation.cedar import children

    if isinstance(node, e.Literal):
        found.extend(
            v
            for v in (node.value if isinstance(node.value, list) else [node.value])
            if isinstance(v, (int, str)) and not isinstance(v, bool)
        )
    for child in children(node):
        literals(child, found)
    return found


def endpoints(forbid, found):
    """The folded value of every comparison side and between() bound that reads no fact (1240000 for
    València's urban cap), and the count of every Period a rule names or a setting holds.
    """
    from realms.legislation import expressions as e
    from realms.legislation.cedar import children

    def reads_a_fact(node):
        return isinstance(node, e.Fact) or any(reads_a_fact(c) for c in children(node))

    def value(node):
        got = e._eval(node, e.Env({}, dict(forbid.settings), None), False)
        period = e.as_period(got)
        if period is not None:
            found.append(period[0])
        elif isinstance(got, int) and not isinstance(got, bool):
            found.append(got)

    def walk(node):
        if isinstance(node, e.Compare):
            for side in (node.left, node.right):
                if not reads_a_fact(side):
                    value(side)
        if isinstance(node, e.Between):
            value(node.low)
            value(node.high)
        for child in children(node):
            walk(child)

    walk(forbid.expression)
    return found


class Draw:
    """Generated values: around every literal, every Period count and every folded comparison endpoint
    (±1), 0, the 64-bit extremes, the range's edges and beyond, and random values inside and outside
    the range."""

    def __init__(self, seed, forbids):
        self.random = random.Random(seed)
        pool = []
        for f in forbids:
            literals(f.expression, pool)
            endpoints(f, pool)
            pool.extend(
                v
                for v in f.settings.values()
                if isinstance(v, int) and not isinstance(v, bool)
            )
        self.ints = sorted(
            {v for v in pool if isinstance(v, int)} | {0, I64_MIN, I64_MAX}
        )
        self.ints += [
            v + d for v in self.ints for d in (-1, 1) if I64_MIN <= v + d <= I64_MAX
        ]
        self.strings = sorted(
            {v for v in pool if isinstance(v, str)} | {"", "x", "ibi_urbano"}
        )

    def long(self, bounds):
        choice = self.random.random()
        if bounds and choice < 0.35:
            low, high = bounds
            return self.random.choice(
                [low, high, low - 1, high + 1, self.random.randint(low, high)]
            )
        if choice < 0.75:
            return self.random.choice(self.ints)
        return self.random.randint(I64_MIN, I64_MAX)

    def value(self, kind, bounds=None):
        if kind == "Long":
            return self.long(bounds)
        if kind == "String":
            return self.random.choice(self.strings)
        if kind == "Bool":
            return self.random.random() < 0.5
        if kind == "Period":
            return {
                "count": self.long(None),
                "unit": self.random.choice(UNITS + ("Week", "")),
            }
        if kind == "Set<String>":
            return self.random.sample(
                self.strings, self.random.randint(0, len(self.strings))
            )
        raise AssertionError(kind)

    def facts(self, action):
        out = {}
        for name, fact in action.facts.items():
            if fact.optional and self.random.random() < 0.25:
                continue
            out[name] = self.value(fact.type, fact.range)
        return out

    def settings(self, bound):
        return {name: self.value_like(value) for name, value in bound.settings.items()}

    def value_like(self, value):
        if isinstance(value, bool):
            return self.value("Bool")
        if isinstance(value, int):
            return self.long(None)
        if isinstance(value, str):
            return self.value("String")
        if hasattr(value, "to_json"):
            return self.value("Period")
        return self.random.sample(
            self.strings, self.random.randint(1, len(self.strings))
        )


def agree(c, generic, per_action, seed=2026):
    """Python and Cedar on generated facts: the forbids that fire must be the same set, and Cedar must
    report no evaluation error. Returns how many requests were checked."""
    forbids = c.forbids()
    schema = c.cedar_schema(generic, context={"op": "String"})
    policies = PERMIT_ALL + c.cedar(generic)
    draw, checked = Draw(seed, forbids), 0
    for name, action in c.facts.actions.items():
        on_action = [f for f in forbids if name in f.actions]
        if not on_action:
            continue
        requests, expected = [], []
        for _ in range(per_action):
            facts = draw.facts(action)
            context = {"op": name, "facts": facts}
            settings = (
                {}
            )  # one open value per domain and setting, shared by every forbid that reads it
            if generic:
                for f in on_action:
                    settings.setdefault(f.domain, {}).update(draw.settings(f))
                settings = {d: s for d, s in settings.items() if s}
            if (
                settings
            ):  # the schema has a settings record only for an action whose forbids read some
                context["settings"] = {
                    d: {k: plain(v) for k, v in s.items()} for d, s in settings.items()
                }
            requests.append(
                {
                    "principal": 'Realm::User::"u"',
                    "action": f'Realm::Action::"{name}"',
                    "resource": 'Realm::Realm::"1"',
                    "context": context,
                }
            )
            expected.append(
                {
                    f.id
                    for f in on_action
                    if f.evaluate(
                        facts, settings.get(f.domain, {}) if generic else None
                    )
                }
            )
        results = cedarpy.is_authorized_batch(requests, policies, ENTITIES, schema)
        for request, result, fired in zip(requests, results, expected):
            assert not result.diagnostics.errors, (
                request["context"],
                result.diagnostics.errors,
            )
            by_cedar = (
                set(result.diagnostics.id_annotations_by_reason.values())
                if result.decision == cedarpy.Decision.Deny
                else set()
            )
            assert by_cedar == fired, (request["context"], by_cedar, fired)
        checked += len(requests)
    return checked


def test_python_and_cedar_agree_on_10000_generated_fact_sets():
    assert agree(with_edges(), generic=False, per_action=2500) == 10_000


def test_python_and_cedar_agree_with_the_settings_left_open():
    assert agree(with_edges(), generic=True, per_action=1000) == 4_000


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
def cedar_cli():
    cli, cvc5 = os.environ.get("CEDAR_CLI"), os.environ.get("CVC5")
    if not (cli and cvc5 and os.path.exists(cli) and os.path.exists(cvc5)):
        pytest.skip(
            "set CEDAR_CLI and CVC5 to the pinned Cedar CLI 4.14.1 and cvc5 1.3.1 (scripts/cedar_tools.sh)"
        )
    assert (
        "4.14.1"
        in subprocess.run([cli, "--version"], capture_output=True, text=True).stdout
    )
    return cli


def test_every_generated_forbid_never_errors(cedar_cli, tmp_path):
    """SymCC's proof (R1, #481) on the installed Cedar of every forbid of the design's chapters and the
    edge cases, for every action each one names."""
    c = with_edges()
    schema = tmp_path / "realm.cedarschema"
    schema.write_text(c.cedar_schema(context={"op": "String"}))
    proved = 0
    for bound in c.forbids():
        policy = tmp_path / (bound.id.replace("/", "__") + ".cedar")
        policy.write_text(bound.cedar() + "\n")
        for action in bound.actions:
            out = subprocess.run(
                [
                    cedar_cli,
                    "symcc",
                    "--principal-type",
                    "Realm::User",
                    "--action",
                    f'Realm::Action::"{action}"',
                    "--resource-type",
                    "Realm::Realm",
                    "--schema",
                    str(schema),
                    "never-errors",
                    "--policies",
                    str(policy),
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "CVC5": os.environ["CVC5"]},
            )
            assert out.returncode == 0 and "VERIFIED" in out.stdout, (
                bound.id,
                action,
                out.stdout,
                out.stderr,
            )
            proved += 1
    assert proved == 13 + 4  # 4 forbids on two actions each, the others on one


def test_the_unbounded_sum_would_error_and_the_bounded_one_does_not(
    cedar_cli, tmp_path
):
    """The R1 finding, reproduced on the generator's output: the bounded form of F.rate + F.surcharge
    passes never-errors; the same comparison written by hand without the bounds does not.
    """
    c = with_edges()
    added = [f for f in c.forbids() if f.id.endswith("two_facts_added")][0]
    schema = tmp_path / "s.cedarschema"
    schema.write_text(c.cedar_schema(context={"op": "String"}))
    unbounded = added.cedar().replace(
        "(context.facts.rate < 0 || context.facts.rate > 100000000 || context.facts.surcharge < 0 || context.facts.surcharge > 100000000 || ",
        "(",
    )
    assert unbounded != added.cedar()
    results = {}
    for name, text in (("bounded", added.cedar()), ("unbounded", unbounded)):
        policy = tmp_path / f"{name}.cedar"
        policy.write_text(text + "\n")
        out = subprocess.run(
            [
                cedar_cli,
                "symcc",
                "--principal-type",
                "Realm::User",
                "--action",
                'Realm::Action::"document_share"',
                "--resource-type",
                "Realm::Realm",
                "--schema",
                str(schema),
                "never-errors",
                "--policies",
                str(policy),
            ],
            capture_output=True,
            text=True,
        )
        results[name] = out.stdout
    assert (
        "VERIFIED" in results["bounded"] and "DOES NOT HOLD" in results["unbounded"]
    ), results


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
    assert repr(F.x.in_(["a", "b"])) == "F.x.in_(['a', 'b'])"
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
