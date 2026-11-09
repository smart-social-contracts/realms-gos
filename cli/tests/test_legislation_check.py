"""``realms legislation check``: a legislation/ tree loaded as the realm loads it, refusals printed as the checker does."""

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from realms.cli.commands import legislation
from realms.cli.commands.legislation import legislation_app

# GGG's fact schema for the operations the forbids below name (the canister's ggg_facts has them all, with these shapes)
RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
FACTS = {
    "tax_policy_propose": {"facts": {"policy": "String"}},
    "tax_policy_propose.taxes": {"facts": {"code": "String", "rate": RATE}, "also": ["tax_policy_draft.taxes"]},
    "tax_policy_draft.taxes": {"facts": {"code": "String", "rate": RATE}},
    "document_share": {"facts": {"kind": "String", "pages": {"type": "Long", "min": 0, "max": 10_000}}},
    "codex_revert": {"facts": {}},
}  # fmt: skip

ES_REGISTER = """from realms.legislation import Ref, above, fixed

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
ES_PROCEDURE = """from realms.legislation import above, overridable, working_days

class Procedure(above("procedure")):
    comment_period = overridable(working_days(30), legislation="Ley 7/1985 Art. 49", check=lambda v: v >= working_days(30))
"""


def write(tree: Path, path: str, text) -> None:
    file = tree / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(text) if isinstance(text, (dict, list)) else text)


def source_json(source: str) -> dict:
    """The source.json of §3.1 for ``source``."""
    return {
        "id": source,
        "name": {"en": source},
        "created_by": "an act",
        "publisher": {"realm": None},
    }


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """eu → eu/es → eu/es/vc/46250-valencia, with a fixed GGG operation list."""
    monkeypatch.setattr(
        legislation,
        "ggg_operations",
        lambda: frozenset(
            {
                "user_add",
                "notification_send",
                "realm_join",
                "codex_activate",
                "codex_revert",
                "tax_assess",
                "tax_policy_propose",
                "tax_policy_draft",
                "document_share",
            }
        )
        | legislation.INTERIM_OPERATIONS,
    )
    monkeypatch.setattr(legislation, "fact_schema", lambda: FACTS)
    root = tmp_path / "legislation"
    for source in ("eu", "eu/es", "eu/es/vc", "eu/es/vc/46250-valencia"):
        write(root, source + "/source.json", source_json(source))
    write(
        root,
        "eu/es/calendar.json",
        {
            "weekend": ["sat", "sun"],
            "years": {"2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]},
            "legislation": "Ley 39/2015 Art. 30.7",
        },
    )
    write(root, "eu/es/register.py", ES_REGISTER)
    write(root, "eu/es/procedure.py", ES_PROCEDURE)
    return root


def run(*args):
    return CliRunner().invoke(legislation_app, ["check", *args])


def test_a_tree_that_loads_is_ok(tree):
    out = run(str(tree))
    assert out.exit_code == 0, out.output
    assert "ok: 4 realm(s) checked" in out.output


def test_a_lower_layer_overriding_fixed_legislation_is_refused_as_the_doc_shows(tree):
    write(tree, "eu/es/vc/46250-valencia/register.py", VALENCIA_REGISTER)
    out = run(str(tree))
    assert out.exit_code == 1
    assert (
        "eu/es/vc/46250-valencia/register.py:3: on_user_add is fixed by eu/es/register.py (Ley 7/1985 Art. 17.3)"
        in out.output
    )
    assert "1 refused" in out.output
    assert run(str(tree), "--realm", "eu/es").exit_code == 0


def test_a_settings_check_runs_on_the_winning_value(tree):
    write(
        tree,
        "eu/es/vc/46250-valencia/procedure.py",
        "from realms.legislation import above, days\nclass Procedure(above('procedure')):\n    comment_period = days(45)\n",
    )
    out = run(str(tree), "--realm", "eu/es/vc/46250-valencia")
    assert out.exit_code == 1
    assert (
        "comment_period = days(45) fails the check of eu/es/procedure.py (Ley 7/1985 Art. 49)"
        in out.output
    )


def test_a_hook_must_name_a_ggg_operation(tree):
    write(
        tree,
        "eu/es/vc/tax.py",
        "from realms.legislation import above, fixed\nclass Tax(above('tax')):\n"
        "    @fixed(legislation='x')\n    def on_user_delete(self, event): pass\n",
    )
    out = run(str(tree), "--realm", "eu/es/vc")
    assert out.exit_code == 1
    assert (
        "eu/es/vc/tax.py:3: on_user_delete must name a GGG operation" in out.output
    )  # the decorator's line


def test_the_realm_must_be_a_source_and_the_tree_a_folder(tree):
    assert run(str(tree), "--realm", "fr").exit_code == 2
    assert run(str(tree / "nowhere")).exit_code == 2


def test_the_realm_s_own_chapter_may_hook_codex_activate(tree):
    write(
        tree,
        "eu/es/vc/46250-valencia/realm.py",
        "from realms.legislation import above\nclass Realm(above('realm')):\n"
        "    def on_codex_activate(self, event):\n        self.ggg.notification_send(recipient='members', body='active')\n"
        "    def compute_realm_join(self, user, quarters):\n        return quarters[0]\n",
    )
    out = run(str(tree), "--realm", "eu/es/vc/46250-valencia")
    assert out.exit_code == 0, out.output


def test_a_realm_loads_its_own_realm_chapter_and_not_the_one_a_source_above_keeps(tree):
    """Spain is a realm too, so it keeps a realm.py beside its releases; València, which runs Spain's chapters, does not
    load it (as the resolver skips a relayed realm chapter, §3)."""
    write(
        tree,
        "eu/es/realm.py",
        "from realms.legislation import above\nclass Realm(above('realm')):\n    x = 1.5\n",  # a floating point number: refused
    )
    assert run(str(tree), "--realm", "eu/es/vc/46250-valencia").exit_code == 0
    out = run(str(tree), "--realm", "eu/es")
    assert out.exit_code == 1 and "eu/es/realm.py" in out.output


def test_the_ggg_operation_list_comes_from_the_canisters_interface_plus_the_interim_list():
    pytest.importorskip("basilisk")
    assert {
        "tax_policy_propose",
        "codex_install",
        "realm_join",
        "codex_activate",
        "procedure_define",
    } <= legislation.ggg_operations()


# ── Every rule of §12.1 that needs no schema: a chapter that breaks it is refused, and the same chapter kept is not ──

VALENCIA = "eu/es/vc/46250-valencia"


def tax_chapter(body="    pass\n", imports="above"):
    """Spain's tax chapter: the SDK names it imports, and the class body."""
    return (
        f"from realms.legislation import {imports}\n\nclass Tax(above('tax')):\n{body}"
    )


