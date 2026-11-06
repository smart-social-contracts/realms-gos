"""``realms.legislation``, the layered codex library: every rule of docs/codices/layered-codex.md §4,
§4.1 (each line of the marks table, including the §5.5 refusal), §4.2, §4.3, §4.4 and §4.5, with no
canister. Chapters are loaded as the realm and the tools load them: from their source text.
"""

import os
import subprocess
import sys

import pytest

from realms.legislation import (
    Calendar,
    Chapter,
    Codex,
    Event,
    LegislationError,
    Money,
    Period,
    Ref,
    Refused,
    UnitMismatch,
    above,
    days,
    extension,
    fixed,
    forbid,
    months,
    overridable,
    pct,
    times,
    working_days,
    years,
)

LAYERS = ("eu", "eu/es", "eu/es/vc", "eu/es/vc/46250-valencia")
VALENCIA = "eu/es/vc/46250-valencia"
OPERATIONS = (
    "codex_install",
    "document_share",
    "tax_assess",
    "tax_policy_propose",
    "user_add",
    "user_remove",
    "procedure_define",
    "proposal_create",
    "notification_send",
    "invoice_create",
    "zone_set",
    "codex_activate",
    "realm_join",
)


def codex(realm=VALENCIA, operations=OPERATIONS, layers=LAYERS):
    return Codex(layers, realm=realm, operations=operations)


def refused(c, source, domain, text):
    with pytest.raises(Refused) as e:
        c.load(source, domain, text)
    return str(e.value)


# ── Periods (§4.3, GGG's units) ───────────────────────────────────────────────


def test_the_four_unit_constructors_are_ggg_periods():
    assert [p.to_json() for p in (days(60), working_days(30), months(3), years(1))] == [
        {"count": 60, "unit": "Day"},
        {"count": 30, "unit": "WorkingDay"},
        {"count": 3, "unit": "Month"},
        {"count": 1, "unit": "Year"},
    ]
    assert repr(working_days(30)) == "working_days(30)"


def test_periods_compare_within_one_unit():
    assert (
        days(45) > days(30)
        and days(30) <= days(30)
        and days(30) == days(30)
        and days(1) != days(2)
    )
    assert working_days(30) >= working_days(30) and not (months(2) < months(1))
    assert days(3) != None  # noqa: E711 - a Period is not another kind of value
    assert len({days(3), days(3)}) == 1


@pytest.mark.parametrize(
    "compare",
    [lambda a, b: a >= b, lambda a, b: a < b, lambda a, b: a == b, lambda a, b: a != b],
)
def test_a_different_unit_fails_closed(compare):
    with pytest.raises(UnitMismatch):
        compare(days(45), working_days(30))


def test_a_period_counts_integers():
    with pytest.raises(LegislationError):
        days(1.5)
    with pytest.raises(LegislationError):
        days(True)
    with pytest.raises(LegislationError):
        Period(1, "Week")


# ── Rates and money (§4.3, §4.5) ─────────────────────────────────────────────


def test_pct_is_the_integer_in_hundred_millionths():
    assert (
        pct("1.10") == 1_100_000 and pct("0.1") == 100_000 and pct("60") == 60_000_000
    )
    assert (
        pct("100") == 100_000_000
        and pct("0.123456") == 123_456
        and pct("0.07") == 70_000
    )
    assert pct("-0.5") == -500_000 and pct(2) == 2_000_000 and pct(".5") == 500_000


@pytest.mark.parametrize(
    "bad", [1.10, True, "", ".", "1.1.1", "abc", "0.1234567", None]
)
def test_pct_refuses_floats_and_what_does_not_fit_the_scale(bad):
    with pytest.raises(LegislationError):
        pct(bad)


def test_money_times_a_rate_rounds_to_the_nearest_cent_half_away_from_zero():
    assert times(100_000, pct("1.10")) == 1_100  # 1000.00 at 1.10 % is 11.00
    assert times(2_345, pct("10")) == 235  # 23.45 at 10 %: 2.345 becomes 2.35
    assert times(-2_345, pct("10")) == -235  # -2.345 becomes -2.35
    assert times(1_234, pct("10")) == 123  # 1.234 becomes 1.23
    assert Money(2_345).times(pct("10")) == Money(235)
    gross = Money(100_000).times(pct("1.10"))
    net = gross - gross.times(pct("60"))
    assert net < gross and (net - gross) == Money(-660) and -gross == Money(-1_100)
    assert int(gross) == 1_100 and gross.to_json() == 1_100


def test_money_takes_integers_only_and_no_rounding_argument():
    with pytest.raises(LegislationError):
        Money(1.5)
    with pytest.raises(LegislationError):
        times(100, 1.5)
    with pytest.raises(TypeError):
        Money(1).times(pct("10"), rounding="HALF_EVEN")


# ── The calendar (§3.4, §4.5) ─────────────────────────────────────────────────

