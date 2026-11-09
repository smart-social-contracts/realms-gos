"""``realms legislation diff`` (docs/codices/layered-codex.md §12.3): each section of the report, on a small tree in a
throw-away git repository. The questions are SymCC's, so these tests need the pinned Cedar CLI and cvc5
(``scripts/cedar_tools.sh``, ``CEDAR_CLI`` and ``CVC5``) and skip without them.
"""

import json
import os

import pytest
from typer.testing import CliRunner

from realms.cli.commands import legislation
from realms.cli.commands.legislation import legislation_app
from realms.cli.commands.legislation_diff import analyse_action
from realms.cli.commands.legislation_load import CommitFiles, load_realm
from realms.cli.commands.legislation_proof import Answer, Solver
from realms.cli.commands.legislation_report import Comparison

VALENCIA = "eu/es/vc/46250-valencia"
RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
TAX_ENTRY = {"code": "String", "rate": RATE}
FACTS = {
    "tax_policy_propose": {"facts": {"policy": "String"}},
    "tax_policy_propose.taxes": {
        "facts": TAX_ENTRY,
        "also": ["tax_policy_draft.taxes"],
    },
    "tax_policy_draft.taxes": {"facts": TAX_ENTRY},
    "document_share": {"facts": {"kind": "String"}},
}
OPERATIONS = frozenset(
    {
        "tax_policy_propose",
        "tax_policy_draft",
        "document_share",
        "tax_assess",
        "proposal_create",
    }
)


def tax(cap="1.10", extra=""):
    """Spain's tax chapter: the urban cap, which a capital raises by 0.07 %, and ``extra`` members."""
    return f"""from realms.legislation import F, S, above, forbid, overridable, pct, when

class Tax(above('tax')):
    capital = overridable(False, legislation='TRLRHL Art. 72.3')

    ibi_urban_cap = forbid('tax_policy_propose',
        (F.code == 'ibi_urban') & F.rate.is_some() & (F.rate > pct('{cap}') + when(S.capital, pct('0.07'))),
        each='taxes', legislation='TRLRHL Art. 72.3')
{extra}"""


def rustic(cap):
    return f"""
    ibi_rustic_cap = forbid('tax_policy_propose',
        (F.code == 'ibi_rustic') & F.rate.is_some() & (F.rate > pct('{cap}')),
        each='taxes', legislation='TRLRHL Art. 72.3')
"""


VALENCIA_TAX = "from realms.legislation import above\n\nclass Tax(above('tax')):\n    capital = True\n"
CALLS_TAX = """from realms.legislation import above

class LocalRegime(above('local_regime')):
    uses = ['tax']
"""
CASES = [{"name": "the cap", "call": "capital", "returns": False}]


def source_json(source):
    return {"id": source, "name": {"en": source}, "publisher": {"realm": None}}


@pytest.fixture
def cedar_tools():
    cli, cvc5 = os.environ.get("CEDAR_CLI"), os.environ.get("CVC5")
    if not (cli and cvc5 and os.path.exists(cli) and os.path.exists(cvc5)):
        pytest.skip(
            "set CEDAR_CLI and CVC5 to the pinned Cedar CLI and cvc5 (scripts/cedar_tools.sh)"
        )
    return cli, cvc5


@pytest.fixture
def world(tmp_path, git, monkeypatch, cedar_tools):
    """A tree of eu, eu/es and València in a git repository, and ``world.change(files)``: the files written and
    committed, the commit returned. The first commit is the base."""
    monkeypatch.setattr(legislation, "ggg_operations", lambda: OPERATIONS)
    monkeypatch.setattr(legislation, "fact_schema", lambda: FACTS)
    git(tmp_path, "init", "-q")
    tree = tmp_path / "legislation"

    class World:
        def __init__(self):
            self.tree, self.tools = tree, cedar_tools

        def change(self, files):
            for path, text in files.items():
                (tree / path).parent.mkdir(parents=True, exist_ok=True)
                (tree / path).write_text(
                    json.dumps(text) if isinstance(text, (dict, list)) else text
                )
            git(tmp_path, "add", "-A")
            git(tmp_path, "commit", "-q", "--no-verify", "-m", "change")
            return git(tmp_path, "rev-parse", "HEAD")

        def diff(self, base, head, *args):
            return CliRunner().invoke(
                legislation_app,
                ["diff", str(tree), "--base", base, "--head", head, *args],
            )

        def realm(self, base, head, realm):
            ggg = legislation.check_view()
            return Comparison(
                tree, base, head, OPERATIONS, FACTS, ggg, cedar_tools
            ).realm(realm)

    world = World()
    world.base = world.change(
        {
            **{f"{s}/source.json": source_json(s) for s in ("eu", "eu/es", VALENCIA)},
            "eu/es/tax.py": tax(extra=rustic("0.90")),
            f"{VALENCIA}/tax.py": VALENCIA_TAX,
        }
    )
    return world


