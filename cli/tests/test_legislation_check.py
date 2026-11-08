"""``realms legislation check``: a legislation/ tree loaded as the realm loads it, refusals printed as the checker does."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from realms.cli.commands import legislation
from realms.cli.commands.legislation import legislation_app

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
            }
        )
        | legislation.INTERIM_OPERATIONS,
    )
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
            "never on anything else",
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