SPAIN = {
    "weekend": ["sat", "sun"],
    "years": {"2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]},
    "legislation": "Ley 39/2015 Art. 30.7 (illustrative)",
}
CV = {
    "years": {"2027": [{"date": "2027-10-09", "name": {"ca-valencia": "9 d'Octubre"}}]}
}


def test_the_calendar_is_the_union_of_the_layers():
    cal = Calendar([SPAIN, CV])
    assert cal.weekend == {"sat", "sun"} and cal.holidays == {
        2027: {"2027-01-01", "2027-10-09"}
    }
    assert cal.is_working_day("2027-10-08") and not cal.is_working_day("2027-10-09")
    assert not cal.is_working_day("2027-10-10")  # a Sunday


def test_working_days_step_over_the_weekend_and_the_holidays():
    cal = Calendar([SPAIN, {"years": {"2026": []}}])
    assert (
        cal.add("2026-12-31", working_days(2)) == "2027-01-05"
    )  # Fri 1 Jan holiday, Sat, Sun
    assert cal.add("2027-01-05", working_days(-2)) == "2026-12-31"
    assert cal.add("2027-01-05", working_days(0)) == "2027-01-05"
    assert (
        cal.add("2027-01-07", working_days(1)) == "2027-01-08"
        and cal.add("2027-01-08", working_days(1)) == "2027-01-11"
    )


def test_days_months_and_years_are_calendar_arithmetic():
    cal = Calendar([SPAIN])
    assert cal.add("2026-12-31", days(45)) == "2027-02-14"
    assert (
        cal.add("2027-01-31", months(1)) == "2027-02-28"
    )  # date to date: the last day of a shorter month
    assert cal.add("2028-01-31", months(1)) == "2028-02-29"
    assert cal.add("2027-11-30", months(3)) == "2028-02-29"
    assert cal.add("2028-02-29", years(1)) == "2029-02-28"
    assert cal.add("2027-03-01", days(-1)) == "2027-02-28"


def test_a_count_into_a_year_with_no_entries_fails_closed():
    cal = Calendar([SPAIN])
    with pytest.raises(LegislationError, match="no entries for 2028"):
        cal.add("2027-12-30", working_days(2))
    with pytest.raises(LegislationError, match="no entries for 2026"):
        cal.is_working_day("2026-12-31")
    assert (
        cal.add("2027-12-30", days(5)) == "2028-01-04"
    )  # calendar days need no entries
    assert Calendar([{"years": {"2028": []}}]).is_working_day(
        "2028-01-03"
    )  # listed with no holidays: known


@pytest.mark.parametrize(
    "bad",
    [
        {"weekend": ["saturday"]},
        {"years": {"27": []}},
        {"years": {"2027": [{"date": "2026-01-01"}]}},
        {"years": {"2027": [{"date": "2027-02-30"}]}},
        [],
    ],
)
def test_a_wrong_calendar_is_refused(bad):
    with pytest.raises(LegislationError):
        Calendar([bad])


def test_a_day_is_written_yyyy_mm_dd():
    with pytest.raises(LegislationError):
        Calendar([SPAIN]).add("1/1/2027", days(1))
    with pytest.raises(LegislationError):
        Calendar([SPAIN]).add("2027-01-01", 3)


# ── A chapter: what the file contains (§4) ────────────────────────────────────

ES_PROCEDURE = """
from realms.legislation import above, fixed, forbid, months, overridable, working_days

class Procedure(above("procedure")):
    uses = []

    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

    over_six_months = forbid("procedure_define", "<F tree: S2>", legislation="Ley 39/2015 Art. 21.2")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))
"""


def test_a_chapter_is_one_class_named_after_its_domain_extending_above():
    c = codex()
    cls = c.load("eu/es", "procedure", ES_PROCEDURE)
    assert (
        cls.__name__ == "Procedure"
        and cls.__bases__ == (Chapter,)
        and cls.__file__ == "eu/es/procedure.py"
    )
    assert sorted(cls.__members__) == [
        "default_decision_deadline",
        "over_six_months",
        "working_days_after",
    ]
    assert {m.kind for m in cls.__members__.values()} == {"setting", "forbid", "helper"}
    assert c.resolved("procedure") is cls and c.top("procedure") is cls


def test_members_are_named_after_ggg_operations():
    c = codex()
    cls = c.load(
        "eu",
        "data_protection",
        """
from realms.legislation import above, extension
class DataProtection(above("data_protection")):
    @extension(legislation="GDPR Art. 5-6")
    def check_document_share(self, args):
        pass
    @extension(legislation="GDPR")
    def on_document_share(self, event):
        pass
    @extension(legislation="GDPR")
    def compute_tax_assess(self, entry, subject):
        return []
""",
    )
    kinds = {k: m.kind for k, m in cls.__members__.items()}
    assert kinds == {
        "check_document_share": "guard",
        "on_document_share": "hook",
        "compute_tax_assess": "compute",
    }


@pytest.mark.parametrize(
    "member, reason",
    [
        (
            "    @fixed(legislation='x')\n    def on_user_delete(self, event): pass",
            "must name a GGG operation",
        ),
        (
            "    @fixed(legislation='x')\n    def check_nothing(self, args): pass",
            "must name a GGG operation",
        ),
        (
            "    @fixed(legislation='x')\n    def on_(self, event): pass",
            "names no operation",
        ),
        (
            "    rule = forbid('no_such_op', True, legislation='x')",
            "must name a GGG operation",
        ),
    ],
)
def test_a_hook_guard_compute_or_forbid_names_a_ggg_operation(member, reason):
    text = (
        "from realms.legislation import above, fixed, forbid\nclass Tax(above('tax')):\n"
        + member
        + "\n"
    )
    assert reason in refused(codex(), "eu/es", "tax", text)


def test_without_the_operation_list_names_are_not_checked():
    c = codex(operations=None)
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, fixed\nclass Tax(above('tax')):\n"
        "    @fixed(legislation='x')\n    def on_anything(self, event): pass\n",
    )