# ── Newly forbidden and newly allowed (point 2) ──────────────────────────────────────────────────────────────────


def test_a_lowered_cap_is_newly_forbidden_as_a_range_proved_equal_to_the_difference(
    world,
):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    spain = world.realm(world.base, head, "eu/es")
    [result] = [r for r in spain.folded if r.action == "tax_policy_propose.taxes"]
    assert result.statements == [
        'code = "ibi_urban": rate more than 1.00 % (1000000) and at most 1.10 % (1100000) newly forbidden'
    ]
    assert result.forbidden.proved is False and result.allowed.proved is True
    valencia = world.realm(world.base, head, VALENCIA)
    [result] = [r for r in valencia.folded if r.action == "tax_policy_propose.taxes"]
    assert result.statements == [
        'code = "ibi_urban": rate more than 1.07 % (1070000) and at most 1.17 % (1170000) newly forbidden'
    ]


def test_a_raised_cap_is_newly_allowed(world):
    head = world.change({"eu/es/tax.py": tax("1.20", rustic("0.90"))})
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").folded
        if r.action == "tax_policy_propose.taxes"
    ]
    assert result.statements == [
        'code = "ibi_urban": rate more than 1.10 % (1100000) and at most 1.20 % (1200000) newly allowed'
    ]
    assert result.forbidden.proved is True and result.allowed.proved is False


def test_a_change_that_forbids_some_requests_and_allows_others_says_both(world):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("1.00"))})
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").folded
        if r.action == "tax_policy_propose.taxes"
    ]
    assert sorted(result.statements) == [
        'code = "ibi_rustic": rate more than 0.90 % (900000) and at most 1.00 % (1000000) newly allowed',
        'code = "ibi_urban": rate more than 1.00 % (1000000) and at most 1.10 % (1100000) newly forbidden',
    ]


def test_a_change_of_a_source_is_also_asked_with_its_settings_left_open(world):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").open
        if r.action == "tax_policy_propose.taxes"
    ]
    assert sorted(result.statements) == [
        'code = "ibi_urban", capital: rate more than 1.07 % (1070000) and at most 1.17 % (1170000) newly forbidden',
        'code = "ibi_urban", not capital: rate more than 1.00 % (1000000) and at most 1.10 % (1100000) newly forbidden',
    ]
    assert (
        world.realm(world.base, head, VALENCIA).open == []
    )  # València did not change: its settings are its own


def test_a_change_that_changes_nothing_a_forbid_decides_says_none(world):
    head = world.change(
        {"eu/es/tax.py": tax("1.10", rustic("0.90")) + "\n# a comment\n"}
    )
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").folded
        if r.action == "tax_policy_propose.taxes"
    ]
    assert result.forbidden.proved and result.allowed.proved and result.statements == []


def test_a_difference_that_is_no_range_of_a_number_is_given_as_the_request_alone(world):
    secret = (
        "\n    secret = forbid('document_share', F.kind == 'secret', legislation='x')\n"
    )
    head = world.change({"eu/es/tax.py": tax("1.10", rustic("0.90") + secret)})
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").folded
        if r.action == "document_share"
    ]
    assert (
        result.forbidden.proved is False
        and result.forbidden.example["facts"]["kind"] == "secret"
    )
    assert result.statements == []
    report = world.diff(world.base, head).output
    assert "- newly forbidden: yes, e.g. `{'facts': {'kind': 'secret'}" in report


# ── The questions with one answer (points 3 and 4) ───────────────────────────────────────────────────────────────


def dead_extra():
    return """
    everything = forbid('tax_policy_propose', F.code == 'ibi_urban', each='taxes', legislation='x')
    the_rest = forbid('tax_policy_propose', ~(F.code == 'ibi_urban'), each='taxes', legislation='x')
"""