def refused_with(tree, text, expected, path="eu/es/tax.py", realm="eu/es"):
    write(tree, path, text)
    out = run(str(tree), "--realm", realm)
    assert out.exit_code == 1, out.output
    assert expected in out.output, out.output


def kept(tree, text, path="eu/es/tax.py", realm="eu/es"):
    write(tree, path, text)
    out = run(str(tree), "--realm", realm)
    assert out.exit_code == 0, out.output


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "from realms.legislation import above\nclass Taxes(above('tax')):\n    pass\n",
            "the class of eu/es/tax.py is named Tax",
        ),
        (
            "from realms.legislation import above\nclass Tax(above('procedure')):\n    pass\n",
            "above('procedure') in the tax chapter: a chapter extends above(\"tax\")",
        ),
        (
            "from realms.legislation import above\nclass Tax(above('tax'), object):\n    pass\n",
            "Tax extends above(...) and nothing else, with no decorator or keyword",
        ),
        (
            "from realms.legislation import above\nclass Tax(above('tax')):\n    pass\nclass Other(above('tax')):\n    pass\n",
            "one class per file, extending above(...)",
        ),
        (
            "from realms.legislation import above\nx = [i for i in range(3)]\nclass Tax(above('tax')):\n    pass\n",
            "a constant expression: literals, lists and dicts of them",
        ),
    ],
)
def test_a_chapter_is_one_class_named_after_its_domain_that_extends_above_its_domain(
    tree, text, expected
):
    refused_with(tree, text, expected)
    kept(tree, tax_chapter())


@pytest.mark.parametrize(
    "imports, expected",
    [
        ("import os\n", "nothing else at module level except imports from the SDK"),
        (
            "from os import path\n",
            "import os: a chapter imports only from realms.legislation and ggg",
        ),
        (
            "from realms.legislation import above, Codex\n",
            "from realms.legislation import Codex: a chapter imports only",
        ),
        (
            "from . import sibling\n",
            "a chapter imports only from realms.legislation and ggg",
        ),
    ],
)
def test_a_chapter_imports_only_the_sdk_and_ggg(tree, imports, expected):
    refused_with(tree, imports + "class Tax(above('tax')):\n    pass\n", expected)
    kept(tree, "from ggg import Member\n" + tax_chapter())


@pytest.mark.parametrize(
    "construct, expected",
    [
        ("exec('1')", "exec: not allowed in a chapter"),
        ("eval('1')", "eval: not allowed in a chapter"),
        ("compile('1', 'x', 'exec')", "compile: not allowed in a chapter"),
        ("globals()", "globals: not allowed in a chapter"),
        ("locals()", "locals: not allowed in a chapter"),
        ("setattr(self, 'a', 1)", "setattr: not allowed in a chapter"),
        ("open('x')", "open: not allowed in a chapter"),
        ("__import__('os')", "__import__: no dunder names in a chapter"),
        ("self.__class__", "__class__: no dunder names in a chapter"),
    ],
)
def test_a_chapter_holds_no_banned_construct(tree, construct, expected):
    method = f"    @fixed(legislation='x')\n    def helper(self):\n        return {construct}\n"
    refused_with(tree, tax_chapter(method, "above, fixed"), expected)
    kept(tree, tax_chapter(method.replace(construct, "len([1, 2])"), "above, fixed"))


