"""The canister's copy of ``realms.legislation`` on PocketIC: a layered-codex chapter loads inside the realm
wasm, through the module the canister bundles, which proves the library needs nothing the canister lacks
(no CLI dependency, no stubbed stdlib). The readable-subset check and the marks run where they will run,
and the instruction cost of the first use is measured. See docs/codices/layered-codex.md §12 and realms-gos#483.
"""

import json

from ic.candid import Types, encode
from ic.principal import Principal
from pic import CONTROLLER

CODE = '''
import json
before = ic.performance_counter(0)
from realms.legislation import Codex, Event, Money, Refused, pct, plain
imported = ic.performance_counter(0)

ES = """
from realms.legislation import Ref, above, extension, fixed, months, overridable, pct, working_days

class Procedure(above("procedure")):
    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")
    comment_period = overridable(working_days(30), legislation="Ley 7/1985 Art. 49", check=lambda v: v >= working_days(30))
    bonus = fixed({"general": pct("60")}, legislation="TRLRHL Art. 74")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_codex_install(self, event):
        event.order = ["es"]
        self.ggg.notification_send(recipient=Ref("external", "AGE"), topic="ordinance", title="Comunicació", body=event.result["codex"])
"""
TOWN = """
from realms.legislation import above, days, working_days

class Procedure(above("procedure")):
    default_decision_deadline = days(60)
    comment_period = working_days(45)

    def on_codex_install(self, event):
        missing = [name for name in ("ca-valencia", "es") if name not in event.args.get("languages", [])]
        title = f"Nova ordenança{' (traducció pendent)' if missing else ''}"
        self.ggg.notification_send(recipient="members", topic="ordinance", title=title, body=",".join(event.order))
"""
REFUSED = """
from realms.legislation import above
class Procedure(above("procedure")):
    def working_days_after(self, day, n):
        pass
"""
codex = Codex(["eu/es", "x/town"], realm="x/town", operations=["codex_install", "notification_send"])
codex.load("eu/es", "procedure", ES)
codex.load("x/town", "procedure", TOWN)
loaded = ic.performance_counter(0)
codex.calendar_layer("eu/es", {"weekend": ["sat", "sun"], "years": {"2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]}})
codex.check()
context = codex.context(now=7)
chapter = codex.instance("procedure", context)
chapter.on_codex_install(Event("alice", {"document": "D-1", "languages": ["es"]}, {"codex": "C-1"}, 7))
other = Codex(["eu/es", "x/town"], realm="x/town")
other.load("eu/es", "procedure", ES)
try:
    other.load("x/town", "procedure", REFUSED)
    refusal = None
except Refused as e:
    refusal = str(e)
# Cent rounding, in the canister, with no float: 1.00 token at 1.5 % rounds to one cent (half away).
money = Money(10 ** 8, 8).times(pct("1.5")).to_json()
print(json.dumps({"deadline": chapter.default_decision_deadline.to_json(), "bonus": plain(chapter.bonus),
                  "day": chapter.working_days_after("2026-12-31", 2), "effects": context.effects.queued,
                  "money": money, "refusal": refusal,
                  "cost": {"import": imported - before, "load": loaded - imported}}))
'''


def test_a_chapter_loads_through_the_canisters_copy_of_the_library(realm):
    realm.pic.set_sender(Principal.from_str(CONTROLLER))
    out = realm.raw(
        "__shell__", encode([{"type": Types.Text, "value": CODE}]), query=False
    )[0]["value"]
    assert out.startswith("{"), out  # a traceback otherwise
    got = json.loads(out)
    assert got["deadline"] == {"count": 60, "unit": "Day"}
    assert got["bonus"] == {"general": 60_000_000}
    assert got["day"] == "2027-01-05"
    assert got["effects"] == [
        [
            "notification_send",
            {
                "recipient": {"kind": "external", "id": "AGE"},
                "topic": "ordinance",
                "title": "Comunicació",
                "body": "C-1",
            },
        ],
        [
            "notification_send",
            {
                "recipient": "members",
                "topic": "ordinance",
                "title": "Nova ordenança (traducció pendent)",
                "body": "es",
            },
        ],
    ]
    assert (
        got["refusal"]
        == "x/town/procedure.py:4: working_days_after is fixed by eu/es/procedure.py (Ley 39/2015 Art. 30)"
    )
    assert got["money"] == 2 * 10**6  # 0.015 -> 0.02, rounded to the cent in the wasm
    cost = got["cost"]
    print(
        f"first use: import {cost['import']:,} instructions, two chapters {cost['load']:,}"
    )
    # The library's import closure compiles once per upgrade: measured at 371M instructions with the
    # F expressions (S2) and 427M with the resolver (S3) too, on basilisk 0.16.
    assert cost["import"] + cost["load"] < 600_000_000, cost