@pytest.mark.parametrize(
    "op", ["codex_revert", "codex_revert_fast", "it_team_set", "sync_source_remove"]
)
@pytest.mark.parametrize(
    "form",
    [
        "    @fixed(legislation='x')\n    def on_%s(self, event): pass",
        "    @fixed(legislation='x')\n    def check_%s(self, args): pass",
        "    rule = forbid('%s', True, legislation='x')",
    ],
)
def test_reserved_operations_cannot_be_hooked_guarded_or_forbidden(op, form):
    text = (
        "from realms.legislation import above, fixed, forbid\nclass Tax(above('tax')):\n"
        + form % op
        + "\n"
    )
    assert "%s is reserved" % op in refused(
        codex(operations=None), "eu/es", "tax", text
    )


def test_the_class_name_and_the_base_are_checked():
    c = codex()
    assert "is named DataProtection" in refused(
        c,
        "eu",
        "data_protection",
        "from realms.legislation import above\nclass Dp(above('data_protection')): pass\n",
    )
    assert 'above("tax")' in refused(
        c,
        "eu",
        "tax",
        "from realms.legislation import above\nclass Tax(above('procedure')): pass\n",
    )
    assert "extends above" in refused(
        c,
        "eu",
        "tax",
        "from realms.legislation import above, Chapter\nclass Tax(Chapter): pass\n",
    )
    assert "extends above" in refused(
        c,
        "eu",
        "tax",
        "from realms.legislation import above, Chapter\nclass Tax(above('tax'), Chapter): pass\n",
    )
    assert "one class per file" in refused(
        c, "eu", "tax", "from realms.legislation import above\nx = above('tax')\n"
    )
    assert "one class per file" in refused(
        c,
        "eu",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')): pass\n"
        "First = Tax\nclass Tax(above('tax')): pass\n",
    )
    assert "is named Tax" in refused(
        c,
        "eu",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')): pass\n"
        "class Tax2(above('tax')): pass\n",
    )


def test_nothing_else_at_module_level_except_sdk_imports_and_constants():
    c = codex()
    c.load(
        "eu",
        "tax",
        "from realms.legislation import above, pct as p\nLIMIT = p('1.30')\nNAMES = ['a']\n"
        "class Tax(above('tax')): pass\n",
    )
    assert "nothing else at module level" in refused(
        c,
        "eu/es",
        "tax",
        "from realms.legislation import above\ndef helper(): pass\nclass Tax(above('tax')): pass\n",
    )
    assert "nothing else at module level" in refused(
        c,
        "eu/es",
        "tax",
        "from realms.legislation import above\nX = 1.5\nclass Tax(above('tax')): pass\n",
    )


@pytest.mark.parametrize(
    "line",
    [
        "import os",
        "from json import dumps",
        "import realms.legislation",
        "from realms import legislation",
        "from . import x",
        "from ggg.finance import Tax",
    ],
)
def test_imports_only_from_the_sdk_and_ggg(line):
    assert "imports only from realms.legislation and ggg" in refused(
        codex(),
        "eu",
        "tax",
        line
        + "\nfrom realms.legislation import above\nclass Tax(above('tax')): pass\n",
    )


def test_ggg_entities_are_importable_read_only_through_the_codex_modules():
    ggg = type(sys)("ggg")
    ggg.Member = type("Member", (), {})
    c = Codex(LAYERS, realm=VALENCIA, modules={"ggg": ggg})
    cls = c.load(
        VALENCIA,
        "tax",
        "import ggg\nfrom ggg import Member\nfrom realms.legislation import above\n"
        "class Tax(above('tax')):\n    def ibi_bonus(self, land): return Member\n",
    )
    assert cls(c.context()).ibi_bonus(None) is ggg.Member


@pytest.mark.parametrize(
    "builtin", ["exec", "eval", "compile", "globals", "locals", "setattr", "open"]
)
def test_the_banned_builtins_are_gone(builtin):
    text = (
        "from realms.legislation import above\nclass Tax(above('tax')):\n    x = %s\n"
        % builtin
    )
    assert "NameError" in refused(codex(), "eu", "tax", text)


def test_public_members_above_the_realm_carry_a_citation_and_the_realm_need_not():
    c = codex()
    assert 'needs legislation="..."' in refused(
        c,
        "eu/es",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n"
        "    capital = False\n",
    )
    assert 'needs legislation="..."' in refused(
        c,
        "eu/es",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n"
        "    def ibi_bonus(self, land): return 0\n",
    )
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, overridable\nclass Tax(above('tax')):\n"
        "    capital = overridable(False, legislation='TRLRHL Art. 72.3')\n    _secret = 1\n"
        "    def _private(self): return 1\n",
    )
    cls = c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    capital = True\n"
        "    def ibi_bonus(self, land): return 0\n",
    )
    assert cls.capital is True


@pytest.mark.parametrize(
    "text, reason",
    [
        ("    x = 1.5", "floats are not allowed"),
        ("    class Inner: pass", "no nested classes"),
        ("    __slots__ = ()", "no dunder members"),
        ("    now = 3", "reserved by the SDK"),
        ("    def calendar(self): pass", "reserved by the SDK"),
        ("    uses = 'procedure'", "uses lists the domains"),
        ("    uses = ['a', 'a']", "uses lists the domains"),
        (
            "    x = property(lambda self: 1)",
            "a member is a method, a setting or a forbid",
        ),
        ("    x = fixed(1.5, legislation='x')", "never a float"),
        ("    x = fixed({1: 'a'}, legislation='x')", "never a float"),
        (
            "    x = fixed(legislation='x', check=lambda v: True)",
            "check= is for settings",
        ),
        ("    x = fixed(lambda self: 1, legislation='x')", "a method is marked"),
    ],
)
def test_what_a_chapter_cannot_contain(text, reason):
    assert reason in refused(
        codex(),
        VALENCIA,
        "tax",
        "from realms.legislation import above, fixed\nclass Tax(above('tax')):\n"
        + text
        + "\n",
    )