def test_an_operation_that_nothing_can_pass_is_dead_and_fails(world):
    head = world.change({"eu/es/tax.py": tax("1.10", dead_extra())})
    spain = world.realm(world.base, head, "eu/es")
    [result] = [r for r in spain.folded if r.action == "tax_policy_propose.taxes"]
    assert result.dead.proved
    assert any("nothing is permitted" in f for f in spain.failures())
    report = world.diff(world.base, head)
    assert report.exit_code == 1 and "nothing permitted: YES" in report.output


def test_an_operation_with_requests_that_pass_is_not_dead(world):
    [result] = [
        r
        for r in world.realm(world.base, world.base, "eu/es").folded
        if r.action == "tax_policy_propose.taxes"
    ]
    assert result.dead.proved is False


def test_a_forbid_that_never_fires_fails(world):
    never = "\n    never = forbid('tax_policy_propose', (F.code == 'a') & (F.code == 'b'), each='taxes', legislation='x')\n"
    head = world.change({"eu/es/tax.py": tax("1.10", rustic("0.90") + never)})
    spain = world.realm(world.base, head, "eu/es")
    assert any("eu/es/tax/never never fires" in f for f in spain.failures())
    assert world.diff(world.base, head).exit_code == 1


def test_a_forbid_the_others_already_cover_is_an_overlap_and_only_a_warning(world):
    covered = "\n    over_two = forbid('tax_policy_propose', (F.code == 'ibi_urban') & F.rate.is_some() & (F.rate > pct('2.00')), each='taxes', legislation='x')\n"
    head = world.change({"eu/es/tax.py": tax("1.10", rustic("0.90") + covered)})
    spain = world.realm(world.base, head, "eu/es")
    assert spain.failures() == []
    assert spain.warnings() == [
        "eu/es: tax_policy_propose.taxes: eu/es/tax/over_two is covered by the other forbids"
    ]
    report = world.diff(world.base, head)
    assert (
        report.exit_code == 0
        and "overlap: `eu/es/tax/over_two` is covered by the other forbids"
        in report.output
    )


# ── A forbid that can error, and a query the solver does not decide ──────────────────────────────────────────────


class Rewritten:
    """A codex or forbid whose Cedar is ``rewrite`` of what it generated: a forbid the generator would not write."""

    def __init__(self, inner, rewrite):
        self.inner, self.rewrite = inner, rewrite

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def cedar(self, generic=False):
        return self.rewrite(self.inner.cedar(generic))

    def forbids(self):
        return [
            Rewritten(f, self.rewrite) if hasattr(f, "cedar") else f
            for f in self.inner.forbids()
        ]


def loaded(world, commit, realm):
    return load_realm(
        CommitFiles(world.tree, commit),
        realm,
        OPERATIONS,
        FACTS,
        legislation.check_view(),
    )[0]


def test_a_forbid_that_can_error_fails_with_the_request_that_makes_it(world):
    codex = loaded(world, world.base, "eu/es")
    overflowing = Rewritten(
        codex,
        lambda cedar: cedar.replace(
            "context.facts.rate >", "context.facts.rate + 9223372036854775807 >"
        ),
    )
    solver = Solver(*world.tools, codex.cedar_schema(context={"op": "String"}))
    result = analyse_action(
        solver, "tax_policy_propose.taxes", codex, overflowing, False, {}
    )
    assert len(result.can_error) == 2 and all(
        "can error on tax_policy_propose.taxes" in e for e in result.can_error
    )
    assert result.failures() == result.can_error


def test_a_forbid_that_cannot_error_is_not_reported(world):
    codex = loaded(world, world.base, "eu/es")
    solver = Solver(*world.tools, codex.cedar_schema(context={"op": "String"}))
    assert (
        analyse_action(
            solver, "tax_policy_propose.taxes", codex, codex, False, {}
        ).can_error
        == []
    )


def test_a_query_the_solver_does_not_decide_is_a_failure_and_never_a_pass(world):
    codex = loaded(world, world.base, "eu/es")

    class Undecided:
        def __getattr__(self, name):
            return lambda *args: Answer(None, None, "no answer in 0 s")

    result = analyse_action(
        Undecided(), "tax_policy_propose.taxes", codex, codex, False, {}
    )
    assert result.forbidden.proved is None and result.undecided
    assert any("undecided: nothing permitted" in f for f in result.failures())
    assert not result.nothing_to_say()