@pytest.mark.parametrize(
    "member, expected",
    [
        (
            "    def helper(self, x=1):\n        return x\n",
            "a default argument is state shared by every call",
        ),
        (
            "    class Inner:\n        pass\n",
            "a chapter's class holds settings, forbids and methods, nothing else",
        ),
        (
            "    def helper(self, event):\n        other = [1]\n        other.a = 2\n",
            "never on self or anything else",
        ),
        ("    helper = lambda self, x=1: x\n", "a constant expression"),
    ],
)
def test_a_chapter_class_holds_settings_forbids_and_plain_methods(
    tree, member, expected
):
    refused_with(
        tree,
        tax_chapter(member.replace("def helper", "def helper")),
        expected,
        realm="eu/es",
    )
    kept(tree, tax_chapter("    def helper(self, x):\n        return x\n"))


def test_a_public_member_of_a_layer_above_the_realm_cites_its_provision(tree):
    cited = "    rate = overridable(1, legislation='Ley 7/1985 Art. 1')\n"
    uncited = "    rate = overridable(1)\n"
    refused_with(
        tree,
        tax_chapter(uncited, "above, overridable"),
        'rate needs legislation="...": eu/es is not the realm\'s own layer',
        realm=VALENCIA,
    )
    kept(tree, tax_chapter(cited, "above, overridable"), realm=VALENCIA)
    kept(
        tree, tax_chapter(uncited, "above, overridable"), realm="eu/es"
    )  # the realm's own layer may leave it out


def test_a_lower_layer_cannot_define_what_a_layer_above_made_fixed(tree):
    fixed_here = (
        "    @fixed(legislation='x')\n    def helper(self):\n        return 1\n"
    )
    below = "    def helper(self):\n        return 2\n"
    write(tree, "eu/es/tax.py", tax_chapter(fixed_here, "above, fixed"))
    refused_with(
        tree,
        tax_chapter(below),
        "helper is fixed by eu/es/tax.py (x)",
        path=f"{VALENCIA}/tax.py",
        realm=VALENCIA,
    )
    kept(
        tree,
        tax_chapter("    def other(self):\n        return 2\n"),
        path=f"{VALENCIA}/tax.py",
        realm=VALENCIA,
    )


def test_an_extension_does_not_call_super(tree):
    ext = "    @extension(legislation='x')\n    def helper(self):\n        return {}\n"
    write(
        tree, "eu/es/tax.py", tax_chapter(ext.replace("{}", "[1]"), "above, extension")
    )
    refused_with(
        tree,
        tax_chapter(ext.replace("{}", "super().helper()"), "above, extension"),
        "helper is an extension: every layer's definition runs, so it does not call super()",
        path=f"{VALENCIA}/tax.py",
        realm=VALENCIA,
    )
    kept(
        tree,
        tax_chapter(ext.replace("{}", "[2]"), "above, extension"),
        path=f"{VALENCIA}/tax.py",
        realm=VALENCIA,
    )


@pytest.mark.parametrize(
    "method, expected",
    [
        (
            "on_nothing(self, event)",
            "on_nothing must name a GGG operation: 'nothing' is not one",
        ),
        (
            "check_nothing(self, args)",
            "check_nothing must name a GGG operation: 'nothing' is not one",
        ),
        (
            "compute_nothing(self)",
            "compute_nothing must name a GGG operation: 'nothing' is not one",
        ),
    ],
)
def test_a_hook_a_guard_and_a_compute_name_a_ggg_operation(tree, method, expected):
    body = f"    @fixed(legislation='x')\n    def {method}:\n        return None\n"
    refused_with(tree, tax_chapter(body, "above, fixed"), expected)
    kept(tree, tax_chapter(body.replace("nothing", "user_add"), "above, fixed"))


@pytest.mark.parametrize("prefix", ["on_", "check_", "compute_"])
@pytest.mark.parametrize("operation", ["codex_revert", "sync_source_remove"])
def test_a_chapter_does_not_hook_guard_or_compute_a_reserved_operation(
    tree, prefix, operation
):
    args = {"on_": "event", "check_": "args", "compute_": ""}[prefix]
    body = f"    @fixed(legislation='x')\n    def {prefix}{operation}(self, {args}):\n        return None\n"
    refused_with(
        tree,
        tax_chapter(body.replace(", )", ")"), "above, fixed"),
        f"{operation} is reserved; no chapter may hook, guard or forbid it",
    )
    kept(
        tree,
        tax_chapter(
            body.replace(operation, "user_add").replace(", )", ")"), "above, fixed"
        ),
    )


def test_domains_that_use_each_other_are_a_cycle(tree):
    write(
        tree,
        "eu/es/procedure.py",
        "from realms.legislation import above\n\nclass Procedure(above('procedure')):\n    uses = ['tax']\n",
    )
    refused_with(
        tree,
        tax_chapter("    uses = ['procedure']\n"),
        "uses cycle: procedure → tax → procedure (eu/es/procedure.py uses tax; eu/es/tax.py uses procedure)",
    )
    kept(tree, tax_chapter("    uses = []\n"))