def test_a_chapter_is_defined_only_by_loading_it():
    with pytest.raises(LegislationError, match="Codex.load"):
        above("tax")
    with pytest.raises(LegislationError, match="Codex.load"):
        type("Tax", (Chapter,), {})


def test_the_realm_domain_is_filled_only_in_the_realms_own_layer():
    c = codex()
    assert "realm domain may only be filled in the realm's own layer" in refused(
        c,
        "eu/es",
        "realm",
        "from realms.legislation import above\nclass Realm(above('realm')): pass\n",
    )
    cls = c.load(
        VALENCIA,
        "realm",
        "from realms.legislation import above\nclass Realm(above('realm')):\n"
        "    def on_codex_activate(self, event):\n        self.ggg.zone_set(zone='Ciutat Vella')\n"
        "    def compute_realm_join(self, user, quarters):\n        return quarters[0]\n",
    )
    ctx = c.context()
    realm = cls(ctx)
    realm.on_codex_activate(Event("realm", {}, {}, 0))
    assert ctx.effects.queued == [["zone_set", {"zone": "Ciutat Vella"}]]
    assert realm.compute_realm_join("alice", ["Q-1"]) == "Q-1"


def test_chapters_load_top_to_bottom_once_per_layer_and_only_into_known_layers():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')): pass\n",
    )
    assert "already loaded" in refused(
        c,
        "eu/es",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')): pass\n",
    )
    assert "already loaded" in refused(
        c,
        "eu",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')): pass\n",
    )
    with pytest.raises(LegislationError, match="not a layer"):
        c.load("fr", "tax", "")
    with pytest.raises(LegislationError, match="not a source id"):
        Codex(["EU"])
    with pytest.raises(LegislationError, match="not a domain id"):
        c.load("eu", "Tax", "")


def test_a_syntax_error_or_a_crash_names_the_file():
    assert refused(codex(), "eu", "tax", "class Tax(\n").startswith("eu/tax.py:")
    assert "ZeroDivisionError" in refused(codex(), "eu", "tax", "x = 1 // 0\n")


# ── Marks (§4.1): each line of the table, and the §5.5 refusal ───────────────

ES_REGISTER = """# legislation/eu/es/register.py
from realms.legislation import Ref, above, fixed

class Register(above("register")):
    authority = fixed(Ref("external", "INE"), legislation="Ley 7/1985 Art. 17.3")

    @fixed(legislation="Ley 7/1985 Art. 17.3")
    def on_user_add(self, event):
        self.ggg.notification_send(recipient=self.authority, topic="padron", title="alta", body=event.args["user"])
"""
VALENCIA_REGISTER = """from realms.legislation import above
class Register(above("register")):
    def on_user_add(self, event):
        pass
"""


def test_fixed_a_lower_layer_may_not_define_the_same_name():
    c = codex()
    c.load("eu/es", "register", ES_REGISTER)
    assert refused(c, VALENCIA, "register", VALENCIA_REGISTER) == (
        "eu/es/vc/46250-valencia/register.py:3: on_user_add is fixed by eu/es/register.py (Ley 7/1985 Art. 17.3)"
    )
    assert refused(
        c,
        VALENCIA,
        "register",
        "from realms.legislation import above\nclass Register(above('register')):\n"
        "    authority = None\n",
    ) == (
        "eu/es/vc/46250-valencia/register.py:3: authority is fixed by eu/es/register.py (Ley 7/1985 Art. 17.3)"
    )


def test_fixed_only_this_definition_runs_and_this_value_holds():
    c = codex()
    c.load("eu/es", "register", ES_REGISTER)
    cls = c.load(
        VALENCIA,
        "register",
        "from realms.legislation import above\nclass Register(above('register')):\n"
        "    def on_user_remove(self, event): pass\n",
    )
    ctx = c.context()
    cls(ctx).on_user_add(Event("clerk", {"user": "alice"}, {}, 0))
    assert ctx.effects.queued == [
        [
            "notification_send",
            {
                "recipient": {"kind": "external", "id": "INE"},
                "topic": "padron",
                "title": "alta",
                "body": "alice",
            },
        ]
    ]
    assert cls.authority == Ref("external", "INE")


ES_LOCAL_REGIME = """
from realms.legislation import above, extension, Ref

class LocalRegime(above("local_regime")):
    uses = ["procedure"]
    notified = extension(["AGE"], legislation="Ley 7/1985 Art. 65.2")
    scopes = extension({"state": "AGE"}, legislation="x")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def check_proposal_create(self, args):
        if args.get("effective_at") is None:
            return self.deny("an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_codex_install(self, event):
        event.order = ["es"]
        self.ggg.notification_send(recipient=Ref("external", "AGE"), topic="ordinance", title="Comunicació", body=event.result["codex"])

    @extension(legislation="x")
    def bodies(self):
        return ["AGE", "GVA"]
"""
CV_LOCAL_REGIME = """
from realms.legislation import above, extension

class LocalRegime(above("local_regime")):
    uses = ["language"]
    notified = extension(["GVA", "AGE"], legislation="x")
    scopes = extension({"region": "GVA"}, legislation="x")

    @extension(legislation="Llei 4/1983; Llei 8/2010")
    def on_codex_install(self, event):
        event.languages_missing = ["ca-valencia"] if event.order == ["es"] else []

    @extension(legislation="x")
    def check_proposal_create(self, args):
        if args.get("languages") == []:
            return self.deny("a translation is needed")

    @extension(legislation="x")
    def bodies(self):
        return ["GVA", "EMSHI"]
"""
VALENCIA_LOCAL_REGIME = """
from realms.legislation import above

class LocalRegime(above("local_regime")):
    notified = ["Ajuntament"]
    scopes = {"town": "Ajuntament"}

    def on_codex_install(self, event):
        title = "Nova ordenança" + (" (traducció pendent)" if event.languages_missing else "")
        self.ggg.notification_send(recipient="members", topic="ordinance", title=title, body=event.result["codex"])

    def check_proposal_create(self, args):
        if args.get("recipient") == "bad":
            return self.deny("bad recipient")

    def bodies(self):
        return ["Ajuntament", "AGE"]
"""