def test_a_solver_that_times_out_fails_the_report(world, monkeypatch):
    from realms.cli.commands import legislation_proof

    monkeypatch.setattr(legislation_proof, "SYMCC_TIMEOUT", 0.001)
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    report = world.diff(world.base, head)
    assert report.exit_code == 1 and "undecided" in report.output


# ── The rest of the report (points 1, 5, 6 and 7) ────────────────────────────────────────────────────────────────


def test_the_report_has_the_generated_cedar_diff_and_the_flattened_view_before_and_after(
    world,
):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    spain = world.realm(world.base, head, "eu/es")
    assert "-         && context.facts.rate > 1100000" in "\n".join(
        spain.cedar
    ).replace("-         ", "-         ")
    assert any(
        "rate > 1000000" in line and line.startswith("+") for line in spain.cedar
    )
    before, after = spain.views["tax"]
    assert [line for line in before if "ibi_urban_cap" in line] and [
        line for line in after if "ibi_urban_cap" in line
    ]
    assert not [line for line in before if "maximum" in line]
    assert [line.strip() for line in after if "maximum" in line] == [
        "for eu/es: maximum 1.00 % (1000000)",
        "for eu/es: maximum 0.90 % (900000)",
    ]


def test_the_folded_line_names_the_settings_that_raise_the_cap(world):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    _, after = world.realm(world.base, head, VALENCIA).views["tax"]
    assert [line.strip() for line in after if "maximum" in line] == [
        f"for {VALENCIA}: capital → maximum 1.07 % (1070000)",
        f"for {VALENCIA}: maximum 0.90 % (900000)",
    ]


def test_a_domain_that_calls_a_changed_one_is_reached_through_uses(world):
    first = world.change({"eu/es/local_regime.py": CALLS_TAX})
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    spain = world.realm(first, head, "eu/es")
    assert spain.domains == ["local_regime", "tax"]
    report = world.diff(first, head).output
    assert (
        "Changed: `eu/es/tax`" in report
        and "| `eu/es` | `local_regime`, `tax` |" in report
    )


def test_only_the_realms_a_changed_source_is_a_layer_of_are_reached(world):
    head = world.change({f"{VALENCIA}/tax.py": VALENCIA_TAX.replace("True", "False")})
    report = world.diff(world.base, head).output
    assert f"| `{VALENCIA}` | `tax` |" in report and "| `eu/es` |" not in report


def test_the_realms_test_cases_run_and_a_failing_one_fails_the_report(world):
    first = world.change({"eu/es/tax.cases.json": CASES})
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    assert world.realm(first, head, "eu/es").cases == [
        ('eu/es/tax.cases.json: case "the cap"', None)
    ]
    failing = world.change({"eu/es/tax.cases.json": [{**CASES[0], "returns": True}]})
    spain = world.realm(first, failing, "eu/es")
    assert [why for _, why in spain.cases] == ["returns False, written True"]
    assert world.diff(first, failing).exit_code == 1


def test_a_codex_that_does_not_load_fails_with_the_refusal(world):
    head = world.change({"eu/es/tax.py": "import os\n" + tax()})
    spain = world.realm(world.base, head, "eu/es")
    assert (
        spain.problems and "does not load" in spain.problems[0] and spain.folded == []
    )
    assert world.diff(world.base, head).exit_code == 1


def test_a_realm_added_by_the_change_has_everything_it_forbids_newly_forbidden(world):
    head = world.change(
        {
            "eu/fr/source.json": source_json("eu/fr"),
            "eu/fr/tax.py": tax("1.00", rustic("0.90")),
        }
    )
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/fr").folded
        if r.action == "tax_policy_propose.taxes"
    ]
    assert result.forbidden.proved is False and result.allowed.proved is True


# ── The command ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_clean_change_exits_0_and_a_report_names_the_commits(world):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    report = world.diff(world.base, head)
    assert report.exit_code == 0, report.output
    assert report.output.startswith("<!-- realms-legislation-diff -->\n")
    assert f"`{world.base[:7]}` → `{head[:7]}`" in report.output
    assert "**No failure**, 2 realm(s) reached" in report.output


def test_the_report_goes_to_a_file_with_out(world, tmp_path):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    out = tmp_path / "report.md"
    assert world.diff(world.base, head, "--out", str(out)).exit_code == 0
    assert out.read_text().startswith("<!-- realms-legislation-diff -->")