@pytest.mark.parametrize(
    "uses, expected",
    [
        ("['tax']", "eu/es/tax.py: uses its own domain, tax"),
        ("['water']", "eu/es/tax.py: uses water, but this realm has no water chapter"),
    ],
)
def test_a_chapter_uses_another_domain_the_realm_has(tree, uses, expected):
    refused_with(tree, tax_chapter(f"    uses = {uses}\n"), expected)
    write(
        tree,
        "eu/es/procedure.py",
        "from realms.legislation import above\n\nclass Procedure(above('procedure')):\n    pass\n",
    )
    kept(tree, tax_chapter("    uses = ['procedure']\n"))


def test_a_refusal_in_a_layer_shows_once_though_every_realm_below_runs_the_layer(tree):
    write(
        tree,
        "eu/es/tax.py",
        tax_chapter("    def helper(self, x=1):\n        return x\n"),
    )
    out = run(str(tree))
    assert out.exit_code == 1
    assert out.output.count("a default argument is state shared by every call") == 1
    assert "1 refused" in out.output


# ── Test cases (§12.3): <domain>.cases.json beside a chapter, run on the realm's codex ──

RATE_AND_BONUS = """from realms.legislation import above, overridable, pct

class Tax(above('tax')):
    rate = overridable(pct("1.10"), legislation='TRLRHL Art. 72.3')

    @overridable(legislation='TRLRHL Art. 74')
    def bonus(self, owner):
        return 0
"""
VALENCIA_BONUS = """from realms.legislation import above, pct

class Tax(above('tax')):
    rate = pct("1.24")

    def bonus(self, owner):
        return 60 if owner == "User:alice" else 0
"""
GUARD = """from realms.legislation import above, extension

class LocalRegime(above('local_regime')):
    @extension(legislation='Ley 7/1985 Art. 65.2')
    def check_realm_join(self, args):
        if self.local_day(self.now()) < "2027-01-01":
            return self.deny("not before 2027")
        if args.code is None and not self.approved_by("Administration"):
            return self.deny("a code or the Administration")
"""


def with_cases(tree, path, cases, chapter=None):
    if chapter is not None:
        write(tree, path.replace(".cases.json", ".py"), chapter)
    write(tree, path, cases)


def case_run(tree, realm="eu/es"):
    out = run(str(tree), "--realm", realm)
    return out.exit_code, out.output


def test_a_call_case_reads_a_setting_or_calls_a_helper_of_the_resolved_domain(tree):
    write(tree, "eu/es/tax.py", RATE_AND_BONUS)
    write(tree, "eu/es/vc/46250-valencia/tax.py", VALENCIA_BONUS)
    cases = [
        {"name": "Spain's cap", "call": "rate", "returns": 1100000},
        {
            "name": "no bonus by default",
            "call": "bonus",
            "args": ["User:alice"],
            "returns": 0,
        },
        {
            "name": "València's cap",
            "realm": VALENCIA,
            "call": "rate",
            "returns": 1240000,
        },
        {
            "name": "València's bonus",
            "realm": VALENCIA,
            "call": "bonus",
            "args": ["User:alice"],
            "returns": 60,
        },
    ]
    write(tree, "eu/es/tax.cases.json", cases)
    assert case_run(tree, "eu/es")[0] == 0
    assert case_run(tree, VALENCIA)[0] == 0
    cases[0]["returns"], cases[3]["returns"] = 1200000, 90
    write(tree, "eu/es/tax.cases.json", cases)
    code, output = case_run(tree, "eu/es")
    assert (
        code == 1
        and 'eu/es/tax.cases.json: case "Spain\'s cap": returns 1100000, written 1200000'
        in output
    )
    assert "València's bonus" not in output  # it names València: it runs there
    code, output = case_run(tree, VALENCIA)
    assert (
        'case "València\'s bonus": returns 60, written 90' in output
        and "Spain's cap" not in output
    )


def test_a_case_that_calls_what_the_chapter_does_not_have_says_so(tree):
    write(tree, "eu/es/tax.py", RATE_AND_BONUS)
    write(
        tree, "eu/es/tax.cases.json", [{"name": "x", "call": "nothing", "returns": 0}]
    )
    code, output = case_run(tree)
    assert code == 1 and 'case "x": the tax chapter has no member nothing' in output