def chain():
    c = codex()
    for source, text in (
        ("eu/es", ES_LOCAL_REGIME),
        ("eu/es/vc", CV_LOCAL_REGIME),
        (VALENCIA, VALENCIA_LOCAL_REGIME),
    ):
        c.load(source, "local_regime", text)
    return c


def test_extension_hooks_run_every_layer_top_to_bottom_sharing_the_event():
    c = chain()
    ctx = c.context()
    event = Event("alice", {"document": "D-1"}, {"codex": "C-2"}, 9)
    c.instance("local_regime", ctx).on_codex_install(event)
    assert [e[0] for e in ctx.effects.queued] == [
        "notification_send",
        "notification_send",
    ]
    assert ctx.effects.queued[0][1]["recipient"] == {"kind": "external", "id": "AGE"}
    assert ctx.effects.queued[1][1]["title"] == "Nova ordenança (traducció pendent)"
    assert event.languages_missing == ["ca-valencia"]


def test_a_lower_layer_reads_but_cannot_change_what_a_layer_above_set_on_the_event():
    c = codex()
    c.load("eu/es", "local_regime", ES_LOCAL_REGIME)
    c.load(
        VALENCIA,
        "local_regime",
        "from realms.legislation import above\nclass LocalRegime(above('local_regime')):\n"
        "    def on_codex_install(self, event):\n        event.order = event.order + ['valencia']\n",
    )
    with pytest.raises(LegislationError, match="event.order was set by a layer above"):
        c.instance("local_regime", c.context()).on_codex_install(
            Event("alice", {}, {"codex": "C"}, 0)
        )
    with pytest.raises(LegislationError, match="event.args was set by a layer above"):
        Event("alice", {}, {}, 0).args = {}


def test_extension_guards_run_top_to_bottom_and_the_first_deny_stops():
    guard = chain().instance("local_regime", codex().context()).check_proposal_create
    assert guard(
        {"effective_at": None, "languages": [], "recipient": "bad"}
    ).reason.startswith("an ordinance takes effect")
    assert (
        guard({"effective_at": 1, "languages": [], "recipient": "bad"}).reason
        == "a translation is needed"
    )
    assert (
        guard({"effective_at": 1, "languages": ["es"], "recipient": "bad"}).reason
        == "bad recipient"
    )
    assert guard({"effective_at": 1, "languages": ["es"], "recipient": "ok"}) is None


def test_extension_helpers_and_settings_merge_in_layer_order():
    c = chain()
    cls = c.resolved("local_regime")
    assert cls(c.context()).bodies() == [
        "AGE",
        "GVA",
        "EMSHI",
        "Ajuntament",
    ]  # concatenated, without duplicates
    assert cls.notified == ["AGE", "GVA", "Ajuntament"]
    assert cls.scopes == {"state": "AGE", "region": "GVA", "town": "Ajuntament"}
    assert c.resolved_at("local_regime", "eu/es/vc").notified == ["AGE", "GVA"]


def test_extension_dicts_take_new_keys_only_and_other_types_are_errors():
    c = codex()
    c.load("eu/es", "local_regime", ES_LOCAL_REGIME)
    assert "'state' already set above" in refused(
        c,
        "eu/es/vc",
        "local_regime",
        "from realms.legislation import above, extension\nclass LocalRegime(above('local_regime')):\n"
        "    scopes = extension({'state': 'GVA'}, legislation='x')\n",
    )
    assert "merge lists, sets and dicts, not list and str" in refused(
        c,
        "eu/es/vc",
        "local_regime",
        "from realms.legislation import above, extension\nclass LocalRegime(above('local_regime')):\n"
        "    notified = extension('GVA', legislation='x')\n",
    )
    c.load(
        "eu/es/vc",
        "local_regime",
        "from realms.legislation import above, extension\nclass LocalRegime(above('local_regime')):\n"
        "    @extension(legislation='x')\n    def bodies(self): return 'GVA'\n",
    )
    with pytest.raises(
        LegislationError, match="merge lists, sets and dicts, not list and str"
    ):
        c.instance("local_regime", c.context()).bodies()


def test_extension_sets_join():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, extension\nclass Tax(above('tax')):\n"
        "    @extension(legislation='x')\n    def codes(self): return {'ibi_urban'}\n",
    )
    c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n"
        "    def codes(self): return {'ibi_rustic', 'ibi_urban'}\n",
    )
    assert c.instance("tax", c.context()).codes() == {"ibi_urban", "ibi_rustic"}


def test_extension_a_lower_definition_must_not_call_super():
    c = codex()
    c.load("eu/es", "local_regime", ES_LOCAL_REGIME)
    c.load(
        VALENCIA,
        "local_regime",
        "from realms.legislation import above\nclass LocalRegime(above('local_regime')):\n"
        "    def on_codex_install(self, event):\n        super().on_codex_install(event)\n",
    )
    with pytest.raises(LegislationError, match="must not call super"):
        c.instance("local_regime", c.context()).on_codex_install(
            Event("a", {}, {"codex": "C"}, 0)
        )