def test_a_change_list_gives_the_realm_and_the_commit_it_comes_from(world, tmp_path):
    head = world.change(
        {
            "eu/es/tax.py": tax("1.00", rustic("0.90")),
            f"{VALENCIA}/tax.py": VALENCIA_TAX.replace("True", "False"),
        }
    )
    url = f"https://raw.githubusercontent.com/smart-social-contracts/realms-codices/{head}/legislation/{VALENCIA}/tax.py"
    listing = tmp_path / "change.json"
    listing.write_text(
        json.dumps(
            {"realm": VALENCIA, "set": [{"chapter": f"{VALENCIA}/tax", "url": url}]}
        )
    )
    other = world.change(
        {"eu/es/tax.py": tax("1.30", rustic("0.90"))}
    )  # a later commit: the change list's is the head
    out = CliRunner().invoke(
        legislation_app,
        [
            "diff",
            str(world.tree),
            "--base",
            world.base,
            "--head",
            other,
            "--change-list",
            str(listing),
        ],
    )
    assert out.exit_code == 0, out.output
    assert (
        f"`{world.base[:7]}` → `{head[:7]}`" in out.output
        and "| `eu/es` |" not in out.output
    )


def test_a_change_list_whose_chapters_come_from_two_commits_is_refused(world, tmp_path):
    urls = [f"https://example.org/{c * 40}/legislation/eu/es/tax.py" for c in "ab"]
    listing = tmp_path / "change.json"
    listing.write_text(
        json.dumps(
            {
                "realm": "eu/es",
                "set": [{"chapter": "eu/es/tax", "url": u} for u in urls],
            }
        )
    )
    out = CliRunner().invoke(
        legislation_app,
        ["diff", str(world.tree), "--base", world.base, "--change-list", str(listing)],
    )
    assert out.exit_code == 2 and "come from 2 commits" in out.output


def test_without_the_cedar_tools_it_says_what_to_install(world, monkeypatch):
    monkeypatch.delenv("CEDAR_CLI")
    monkeypatch.delenv("CVC5")
    out = world.diff(world.base, world.base)
    assert out.exit_code == 2 and "scripts/cedar_tools.sh" in out.output


def test_a_commit_git_does_not_have_is_refused(world):
    out = world.diff("f" * 40, world.base)
    assert out.exit_code == 2


def test_the_realm_option_narrows_the_realms(world):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    out = world.diff(world.base, head, "--realm", VALENCIA).output
    assert f"### `{VALENCIA}`" in out and "### `eu/es`" not in out


def test_the_pinned_tools_version(cedar_tools):
    import subprocess

    assert (
        "4.14.1"
        in subprocess.run(
            [cedar_tools[0], "--version"], capture_output=True, text=True
        ).stdout
    )


# ── The readable form needs no solver ────────────────────────────────────────────────────────────────────────────


def test_regions_are_the_runs_where_a_decision_that_changes_only_at_the_constants_holds():
    from realms.cli.commands.legislation_ranges import regions

    decide = lambda n: 1_100_000 < n <= 1_240_000 or n == 5  # noqa: E731
    assert regions(decide, [1_100_000, 1_240_000, 5], 0, 10_000_000) == [
        (5, 5),
        (1_100_001, 1_240_000),
    ]
    assert regions(lambda n: n > 3, [3], 0, 10) == [(4, 10)]
    assert regions(lambda n: False, [3], 0, 10) == []


@pytest.mark.parametrize(
    "first, last, bounds, words",
    [
        (
            1_000_001,
            1_100_000,
            (0, 100_000_000),
            "rate more than 1.00 % (1000000) and at most 1.10 % (1100000)",
        ),
        (0, 400_000, (0, 100_000_000), "rate at most 0.40 % (400000)"),
        (1_300_001, 100_000_000, (0, 100_000_000), "rate more than 1.30 % (1300000)"),
        (0, 100_000_000, (0, 100_000_000), "any rate"),
        (
            7,
            7,
            (0, 100_000_000),
            "rate is 0.000007 % (7)",
        ),
        (5, 9, (0, 10_000), "rate more than 4 and at most 9"),
    ],
)
def test_a_run_is_described_with_the_percentage_of_a_ratio(first, last, bounds, words):
    from realms.cli.commands.legislation_ranges import describe

    assert describe("rate", first, last, bounds) == words