RESOLVER_CODE = '''
import json
before = ic.performance_counter(0)
from realms.legislation import Codex, LegislationError, plan, resolve
imported = ic.performance_counter(0)

SPAIN_PROCEDURE = """
from realms.legislation import above, fixed, months, overridable, working_days

class Procedure(above("procedure")):
    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))
"""
SPAIN_LOCAL_REGIME = """
from realms.legislation import above, fixed

class LocalRegime(above("local_regime")):
    uses = ["procedure"]

    @fixed(legislation="Ley 7/1985 Art. 65.2")
    def earliest(self, day):
        return self.legislation.procedure.working_days_after(day, 15)
"""
TOWN_PROCEDURE = """
from realms.legislation import above, months

class Procedure(above("procedure")):
    default_decision_deadline = months(2)
"""
chapters = {"sha256:p": SPAIN_PROCEDURE, "sha256:l": SPAIN_LOCAL_REGIME, "sha256:t": TOWN_PROCEDURE}
calendars = {"sha256:c": {"weekend": ["sat", "sun"], "years": {"2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]}}}
spain = {"source": "eu/es", "version": "2.4.0", "chapters": {"procedure": "sha256:p", "local_regime": "sha256:l"}, "calendar": "sha256:c"}
cv = {"source": "eu/es/vc", "version": "1.0.0", "chapters": {}, "calendar": None}
relays = [{"source": "eu/es", "layers": [spain]}, {"source": "eu/es/vc", "layers": [spain, cv]}]
p = plan("x/town", relays, {"chapters": {"procedure": "sha256:t"}})
planned = ic.performance_counter(0)
r = resolve(p, chapters, calendars)
resolved = ic.performance_counter(0)
view = r.view("procedure")


def steps(n, cycle=False):
    texts = {}
    for i in range(n):
        nxt = (i + 1) % n
        ends = i == n - 1 and not cycle
        body = "return 0" if ends else f"return 1 + self.legislation.step{nxt}.call()"
        texts[f"step{i}"] = (
            "from realms.legislation import above\\n"
            f"class Step{i}(above('step{i}')):\\n    uses = {[] if ends else [f'step{nxt}']!r}\\n"
            f"    def call(self):\\n        {body}\\n"
        )
    return texts


def refusal(texts):
    named = {d: "sha256:" + d for d in texts}
    try:
        resolve(plan("x/town", [], {"chapters": named}), {named[d]: t for d, t in texts.items()})
    except LegislationError as e:
        return str(e)


deep = Codex(["x/town"], realm="x/town")
for domain, text in steps(10).items():
    deep.load("x/town", domain, text)
try:
    deep.instance("step0", deep.context()).call()
    stopped = None
except LegislationError as e:
    stopped = str(e)
print(json.dumps({
    "paths": p.paths, "layers": p.layers,
    "deadline": r.codex.instance("procedure", r.codex.context()).default_decision_deadline.to_json(),
    "earliest": r.codex.instance("local_regime", r.codex.context()).earliest("2026-12-31"),
    "view": [(m["name"], [l["source"] for l in m["layers"]]) for m in view["members"]],
    "cycle": refusal(steps(2, cycle=True)), "too_deep": refusal(steps(10)), "stopped": stopped,
    "cost": {"import": imported - before, "plan": planned - imported, "resolve": resolved - planned},
}))
'''