def test_extension_cannot_be_re_marked_or_change_kind_below():
    c = codex()
    c.load("eu/es", "local_regime", ES_LOCAL_REGIME)
    assert "cannot mark it fixed" in refused(
        c,
        VALENCIA,
        "local_regime",
        "from realms.legislation import above, fixed\nclass LocalRegime(above('local_regime')):\n"
        "    @fixed\n    def on_codex_install(self, event): pass\n",
    )
    assert "cannot make it a setting" in refused(
        c,
        VALENCIA,
        "local_regime",
        "from realms.legislation import above\nclass LocalRegime(above('local_regime')):\n"
        "    on_codex_install = 1\n",
    )


def test_overridable_the_lowest_definition_runs_and_may_call_super():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, overridable\nclass Tax(above('tax')):\n"
        "    capital = overridable(False, legislation='TRLRHL Art. 72.3')\n"
        "    @overridable(legislation='TRLRHL Art. 74')\n    def ibi_bonus(self, land): return 10\n",
    )
    c.load(
        "eu/es/vc",
        "tax",
        "from realms.legislation import above, fixed\nclass Tax(above('tax')):\n"
        "    @fixed(legislation='x')\n    def ibi_bonus(self, land): return super().ibi_bonus(land) + 5\n",
    )
    # CV re-marked ibi_bonus fixed: València may not redefine it.
    assert "ibi_bonus is fixed by eu/es/vc/tax.py (x)" in refused(
        c,
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    def ibi_bonus(self, land): return 0\n",
    )
    cls = c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    capital = True\n",
    )
    assert cls.capital is True and cls(c.context()).ibi_bonus(None) == 15
    assert c.resolved_at("tax", "eu/es").capital is False


def test_no_mark_is_overridable():
    c = codex()
    c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    capital = False\n",
    )
    assert c.resolved("tax").__members__["capital"].mark is None
    c2 = codex(layers=("a", "b"), realm="b")
    c2.load(
        "a",
        "tax",
        "from realms.legislation import above, overridable\nclass Tax(above('tax')):\n"
        "    capital = overridable(False, legislation='x')\n"
        "    @overridable(legislation='x')\n    def ibi_bonus(self, land): return 1\n",
    )
    cls = c2.load(
        "b",
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    capital = True\n"
        "    def ibi_bonus(self, land): return 2\n",
    )
    assert cls.capital is True and cls(c2.context()).ibi_bonus(None) == 2


def test_a_setting_cannot_become_a_method_or_the_other_way_round():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, overridable\nclass Tax(above('tax')):\n"
        "    capital = overridable(False, legislation='x')\n"
        "    @overridable(legislation='x')\n    def ibi_bonus(self, land): return 0\n",
    )
    assert (
        "capital is a setting in eu/es/tax.py (x): a lower layer cannot make it a method"
        in refused(
            c,
            VALENCIA,
            "tax",
            "from realms.legislation import above\nclass Tax(above('tax')):\n    def capital(self): return True\n",
        )
    )
    assert (
        "ibi_bonus is a method in eu/es/tax.py (x): a lower layer cannot make it a setting"
        in refused(
            c,
            VALENCIA,
            "tax",
            "from realms.legislation import above\nclass Tax(above('tax')):\n    ibi_bonus = 0\n",
        )
    )


def test_forbids_are_never_overridable_and_never_removable():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, forbid\nclass Tax(above('tax')):\n"
        "    ibi_urban_cap = forbid('tax_policy_propose', 'expr', each='taxes', legislation='TRLRHL Art. 72.3')\n",
    )
    assert refused(
        c,
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    ibi_urban_cap = None\n",
    ) == (
        "eu/es/vc/46250-valencia/tax.py:3: ibi_urban_cap is a forbid of eu/es/tax.py (TRLRHL Art. 72.3): forbids are never overridable"
    )
    assert "forbids are never overridable" in refused(
        c,
        VALENCIA,
        "tax",
        "from realms.legislation import above, forbid\nclass Tax(above('tax')):\n"
        "    ibi_urban_cap = forbid('tax_policy_propose', 'other')\n",
    )
    assert "forbids are never overridable" in refused(
        c,
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    def ibi_urban_cap(self): pass\n",
    )
    rule = c.resolved("tax").ibi_urban_cap
    assert (rule.op, rule.expression, rule.each, rule.legislation) == (
        "tax_policy_propose",
        "expr",
        "taxes",
        "TRLRHL Art. 72.3",
    )


def test_a_forbid_names_an_operation_and_a_list():
    with pytest.raises(LegislationError):
        forbid("", True)
    with pytest.raises(LegislationError):
        forbid("tax_policy_propose", True, each=3)


# ── above() (§4.2) ────────────────────────────────────────────────────────────


def test_above_is_the_nearest_layer_above_with_a_chapter_in_the_domain():
    c = codex()
    es = c.load(
        "eu/es",
        "water",
        "from realms.legislation import above, fixed\nclass Water(above('water')):\n"
        "    x = fixed(1, legislation='x')\n",
    )
    assert es.__bases__ == (Chapter,)
    # CV writes no water chapter: EMSHI's base is Spain's class, whatever version is pinned there.
    c2 = Codex(("eu", "eu/es", "eu/es/vc", "eu/es/vc/emshi", VALENCIA), realm=VALENCIA)
    es2 = c2.load(
        "eu/es",
        "water",
        "from realms.legislation import above, fixed\nclass Water(above('water')):\n"
        "    x = fixed(1, legislation='x')\n",
    )
    emshi = c2.load(
        "eu/es/vc/emshi",
        "water",
        "from realms.legislation import above, fixed\nclass Water(above('water')):\n"
        "    y = fixed(2, legislation='y')\n",
    )
    assert emshi.__bases__ == (es2,) and emshi.x == 1
    assert "no procedure chapter" in str(
        pytest.raises(LegislationError, c2.resolved, "procedure").value
    )


