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
            {"user_add", "notification_send", "realm_join", "codex_activate"}
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
    write(
        tree,
        "eu/es/realm.py",
        "from realms.legislation import above\nclass Realm(above('realm')): pass\n",
    )
    out = run(str(tree), "--realm", "eu/es/vc/46250-valencia")
    assert (
        out.exit_code == 1
        and "eu/es/realm.py: the realm domain may only be filled in the realm's own layer"
        in out.output
    )


def test_the_ggg_operation_list_comes_from_the_canisters_interface_plus_the_interim_list():
    pytest.importorskip("basilisk")
    assert {
        "tax_policy_propose",
        "codex_install",
        "realm_join",
        "codex_activate",
        "procedure_define",
    } <= legislation.ggg_operations()