def test_the_resolver_runs_in_the_canister(realm):
    realm.pic.set_sender(Principal.from_str(CONTROLLER))
    out = realm.raw(
        "__shell__", encode([{"type": Types.Text, "value": RESOLVER_CODE}]), query=False
    )[0]["value"]
    assert out.startswith("{"), out  # a traceback otherwise
    got = json.loads(out)
    assert got["paths"] == {
        "local_regime": ["eu/es"],
        "procedure": ["eu/es", "x/town"],
    }
    assert got["layers"] == ["eu/es", "x/town"]  # eu/es/vc writes nothing
    assert got["deadline"] == {"count": 2, "unit": "Month"}
    assert (
        got["earliest"] == "2027-01-22"
    )  # 15 working days, counted by Spain's chapter
    assert got["view"] == [
        ["default_decision_deadline", ["eu/es", "x/town"]],
        ["working_days_after", ["eu/es"]],
    ]
    assert got["cycle"] == (
        "uses cycle: step0 → step1 → step0 "
        "(x/town/step0.py uses step1; x/town/step1.py uses step0)"
    )
    assert got["too_deep"].startswith("uses chain step0 → step1 → ")
    assert "is 9 calls deep: calls between domains stop at depth 8" in got["too_deep"]
    assert got["stopped"] == "calls between domains stop at depth 8"
    cost = got["cost"]
    print(
        f"first use: import {cost['import']:,} instructions, plan {cost['plan']:,}, resolve {cost['resolve']:,}"
    )
    assert sum(cost.values()) < 400_000_000, cost


# ── S2 (realms-gos#484): the Cedar a forbid becomes, generated in the realm ─────────────────────────

RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
TAX_ENTRY = {"code": "String", "rate": RATE}
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
}
ES_TAX = """# legislation/eu/es/tax.py (layered-codex.md §5.6)
from realms.legislation import F, S, above, forbid, overridable, pct, when

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
"""
VALENCIA_TAX = """from realms.legislation import above

class Tax(above("tax")):
    capital = True
    surface_public_transport = True
"""
ES_PROCEDURE = """# legislation/eu/es/procedure.py (layered-codex.md §4)
from realms.legislation import F, above, forbid, months, overridable

class Procedure(above("procedure")):
    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

    over_six_months = forbid("procedure_define",
        F.decision_deadline.is_some() & (F.decision_deadline > months(6))
        & F.longer_deadline_legislation.is_none(),
        legislation="Ley 39/2015 Art. 21.2")
"""
INJECTION = (
    "from realms.legislation import above, months\nU = '_'\nclass Procedure(above('procedure')):\n"
    "    default_decision_deadline = months(type('E', (int,), {U + U + 'str' + U + U: lambda self: '6 && false'})(6))\n"
)
CEDAR_CODE = """
import json
from realms.legislation import Codex
from ic_basilisk_toolkit import cedar
from core import auth

codex = Codex(["eu/es", "eu/es/vc/46250-valencia"], realm="eu/es/vc/46250-valencia", facts=%(facts)r)
codex.load("eu/es", "tax", %(es_tax)r)
codex.load("eu/es/vc/46250-valencia", "tax", %(valencia_tax)r)
codex.load("eu/es", "procedure", %(es_procedure)r)
before = ic.performance_counter(0)
policies = codex.cedar()
schema = codex.cedar_schema(context=auth.CONTEXT)
generated = ic.performance_counter(0)
entities = [{"uid": {"type": "Realm::User", "id": "u"}, "attrs": {"permissions": ["all"], "banned": False}, "parents": []},
            {"uid": {"type": "Realm::Realm", "id": "1"}, "attrs": {}, "parents": []}]
blank = {k: "" if t == "String" else False if t == "Bool" else [] for k, t in auth.CONTEXT.items()}

def decide(action, facts):
    context = dict(blank, op=action, facts=facts)
    return cedar.is_authorized('Realm::User::"u"', 'Realm::Action::"%%s"' %% action, 'Realm::Realm::"1"', entities, context)

try:  # the review of #546: a setting built from an int subclass with its own __str__
    codex.load("eu/es/vc/46250-valencia", "procedure", %(injection)r)
    injection = "LOADED"
except Exception as e:
    injection = f"{type(e).__name__}: {e}"
decisions, error = {}, None
try:
    # Strict validation by the realm's own Cedar engine, then a few decisions. The permit stands in for
    # the realm's permits: the forbids alone decide.
    cedar.load(schema, "permit (principal, action, resource);\\n" + policies)
    loaded = ic.performance_counter(0)
    decisions = {
        "urban at the cap": decide("tax_policy_propose.taxes", {"code": "ibi_urban", "rate": 1240000}),
        "urban over the cap": decide("tax_policy_propose.taxes", {"code": "ibi_urban", "rate": 1240001}),
        "draft over the cap": decide("tax_policy_draft.taxes", {"code": "ibi_urban", "rate": 1240001}),
        "urban without a rate": decide("tax_policy_propose.taxes", {"code": "ibi_urban"}),
        "three months": decide("procedure_define", {"decision_deadline": {"count": 3, "unit": "Month"}}),
        "sixty days": decide("procedure_define", {"decision_deadline": {"count": 60, "unit": "Day"}}),
    }
    decided = ic.performance_counter(0)
except Exception as e:
    error = f"{type(e).__name__}: {e}"
finally:
    cedar.load(auth.SCHEMA, auth.GUARDRAILS + auth.POLICIES)  # the realm's own policy set again
print(json.dumps({"cedar": policies, "schema": schema, "decisions": decisions, "error": error, "injection": injection,
                  "cost": {"generate": generated - before, "load": loaded - generated, "decide": decided - loaded} if error is None else None}))
""" % {
    "facts": FACTS,
    "es_tax": ES_TAX,
    "valencia_tax": VALENCIA_TAX,
    "es_procedure": ES_PROCEDURE,
    "injection": INJECTION,
}