# ── Settings (§4.3) ───────────────────────────────────────────────────────────


def test_settings_are_class_attributes_read_as_self_setting():
    c = codex()
    c.load("eu/es", "procedure", ES_PROCEDURE)
    cls = c.load(
        VALENCIA,
        "procedure",
        "from realms.legislation import above, days\nclass Procedure(above('procedure')):\n"
        "    default_decision_deadline = days(60)\n",
    )
    assert cls(c.context()).default_decision_deadline == days(60)
    assert c.resolved_at("procedure", "eu/es").default_decision_deadline == months(3)


def test_a_town_with_no_chapter_gets_the_default_above():
    c = codex()
    cls = c.load("eu/es", "procedure", ES_PROCEDURE)
    assert c.resolved("procedure") is cls and cls(
        c.context()
    ).default_decision_deadline == months(3)


def test_setting_values_are_json_data_or_periods_and_rates_are_integers():
    c = codex()
    cls = c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above, pct, Ref\nclass Tax(above('tax')):\n"
        "    large_family_bonus = {'general': pct('60'), 'special': pct('90')}\n"
        "    population = 807_693\n    capital = True\n    name = None\n"
        "    authority = Ref('external', 'INE')\n    codes = [['a', 1], {'b': None}]\n",
    )
    assert cls.large_family_bonus == {"general": 60_000_000, "special": 90_000_000}


def test_check_runs_at_install_on_the_winning_value():
    es = (
        "from realms.legislation import above, overridable, working_days\nclass LocalRegime(above('local_regime')):\n"
        "    comment_period = overridable(working_days(30), legislation='Ley 7/1985 Art. 49', check=lambda v: v >= working_days(30))\n"
    )
    c = codex()
    c.load("eu/es", "local_regime", es)
    c.load(
        VALENCIA,
        "local_regime",
        "from realms.legislation import above, working_days\nclass LocalRegime(above('local_regime')):\n"
        "    comment_period = working_days(45)\n",
    )
    c.check()
    c = codex()
    c.load("eu/es", "local_regime", es)
    c.load(
        VALENCIA,
        "local_regime",
        "from realms.legislation import above, working_days\nclass LocalRegime(above('local_regime')):\n"
        "    comment_period = working_days(20)\n",
    )
    with pytest.raises(Refused) as e:
        c.check()
    assert str(e.value) == (
        "eu/es/vc/46250-valencia/local_regime.py:3: comment_period = working_days(20) fails the check of "
        "eu/es/local_regime.py (Ley 7/1985 Art. 49)"
    )


def test_check_fails_closed_on_a_different_unit():
    c = codex()
    c.load(
        "eu/es",
        "local_regime",
        "from realms.legislation import above, overridable, working_days\n"
        "class LocalRegime(above('local_regime')):\n"
        "    comment_period = overridable(working_days(30), legislation='x', check=lambda v: v >= working_days(30))\n",
    )
    c.load(
        VALENCIA,
        "local_regime",
        "from realms.legislation import above, days\nclass LocalRegime(above('local_regime')):\n"
        "    comment_period = days(45)\n",
    )
    with pytest.raises(
        Refused,
        match="comment_period = days\\(45\\) fails the check .* not of one unit",
    ):
        c.check()


def test_every_check_up_the_chain_applies_and_the_default_passes_its_own():
    c = codex()
    c.load(
        "eu/es",
        "tax",
        "from realms.legislation import above, overridable\nclass Tax(above('tax')):\n"
        "    rate = overridable(5, legislation='x', check=lambda v: v <= 10)\n",
    )
    c.load(
        "eu/es/vc",
        "tax",
        "from realms.legislation import above, overridable\nclass Tax(above('tax')):\n"
        "    rate = overridable(6, legislation='y', check=lambda v: v >= 6)\n",
    )
    c.check()
    c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n    rate = 12\n",
    )
    with pytest.raises(Refused, match="rate = 12 fails the check of eu/es/tax.py"):
        c.check()
    with pytest.raises(LegislationError):
        overridable(1, check=3)


# ── Calling another domain (§4.4) ─────────────────────────────────────────────


def with_domains():
    c = codex()
    c.load("eu/es", "procedure", ES_PROCEDURE)
    c.load(
        "eu/es",
        "language",
        "from realms.legislation import above, extension\nclass Language(above('language')):\n"
        "    co_official = extension(['es'], legislation='x')\n",
    )
    c.load(
        "eu/es/vc",
        "language",
        "from realms.legislation import above, extension\nclass Language(above('language')):\n"
        "    co_official = extension(['ca-valencia'], legislation='Estatut Art. 6')\n",
    )
    c.load(
        "eu/es",
        "local_regime",
        "from realms.legislation import above, fixed\nclass LocalRegime(above('local_regime')):\n"
        "    uses = ['procedure', 'language']\n"
        "    @fixed(legislation='x')\n    def earliest(self, day):\n"
        "        return self.legislation.procedure.working_days_after(day, 15)\n"
        "    @fixed(legislation='y')\n    def languages(self):\n"
        "        return self.legislation.language.co_official, self.layer.language.co_official\n"
        "    @fixed(legislation='z')\n    def secret(self):\n"
        "        return self.legislation.procedure._hidden\n"
        "    @fixed(legislation='w')\n    def undeclared(self):\n"
        "        return self.legislation.tax\n",
    )
    c.load(
        "eu/es/vc",
        "local_regime",
        "from realms.legislation import above, fixed\nclass LocalRegime(above('local_regime')):\n"
        "    uses = ['language']\n    @fixed(legislation='x')\n    def at_cv(self):\n"
        "        return self.layer.language.co_official\n",
    )
    c.load(
        VALENCIA,
        "language",
        "from realms.legislation import above\nclass Language(above('language')):\n"
        "    co_official = ['en']\n",
    )
    c.calendar_layer("eu/es", SPAIN)
    return c