def test_a_guard_case_runs_the_domains_guard_on_the_arguments_the_time_and_the_approvals(
    tree,
):
    write(tree, "eu/es/local_regime.py", GUARD)
    before, after = 1_700_000_000, 1_800_000_000  # 14 Nov 2023 and 15 Jan 2027
    write(
        tree,
        "eu/es/local_regime.cases.json",
        [
            {
                "name": "before 2027",
                "guard": "realm_join",
                "now": before,
                "args": {"code": "x"},
                "denies": "not before 2027",
            },
            {
                "name": "needs a code",
                "guard": "realm_join",
                "now": after,
                "args": {"code": None},
                "denies": "a code or the Administration",
            },
            {
                "name": "approved",
                "guard": "realm_join",
                "now": after,
                "args": {"code": None},
                "approvals": ["Administration"],
                "denies": None,
            },
            {
                "name": "with a code",
                "guard": "realm_join",
                "now": after,
                "args": {"code": "x"},
                "denies": None,
            },
        ],
    )
    assert case_run(tree) == (0, "ok: 1 realm(s) checked\n")


def test_a_guard_case_that_does_not_come_out_as_written_is_a_problem(tree):
    write(tree, "eu/es/local_regime.py", GUARD)
    write(
        tree,
        "eu/es/local_regime.cases.json",
        [
            {
                "name": "wrongly allowed",
                "guard": "realm_join",
                "now": 0,
                "denies": None,
            },
            {"name": "no such guard", "guard": "realm_leave", "denies": None},
        ],
    )
    code, output = case_run(tree)
    assert code == 1
    assert "case \"wrongly allowed\": denies 'not before 2027', written None" in output
    assert (
        'case "no such guard": the local_regime chapter has no check_realm_leave'
        in output
    )


@pytest.mark.parametrize(
    "case, expected",
    [
        ({"call": "rate", "returns": 1}, "name is text"),
        (
            {"name": "x", "returns": 1},
            "one of call, guard, forbid, compute says what is tested",
        ),
        (
            {"name": "x", "call": "rate", "guard": "realm_join", "returns": 1},
            "one of call, guard, forbid, compute",
        ),
        ({"name": "x", "call": "rate"}, "returns is what the case expects"),
        ({"name": "x", "call": "rate", "returns": 1, "args": 1}, "args is a list"),
        (
            {"name": "x", "guard": "realm_join", "denies": 5},
            "denies is the reason, or null",
        ),
        (
            {"name": "x", "guard": "realm_join", "denies": None, "now": "soon"},
            "now is a time in seconds",
        ),
        (
            {
                "name": "x",
                "guard": "realm_join",
                "denies": None,
                "approvals": "Administration",
            },
            "approvals is a list",
        ),
        ({"name": "x", "forbid": "tax_assess", "fires": []}, "facts is an object"),
        (
            {"name": "x", "forbid": "tax_assess", "facts": {}, "fires": "all"},
            "fires is a list of forbid ids",
        ),
        (
            {"name": "x", "call": "rate", "returns": 1, "realm": "Eu/ES"},
            "realm is a source id",
        ),
    ],
)
def test_the_shape_of_a_cases_file_is_checked_with_the_layout(tree, case, expected):
    write(tree, "eu/es/tax.py", RATE_AND_BONUS)
    write(tree, "eu/es/tax.cases.json", [case])
    code, output = case_run(tree)
    assert code == 1 and "eu/es/tax.cases.json: case" in output and expected in output
    write(
        tree,
        "eu/es/tax.cases.json",
        [{"name": "x", "call": "rate", "returns": 1100000}],
    )
    assert case_run(tree)[0] == 0


def test_cases_belong_to_a_chapter_beside_them(tree):
    write(tree, "eu/es/tax.cases.json", [])
    code, output = case_run(tree)
    assert (
        code == 1
        and "eu/es/tax.cases.json: cases for tax, but there is no tax.py beside them"
        in output
    )
    write(tree, "eu/es/tax.py", RATE_AND_BONUS)
    assert case_run(tree)[0] == 0


# ── The F rules (§6) and the Cedar analysis of the forbids ──

FORBID_HEAD = "from realms.legislation import F, S, above, forbid, overridable, pct, when\n\nclass Tax(above('tax')):\n    capital = overridable(False, legislation='x')\n"
IBI_CAP = "    cap = forbid('tax_policy_propose', (F.code == 'ibi_urban') & F.rate.is_some() & (F.rate > pct('1.10') + when(S.capital, pct('0.07'))), each='taxes', legislation='x')\n"


def forbid_in(rule):
    return FORBID_HEAD + f"    cap = {rule}\n"