def test_the_realm_generates_the_same_cedar_as_a_laptop_and_its_engine_validates_it(
    realm,
):
    """The canonical Cedar (§6 rule 5) is byte-identical in the realm and on the laptop; the realm's
    Cedar engine (cedar-policy 4.12) validates it strictly against the generated schema and decides
    with it: the per-list actions at propose and at draft, and a Period in another unit (§5.8, §9.4).
    """
    import os
    import sys

    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "realm_backend")
    )
    from realms.legislation import Codex

    laptop = Codex(
        ["eu/es", "eu/es/vc/46250-valencia"],
        realm="eu/es/vc/46250-valencia",
        facts=FACTS,
    )
    laptop.load("eu/es", "tax", ES_TAX)
    laptop.load("eu/es/vc/46250-valencia", "tax", VALENCIA_TAX)
    laptop.load("eu/es", "procedure", ES_PROCEDURE)

    out = realm.shell(CEDAR_CODE)
    assert out.startswith("{"), out  # a traceback otherwise
    got = json.loads(out)
    assert got["error"] is None, got["error"]
    assert (
        "type(name, bases, dict) is not allowed in a chapter" in got["injection"]
    ), got["injection"]
    assert got["cedar"] == laptop.cedar()
    assert (
        '@id("eu/es/tax/ibi_urban_cap")' in got["cedar"]
        and "+ (if true then 70000 else 0)" in got["cedar"]
    )
    assert (
        'action "tax_policy_draft.taxes" in ["call"]' in got["schema"]
        and "covered: Bool" in got["schema"]
    )
    assert got["decisions"] == {
        "urban at the cap": True,
        "urban over the cap": False,
        "draft over the cap": False,
        "urban without a rate": False,
        "three months": True,
        "sixty days": False,
    }
    cost = got["cost"]
    print(
        f"cedar: generate {cost['generate']:,} instructions, load {cost['load']:,}, six decisions {cost['decide']:,}"
    )
    assert cost["generate"] < 100_000_000, cost