def test_legislation_resolves_through_every_layer_and_layer_only_down_to_its_own():
    c = with_domains()
    chapter = c.instance("local_regime", c.context())
    assert chapter.earliest("2026-12-31") == "2027-01-22"
    assert chapter.languages() == (
        ["es", "ca-valencia", "en"],
        ["es"],
    )  # Spain's own view ignores lower layers
    assert chapter.at_cv() == [
        "es",
        "ca-valencia",
    ]  # CV's view: Spain and CV, not València


def test_only_declared_domains_and_only_public_members():
    c = with_domains()
    chapter = c.instance("local_regime", c.context())
    with pytest.raises(LegislationError, match="does not declare 'tax' in uses"):
        chapter.undeclared()
    with pytest.raises(LegislationError, match="_hidden is private to its chapter"):
        chapter.secret()


# ── The self API (§4.5) ───────────────────────────────────────────────────────


def test_self_ggg_queues_effects_as_plain_data_in_order():
    c = codex()
    cls = c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above, Ref, days, pct\nclass Tax(above('tax')):\n"
        "    def on_tax_assess(self, event):\n"
        "        inv = self.ggg.invoice_create(payer=Ref('user', 'alice'), amount=1, due=days(30), rate=pct('1'))\n"
        "        self.ggg.notification_send(recipient=Ref('user', 'alice'), body=inv['invoice'])\n",
    )
    ctx = c.context(now=1_700_000_000, approvals=["Junta de Govern Local"])
    cls(ctx).on_tax_assess(Event("clerk", {}, {}, 0))
    assert ctx.effects.queued == [
        [
            "invoice_create",
            {
                "payer": {"kind": "user", "id": "alice"},
                "amount": 1,
                "due": {"count": 30, "unit": "Day"},
                "rate": 1_000_000,
            },
        ],
        [
            "notification_send",
            {"recipient": {"kind": "user", "id": "alice"}, "body": "$eff:0:invoice"},
        ],
    ]


def test_self_ggg_refuses_an_operation_ggg_does_not_have():
    c = codex()
    cls = c.load(
        VALENCIA,
        "tax",
        "from realms.legislation import above\nclass Tax(above('tax')):\n"
        "    def on_tax_assess(self, event):\n        self.ggg.make_money(amount=1)\n",
    )
    with pytest.raises(LegislationError, match="'make_money' is not a GGG operation"):
        cls(c.context()).on_tax_assess(Event("a", {}, {}, 0))


def test_deny_now_approved_by_and_the_calendar():
    c = codex()
    c.calendar_layer("eu/es", SPAIN)
    cls = c.load(
        VALENCIA,
        "data_protection",
        """
from realms.legislation import above, working_days
class DataProtection(above("data_protection")):
    def check_document_share(self, args):
        if args["recipient"] != "authority" and not self.approved_by("Junta de Govern Local"):
            return self.deny("a recipient that is not an authority needs the Junta de Govern Local")
    def when(self):
        return self.now(), self.calendar.add("2026-12-31", working_days(1))
""",
    )
    chapter = cls(c.context(now=42, approvals=["Junta de Govern Local"]))
    assert chapter.check_document_share({"recipient": "company"}) is None
    denial = cls(c.context(now=42)).check_document_share({"recipient": "company"})
    assert (
        denial.reason
        == "a recipient that is not an authority needs the Junta de Govern Local"
        and "deny" in repr(denial)
    )
    assert chapter.when() == (42, "2027-01-04")


def test_ref_is_ggg_s_ref():
    assert Ref("external", "INE") == Ref("external", "INE") and Ref(
        "external", "INE"
    ) != Ref("user", "INE")
    assert (
        Ref("external", "INE").to_json() == {"kind": "external", "id": "INE"}
        and len({Ref("a", "b"), Ref("a", "b")}) == 1
    )
    with pytest.raises(LegislationError):
        Ref("external", 3)


def test_marks_are_usable_both_ways():
    setting = fixed(["ca-valencia", "es"], legislation="Estatut Art. 6")
    assert (setting.value, setting.mark, setting.legislation, setting.check) == (
        ["ca-valencia", "es"],
        "fixed",
        "Estatut Art. 6",
        None,
    )
    method = extension(legislation="x")(lambda self: 1)
    assert method.mark == "extension" and method.legislation == "x"
    bare = overridable(lambda self: 1)
    assert bare.mark == "overridable" and bare.legislation is None


# ── Self-contained: no CLI dependency ─────────────────────────────────────────


def test_the_canisters_copy_imports_nothing_from_the_cli_or_the_canister():
    """``realms.legislation`` as the realm bundles it (src/realm_backend/realms), imported alone."""
    canister = os.path.join(
        os.path.dirname(__file__), "..", "..", "src", "realm_backend"
    )
    code = (
        "import sys; sys.path.insert(0, %r); import realms.legislation; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('typer', 'rich', 'requests', 'pydantic', 'click', "
        "'core', 'api', 'ggg', 'basilisk', 'ic_basilisk_toolkit', 'json', 're', 'typing', 'datetime', 'functools')))"
        % os.path.abspath(canister)
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