@pytest.mark.parametrize(
    "rule, expected",
    [
        (
            "forbid('nothing', F.x == 1, legislation='x')",
            "cap: the fact schema has no facts for nothing",
        ),
        (
            "forbid('user_add', F.x == 1, legislation='x')",
            "cap: the fact schema has no facts for user_add",
        ),
        (
            "forbid('tax_policy_propose', F.code == 'a', each='nope', legislation='x')",
            'each="nope" names no list of tax_policy_propose',
        ),
        (
            "forbid('tax_policy_propose', F.nope == 'a', each='taxes', legislation='x')",
            "F.nope: tax_policy_propose.taxes has no fact nope",
        ),
        (
            "forbid('tax_policy_propose', F.rate.is_some() & (F.rate > when(S.missing, 1, 0)), each='taxes', legislation='x')",
            "S.missing",
        ),
        (
            "forbid('tax_policy_propose', F.rate.is_some() & (F.rate > pct('0.1234567')), each='taxes', legislation='x')",
            "'0.1234567' has more than 6 decimals: it does not fit the scale of hundred-millionths",
        ),
        (
            "forbid('tax_policy_propose', F.rate > 5, each='taxes', legislation='x')",
            "F.rate is optional: compare it after F.rate.is_some() in the same & chain",
        ),
        (
            "forbid('tax_policy_propose', (F.code == 'a') and (F.rate > 5), each='taxes', legislation='x')",
            "use &, | and ~ in a forbid",
        ),
        (
            "forbid('document_share', F.pages + 4611686018427387904 + 4611686018427387904 > 1, legislation='x')",
            "may overflow Cedar's 64-bit integer even within the fact schema's ranges",
        ),
        (
            "forbid('codex_revert', F.x == 1, legislation='x')",
            "codex_revert is reserved; no chapter may hook, guard or forbid it",
        ),
    ],
)
def test_a_forbid_follows_the_f_rules(tree, rule, expected):
    refused_with(tree, forbid_in(rule), expected)
    write(tree, "eu/es/tax.py", FORBID_HEAD + IBI_CAP)
    assert run(str(tree), "--realm", "eu/es", "--no-analysis").exit_code == 0


def test_a_forbid_reads_the_settings_of_its_own_domain_only(tree):
    write(
        tree,
        "eu/es/procedure.py",
        "from realms.legislation import above, overridable\n\nclass Procedure(above('procedure')):\n    notice = overridable(3, legislation='x')\n",
    )
    rule = "forbid('tax_policy_propose', F.rate.is_some() & (F.rate > when(S.notice, 1, 0)), each='taxes', legislation='x')"
    refused_with(tree, forbid_in(rule), "S.notice")
    write(tree, "eu/es/tax.py", forbid_in(rule.replace("S.notice", "S.capital")))
    assert run(str(tree), "--realm", "eu/es", "--no-analysis").exit_code == 0


@pytest.fixture
def cedar_tools(monkeypatch):
    """The pinned Cedar CLI and cvc5 (scripts/cedar_tools.sh), when CEDAR_CLI and CVC5 point at them."""
    pytest.importorskip("cedarpy")
    cli, cvc5 = os.environ.get("CEDAR_CLI"), os.environ.get("CVC5")
    if not (cli and cvc5 and os.path.exists(cli) and os.path.exists(cvc5)):
        pytest.skip(
            "set CEDAR_CLI and CVC5 to the pinned Cedar CLI and cvc5 (scripts/cedar_tools.sh)"
        )
    return cli, cvc5


def test_check_proves_the_forbids_agree_with_python_and_never_error(tree, cedar_tools):
    write(tree, "eu/es/tax.py", FORBID_HEAD + IBI_CAP)
    out = run(str(tree), "--realm", "eu/es/vc/46250-valencia")
    assert out.exit_code == 0, out.output


def test_check_without_the_cedar_tools_says_what_to_install_and_never_passes_a_forbid_unproved(
    tree, monkeypatch
):
    pytest.importorskip("cedarpy")
    monkeypatch.delenv("CEDAR_CLI", raising=False)
    monkeypatch.delenv("CVC5", raising=False)
    write(tree, "eu/es/tax.py", FORBID_HEAD + IBI_CAP)
    out = run(str(tree), "--realm", "eu/es")
    assert (
        out.exit_code == 2
        and "scripts/cedar_tools.sh" in out.output
        and "--no-analysis" in out.output
    )
    assert run(str(tree), "--realm", "eu/es", "--no-analysis").exit_code == 0
    write(
        tree, "eu/es/tax.py", tax_chapter()
    )  # no forbid, nothing to prove: no tools needed
    assert run(str(tree), "--realm", "eu/es").exit_code == 0


# ── A compute case: compute_<op> run by the realm's sandbox program on records of GGG's entities ──

IBI_TAX = """from realms.legislation import above, fixed, overridable

class Tax(above('tax')):
    @fixed(legislation='TRLRHL Arts. 61, 65, 71 and 74')
    def compute_tax_assess(self, entry, subject):
        if entry.code != "ibi_urban" or subject.assessed_value is None:
            return []
        gross = subject.assessed_value.times(entry.rate)
        net = gross - gross.times(self.ibi_bonus(subject.owner))
        items = [{"concept": entry.code, "amount": gross, "legislation": "TRLRHL Art. 71.1"}]
        if net < gross:
            items.append({"concept": entry.code + "_bonus", "amount": net - gross, "legislation": "TRLRHL Art. 74"})
        return items

    @overridable(legislation='TRLRHL Art. 74')
    def ibi_bonus(self, owner):
        return 0
"""
VALENCIA_IBI = """from ggg import Attribute, Human
from realms.legislation import above, pct

class Tax(above('tax')):
    large_family_bonus = {"general": pct("60"), "special": pct("90")}

    def ibi_bonus(self, owner):
        kind, _, user = owner.partition(":")
        if kind != "User":
            return 0
        for human in Human.find("user", user):
            for attribute in Attribute.find("human", human.id):
                if attribute.kind == "large_family" and attribute.status == "verified":
                    return self.large_family_bonus.get(attribute.value, 0)
        return 0
"""
IBI = {
    "code": "ibi_urban",
    "kind": "tax",
    "subject": "Land",
    "rate": 1_240_000,
    "facts": {},
}
RECORDS = {
    "Land": [
        {"id": "L-1", "owner": "User:alice", "assessed_value": 20_000_000},
        {"id": "L-2", "owner": "User:bob", "assessed_value": 20_000_000},
        {"id": "L-3", "owner": "User:alice"},
    ],
    "Human": [{"id": "h-alice", "user": "alice"}, {"id": "h-bob", "user": "bob"}],
    "Attribute": [
        {
            "id": "a-alice",
            "human": "h-alice",
            "kind": "large_family",
            "value": "general",
            "status": "verified",
        },
        {
            "id": "a-bob",
            "human": "h-bob",
            "kind": "large_family",
            "value": "general",
            "status": "pending",
        },
    ],
}


def ibi_case(subject, returns, **more):
    return {
        "name": f"the IBI of {subject}",
        "realm": VALENCIA,
        "compute": "tax_assess",
        "args": {"entry": IBI, "subject": subject},
        "decimals": 2,
        "records": RECORDS,
        "returns": returns,
        **more,
    }


def test_a_compute_case_assesses_a_parcel_from_the_records(tree):
    write(tree, "eu/es/tax.py", IBI_TAX)
    write(tree, f"{VALENCIA}/tax.py", VALENCIA_IBI)
    gross = {
        "concept": "ibi_urban",
        "amount": 248_000,
        "legislation": "TRLRHL Art. 71.1",
    }  # 1.24 % of 200 000.00
    write(
        tree,
        "eu/es/tax.cases.json",
        [
            ibi_case(
                "Land:L-1",
                [
                    gross,
                    {
                        "concept": "ibi_urban_bonus",
                        "amount": -148_800,
                        "legislation": "TRLRHL Art. 74",
                    },
                ],
            ),
            ibi_case("Land:L-2", [gross]),  # a title not yet verified is none
            ibi_case("Land:L-3", []),  # never assessed: no IBI yet
        ],
    )
    assert case_run(tree, VALENCIA) == (0, "ok: 1 realm(s) checked\n")
    write(tree, "eu/es/tax.cases.json", [ibi_case("Land:L-2", [])])
    code, output = case_run(tree, VALENCIA)
    assert (
        code == 1
        and 'case "the IBI of Land:L-2": returns [{' in output
        and "written []" in output
    )


def test_a_compute_case_that_raises_or_has_no_compute_says_so(tree):
    write(tree, "eu/es/tax.py", IBI_TAX)
    write(tree, f"{VALENCIA}/tax.py", VALENCIA_IBI)
    missing = ibi_case("Land:L-9", [])
    write(
        tree,
        "eu/es/tax.cases.json",
        [missing, ibi_case("Land:L-1", [], compute="tax_declare")],
    )
    code, output = case_run(tree, VALENCIA)
    assert code == 1
    assert "raised: 'Land:L-9' is not found" in output
    assert "no chapter has compute_tax_declare" in output


@pytest.mark.parametrize(
    "change, expected",
    [
        ({"args": "L-1"}, "args is the arguments of the compute_ method"),
        ({"decimals": -1}, "decimals is the token's decimals"),
        ({"decimals": 2.5}, "decimals is the token's decimals"),
        ({"records": [{"id": 1}]}, "records is {Entity: [row, ...]}"),
        (
            {"records": {"Land": [{"owner": "User:alice"}]}},
            "each row an object with an id",
        ),
    ],
)
def test_the_shape_of_a_compute_case_is_checked_with_the_layout(tree, change, expected):
    write(tree, "eu/es/tax.py", IBI_TAX)
    write(tree, "eu/es/tax.cases.json", [ibi_case("Land:L-1", [], **change)])
    code, output = case_run(tree, VALENCIA)
    assert code == 1 and expected in output


def test_the_money_fields_a_case_reads_are_the_realms():
    """The realm reads a parcel's assessed_value as Money (core.legislation.MONEY); cases read the same."""
    import ast

    from realms.cli.commands.legislation_cases import MONEY

    source = (
        Path(__file__).resolve().parents[2] / "src/realm_backend/core/legislation.py"
    ).read_text()
    realms = next(
        ast.literal_eval(node.value)
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "MONEY"
    )
    assert MONEY == realms


# ── A forbid case (§12.1), and a case that could never run ──

CAP_CHAPTER = """from realms.legislation import F, S, above, forbid, overridable, pct, when

class Tax(above('tax')):
    capital = overridable(False, legislation='TRLRHL Art. 72.3')
    surface_public_transport = overridable(False, legislation='TRLRHL Art. 72.3')

    ibi_urban_cap = forbid('tax_policy_propose',
        (F.code == 'ibi_urban') & F.rate.is_some() & (F.rate > pct('1.10')
            + when(S.capital, pct('0.07')) + when(S.surface_public_transport, pct('0.07'))),
        each='taxes', legislation='TRLRHL Art. 72.3')
"""
VALENCIA_CAP = "from realms.legislation import above\n\nclass Tax(above('tax')):\n    capital = True\n    surface_public_transport = True\n"


def cap_case(rate, fires, **more):
    return {
        "name": f"{rate}",
        "realm": VALENCIA,
        "forbid": "tax_policy_propose",
        "each": "taxes",
        "facts": {"code": "ibi_urban", "rate": rate},
        "fires": fires,
        **more,
    }


def test_a_forbid_case_names_the_forbids_that_fire_on_the_facts_in_the_realm(tree):
    write(tree, "eu/es/tax.py", CAP_CHAPTER)
    write(tree, f"{VALENCIA}/tax.py", VALENCIA_CAP)
    cap = ["eu/es/tax/ibi_urban_cap"]
    write(
        tree,
        "eu/es/tax.cases.json",
        [cap_case(1_240_000, []), cap_case(1_240_001, cap)],
    )  # §12.1's example
    assert case_run(tree, VALENCIA) == (0, "ok: 1 realm(s) checked\n")
    # in Spain itself, which has neither the capital nor the transport, 1.10 % is the cap
    write(
        tree,
        "eu/es/tax.cases.json",
        [
            cap_case(1_100_000, [], realm="eu/es"),
            cap_case(1_100_001, cap, realm="eu/es"),
        ],
    )
    assert case_run(tree, "eu/es")[0] == 0


def test_a_forbid_case_that_fires_otherwise_than_written_is_reported(tree):
    write(tree, "eu/es/tax.py", CAP_CHAPTER)
    write(tree, f"{VALENCIA}/tax.py", VALENCIA_CAP)
    write(tree, "eu/es/tax.cases.json", [cap_case(1_240_001, [])])
    code, output = case_run(tree, VALENCIA)
    assert code == 1
    assert "case \"1240001\": fires ['eu/es/tax/ibi_urban_cap'], written []" in output


@pytest.mark.parametrize(
    "case, where, expected",
    [
        (
            cap_case(1, [], realm="eu/es/vc/46250-valencai"),
            "eu/es",
            "realm eu/es/vc/46250-valencai is not a source at or below eu/es",
        ),
        (
            cap_case(1, [], realm="eu/es"),
            "eu/es/vc",
            "realm eu/es is not a source at or below eu/es/vc",
        ),
        (
            cap_case(1, [], realm="eu/fr"),
            "eu/es",
            "realm eu/fr is not a source at or below eu/es",
        ),
    ],
)
def test_a_case_whose_realm_could_not_run_it_is_refused(tree, case, where, expected):
    write(tree, f"{where}/tax.py", CAP_CHAPTER)
    write(tree, f"{where}/tax.cases.json", [case])
    code, output = case_run(tree, where)
    assert code == 1 and f"{where}/tax.cases.json: case '1': {expected}" in output
    write(tree, f"{where}/tax.cases.json", [{**case, "realm": where}])
    assert case_run(tree, where)[0] == 0


@pytest.mark.parametrize(
    "change, expected",
    [
        (
            {"forbid": "tax_policy_propos"},
            "the fact schema has no tax_policy_propos.taxes",
        ),
        ({"each": "taxs"}, "the fact schema has no tax_policy_propose.taxs"),
    ],
)
def test_a_forbid_case_on_an_operation_or_list_the_fact_schema_lacks_is_refused(
    tree, change, expected
):
    write(tree, "eu/es/tax.py", CAP_CHAPTER)
    write(tree, "eu/es/tax.cases.json", [{**cap_case(1, [], realm="eu/es"), **change}])
    code, output = case_run(tree)
    assert code == 1 and expected in output
    write(tree, "eu/es/tax.cases.json", [cap_case(1, [], realm="eu/es")])
    assert case_run(tree)[0] == 0


def test_a_banned_built_in_is_refused_in_a_setting_too(tree):
    for setting in ("    rate = open('x')\n", "    rate = eval\n"):
        name = setting.split("=")[1].split("(")[0].strip()
        refused_with(tree, tax_chapter(setting), f"{name}: not allowed in a chapter")


def test_check_without_cedarpy_says_what_to_install(tree, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "cedarpy", None
    )  # an import of it fails, as where it is not installed
    write(tree, "eu/es/tax.py", FORBID_HEAD + IBI_CAP)
    out = run(str(tree), "--realm", "eu/es")
    assert out.exit_code == 2 and "pip install 'cedarpy==4.12.2'" in out.output
    assert run(str(tree), "--realm", "eu/es", "--no-analysis").exit_code == 0
