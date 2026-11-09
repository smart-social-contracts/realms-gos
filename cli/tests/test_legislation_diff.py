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
from realms.cli.commands.legislation_proof import Answer, Solver, verdict_of
from realms.cli.commands.legislation_report import Comparison

VALENCIA = "eu/es/vc/46250-valencia"
RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
AMOUNT = {"type": "Long", "optional": True, "min": 0, "max": 1_000_000}
TAX_ENTRY = {"code": "String", "rate": RATE, "amount": AMOUNT}
KINDS = ["Fine", "Restitution", "CommunityService", "Suspension"]
PENALTY = {"type": "String", "values": KINDS}  # an enumeration of GGG
FACTS = {
    "tax_policy_propose": {"facts": {"policy": "String"}},
    "tax_policy_propose.taxes": {
        "facts": TAX_ENTRY,
        "also": ["tax_policy_draft.taxes"],
    },
    "tax_policy_draft.taxes": {"facts": TAX_ENTRY},
    "document_share": {"facts": {"kind": "String"}},
    "verdict_draft": {"facts": {"case": "String"}},
    "verdict_draft.penalties": {"facts": {"kind": PENALTY}},
    "loan_create": {
        "facts": {
            "term": "Period",
            "region": "String",
            "rate": {"type": "Long", "min": 0, "max": 100_000_000},
        }
    },
}
OPERATIONS = frozenset(
    {
        "tax_policy_propose",
        "tax_policy_draft",
        "document_share",
        "loan_create",
        "verdict_draft",
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


@pytest.mark.parametrize("word", ["secret", "VERIFIED", "DOES NOT HOLD"])
def test_a_counterexample_that_carries_the_verdict_word_is_still_a_counterexample(
    world, word
):
    extra = (
        f"\n    named = forbid('document_share', F.kind == '{word}', legislation='x')\n"
    )
    head = world.change({"eu/es/tax.py": tax("1.10", rustic("0.90") + extra)})
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").folded
        if r.action == "document_share"
    ]
    assert result.forbidden.proved is False
    assert result.forbidden.example["facts"]["kind"] == word


def test_the_verdict_is_read_from_the_verdict_line_alone():
    held = "\u2713 Policy set 1 implies policy set 2: VERIFIED\n"
    broken = (
        "\u2717 Policy set 1 implies policy set 2: DOES NOT HOLD\n  Counterexample found:\n"
        'principal: Realms::User::"", action: Realms::Action::"a", resource: Realms::Realm::""\n'
        'context: {facts: {kind: "VERIFIED"}}\n'
    )
    assert verdict_of(held) is True
    assert verdict_of(broken) is False
    assert verdict_of('context: {facts: {kind: "VERIFIED"}}\n') is None
    assert verdict_of("error: cvc5 is missing\n") is None


def loans(long_cap, short_cap, extra=""):
    return f"""from realms.legislation import F, above, forbid, months, pct

class Finance(above('finance')):
    long_term_cap = forbid('loan_create', (F.term > months(12)) & (F.rate > pct('{long_cap}')), legislation='x')
    short_term_cap = forbid('loan_create', ~(F.term > months(12)) & (F.rate > pct('{short_cap}')), legislation='x')
{extra}"""


def loan_results_between(world, base, head):
    return [
        r for r in world.realm(base, head, "eu/es").folded if r.action == "loan_create"
    ]


def test_caps_that_swap_between_two_terms_state_no_range_of_the_rate(world):
    base = world.change({"eu/es/finance.py": loans("5.00", "3.00")})
    head = world.change({"eu/es/finance.py": loans("3.00", "5.00")})
    [result] = loan_results_between(world, base, head)
    assert result.forbidden.proved is False and result.allowed.proved is False
    assert (
        result.statements == []
    )  # a rate between 3 and 5 % is allowed for one term and forbidden for the other
    assert result.undecided == []
    report = world.diff(base, head).output
    assert (
        "newly forbidden: yes, e.g." in report and "newly allowed: yes, e.g." in report
    )


def rate_cap(cap, extra=""):
    return f"""from realms.legislation import F, above, forbid, pct

class Finance(above('finance')):
    cap = forbid('loan_create', F.rate > pct('{cap}'), legislation='x')
{extra}"""


def test_a_lowered_cap_on_one_fact_is_a_range_when_nothing_else_cuts_it(world):
    base = world.change({"eu/es/finance.py": rate_cap("4.00")})
    head = world.change({"eu/es/finance.py": rate_cap("3.00")})
    [result] = loan_results_between(world, base, head)
    assert result.statements == [
        "rate more than 3.00 % (3000000) and at most 4.00 % (4000000) newly forbidden"
    ]


def test_a_range_that_a_forbid_both_sets_share_cuts_is_not_stated(world):
    shared = (
        "    closed = forbid('loan_create', F.region == 'closed', legislation='x')\n"
    )
    base = world.change({"eu/es/finance.py": rate_cap("4.00", shared)})
    head = world.change({"eu/es/finance.py": rate_cap("3.00", shared)})
    [result] = loan_results_between(world, base, head)
    assert result.forbidden.proved is False and result.allowed.proved is True
    assert (
        result.statements == []
    )  # rate 3 to 4 % is forbidden before and after for region "closed"
    assert result.undecided == []
    assert "newly forbidden: yes, e.g." in world.diff(base, head).output


def test_no_range_is_stated_when_a_fact_the_forbids_read_is_a_period(world):
    base = world.change({"eu/es/finance.py": loans("5.00", "3.00")})
    head = world.change({"eu/es/finance.py": loans("5.00", "2.00")})
    [result] = loan_results_between(world, base, head)
    assert result.forbidden.proved is False
    assert result.statements == []  # the term is a Period: it cannot be pinned


def justice(refused):
    return f"""from realms.legislation import F, above, forbid

class Justice(above('justice')):
    no_penalties = forbid('verdict_draft', F.kind.in_({refused!r}), each='penalties', legislation='x')
"""


def test_a_forbid_that_refuses_every_word_of_an_enumeration_makes_the_operation_dead(
    world,
):
    head = world.change({"eu/es/justice.py": justice(KINDS)})
    spain = world.realm(world.base, head, "eu/es")
    [result] = [r for r in spain.folded if r.action == "verdict_draft.penalties"]
    assert result.dead.proved is True
    assert any(
        "verdict_draft.penalties: nothing is permitted" in f for f in spain.failures()
    )
    assert world.diff(world.base, head).exit_code == 1


def test_a_forbid_that_leaves_a_word_of_the_enumeration_open_does_not(world):
    head = world.change({"eu/es/justice.py": justice(KINDS[:3])})
    [result] = [
        r
        for r in world.realm(world.base, head, "eu/es").folded
        if r.action == "verdict_draft.penalties"
    ]
    assert result.dead.proved is False
    assert result.forbidden.proved is False
    assert result.forbidden.example["facts"]["kind"] in KINDS[:3]


def test_a_forbid_of_a_word_outside_the_enumeration_never_fires(world):
    head = world.change({"eu/es/justice.py": justice(["Revocation"])})
    spain = world.realm(world.base, head, "eu/es")
    assert any("eu/es/justice/no_penalties never fires" in f for f in spain.failures())


def amount_cap(cap, setting="", gate=""):
    return f"""from realms.legislation import F, S, above, forbid, overridable, pct

class Tax(above('tax')):
{setting}
    cap_without_amount = forbid('tax_policy_propose',
        {gate}(F.code == 'ibi_urban') & F.amount.is_none() & F.rate.is_some() & (F.rate > pct('{cap}')),
        each='taxes', legislation='x')
"""


def test_the_words_say_what_the_proof_pinned_an_absent_optional_fact(world):
    base = world.change({"eu/es/tax.py": amount_cap("2.00")})
    head = world.change({"eu/es/tax.py": amount_cap("1.00")})
    [result] = [
        r
        for r in world.realm(base, head, "eu/es").folded
        if r.action == "tax_policy_propose.taxes"
    ]
    assert result.statements == [
        'code = "ibi_urban", no amount: rate more than 1.00 % (1000000) and at most 2.00 % (2000000) newly forbidden'
    ]


def test_the_words_say_what_the_proof_pinned_a_setting_that_is_not_a_boolean(world):
    setting = "    tier = overridable(1, legislation='x')"
    base = world.change(
        {"eu/es/tax.py": amount_cap("2.00", setting, "(S.tier == 1) & ")}
    )
    head = world.change(
        {"eu/es/tax.py": amount_cap("1.00", setting, "(S.tier == 1) & ")}
    )
    [result] = [
        r
        for r in world.realm(base, head, "eu/es").open
        if r.action == "tax_policy_propose.taxes"
    ]
    assert result.statements == [
        'code = "ibi_urban", no amount, tier = 1: rate more than 1.00 % (1000000) and at most 2.00 % (2000000) newly forbidden'
    ]


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


PRIVACY = """from realms.legislation import F, S, above, forbid, overridable

class Privacy(above('privacy')):
    strict = overridable(False, legislation='x')
    no_secret_sharing = forbid('document_share', S.strict & (F.kind == 'secret'), legislation='x')
"""
VALENCIA_PRIVACY = "from realms.legislation import above\n\nclass Privacy(above('privacy')):\n    strict = True\n"


def share_results(realm_diff, which="folded"):
    return [r for r in getattr(realm_diff, which) if r.action == "document_share"]


def test_a_forbid_that_a_setting_switches_off_in_this_realm_is_information_not_a_failure(
    world,
):
    head = world.change(
        {"eu/es/privacy.py": PRIVACY, f"{VALENCIA}/privacy.py": VALENCIA_PRIVACY}
    )
    spain = world.realm(world.base, head, "eu/es")
    [folded] = share_results(spain)
    assert list(folded.off_here) == ["eu/es/privacy/no_secret_sharing"]
    assert folded.off_here["eu/es/privacy/no_secret_sharing"] == ["strict"]
    assert folded.never_fires == []
    [open_] = share_results(spain, "open")
    assert open_.never_fires == [] and open_.off_here == {}  # with strict open it fires
    assert spain.failures() == []
    valencia = world.realm(world.base, head, VALENCIA)
    assert share_results(valencia)[0].off_here == {}  # strict is on there
    report = world.diff(world.base, head)
    assert report.exit_code == 0
    assert (
        "- off in this realm: `eu/es/privacy/no_secret_sharing` reads `strict` and never fires"
        in report.output
    )


def test_a_forbid_that_reads_a_setting_and_never_fires_with_it_open_fails(world):
    never = PRIVACY.replace(
        "S.strict & (F.kind == 'secret')",
        "S.strict & (F.kind == 'a') & (F.kind == 'b')",
    )
    head = world.change({"eu/es/privacy.py": never})
    spain = world.realm(world.base, head, "eu/es")
    assert any(
        "no_secret_sharing never fires with the settings left open" in f
        for f in spain.failures()
    )
    assert world.diff(world.base, head).exit_code == 1


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
    assert "-         && context.facts.rate > 1100000" in "\n".join(spain.cedar)
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


def test_a_forbid_that_does_not_bind_fails_that_realm_and_is_no_usage_error(world):
    unbound = (
        "\n    ghost = forbid('document_share', F.nothing == 'x', legislation='x')\n"
    )
    head = world.change({"eu/es/tax.py": tax("1.10", rustic("0.90") + unbound)})
    spain = world.realm(world.base, head, "eu/es")
    assert spain.folded == [] and spain.failures()
    assert all("does not load" in f for f in spain.failures())
    out = world.diff(world.base, head)
    assert out.exit_code == 1 and "does not load" in out.output
    fixed = world.change({"eu/es/tax.py": tax("1.10", rustic("0.90"))})
    again = world.realm(head, fixed, "eu/es")
    assert any("does not load at the base commit" in f for f in again.failures())
    assert world.diff(head, fixed).exit_code == 1


def test_a_change_to_only_the_cases_of_a_chapter_reaches_the_realms_that_run_them(
    world,
):
    first = world.change({"eu/es/tax.cases.json": CASES})
    head = world.change({"eu/es/tax.cases.json": [{**CASES[0], "returns": True}]})
    spain = world.realm(first, head, "eu/es")
    assert spain.domains == ["tax"] and [why for _, why in spain.cases] == [
        "returns False, written True"
    ]
    out = world.diff(first, head)
    assert out.exit_code == 1 and "Changed: `eu/es/tax`" in out.output


def test_a_flattened_line_the_solver_does_not_decide_is_undecided_and_fails(
    world, monkeypatch
):
    from realms.cli.commands import legislation_proof
    from realms.cli.commands.legislation_diff import folded_line

    codex = loaded(world, world.base, "eu/es")
    bound = next(f for f in codex.forbids() if f.id.endswith("ibi_urban_cap"))

    class Undecided:
        def __getattr__(self, name):
            return lambda *args: Answer(None, None, "no answer in 0 s")

    undecided = []
    assert folded_line(Undecided(), codex, bound, bound.actions[0], undecided) is None
    assert undecided and "no answer in 0 s" in undecided[0]
    monkeypatch.setattr(legislation_proof, "SYMCC_TIMEOUT", 0.001)
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    spain = world.realm(world.base, head, "eu/es")
    assert any("maximum in the flattened view" in u for u in spain.undecided)
    assert any("maximum in the flattened view" in f for f in spain.failures())


def test_an_answer_without_a_request_is_not_printed_as_none(world):
    from realms.cli.commands.legislation_report import difference

    nameless = Answer(False, None, "")
    [line] = difference("newly forbidden", nameless, False)
    assert "None" not in line and line.startswith("- newly forbidden: yes")
    head = world.change({"eu/es/tax.py": tax("1.10", dead_extra())})
    spain = world.realm(world.base, head, "eu/es")
    assert spain.failures() and not any("None" in f for f in spain.failures())


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


def test_the_realms_the_time_budget_does_not_reach_are_listed_as_not_analysed_and_fail(
    world, monkeypatch
):
    from types import SimpleNamespace

    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    monkeypatch.setattr(legislation, "time", SimpleNamespace(monotonic=lambda: 0.0))
    out = world.diff(world.base, head, "--budget", "1")  # the deadline is 1: long past
    assert out.exit_code == 1
    assert "**2 failure(s)**" in out.output
    assert "- FAILED eu/es: not analysed (time budget)" in out.output
    assert f"- FAILED {VALENCIA}: not analysed (time budget)" in out.output
    assert out.output.startswith("<!-- realms-legislation-diff -->")


def test_a_budget_that_runs_out_part_way_stops_the_realm_and_says_so(
    world, monkeypatch
):
    from realms.cli.commands import legislation_proof

    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    ask, calls = legislation_proof.Solver.ask, []

    def short_of_time(self, *args):
        calls.append(args)
        if len(calls) > 3:
            raise legislation_proof.OutOfTime()
        return ask(self, *args)

    monkeypatch.setattr(legislation_proof.Solver, "ask", short_of_time)
    spain = world.realm(world.base, head, "eu/es")
    assert spain.problems == ["not analysed (time budget): stopped part-way"]
    assert spain.folded == [] and spain.failures() == [
        "eu/es: not analysed (time budget): stopped part-way"
    ]


def test_a_solver_with_a_deadline_stops_a_running_query_and_starts_no_new_one(world):
    import time

    from realms.cli.commands.legislation_proof import OutOfTime

    codex = loaded(world, world.base, "eu/es")
    schema = codex.cedar_schema(context={"op": "String"})
    for deadline in (time.monotonic() - 1, time.monotonic() + 0.0001):
        solver = Solver(*world.tools, schema, deadline)
        with pytest.raises(OutOfTime):
            solver.always_denies(
                "tax_policy_propose.taxes", ["permit (principal, action, resource);"]
            )
    assert (
        Solver(*world.tools, schema, time.monotonic() + 600)
        .always_denies(
            "tax_policy_propose.taxes", ["permit (principal, action, resource);"]
        )
        .proved
        is False
    )


def test_a_realm_that_is_no_source_is_refused_like_check_does(world, tmp_path):
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90"))})
    out = world.diff(world.base, head, "--realm", "eu/xx")
    assert out.exit_code == 2 and "eu/xx has no source.json" in out.output
    listing = tmp_path / "change.json"
    listing.write_text(json.dumps({"realm": "eu/xx", "set": []}))
    out = world.diff(world.base, head, "--change-list", str(listing))
    assert out.exit_code == 2 and "eu/xx has no source.json" in out.output


def test_the_report_has_each_section_with_its_heading_and_a_key_line(world):
    secret = (
        "\n    secret = forbid('document_share', F.kind == 'secret', legislation='x')\n"
    )
    first = world.change(
        {
            "eu/es/tax.cases.json": CASES,
            "eu/es/tax.py": tax("1.10", rustic("0.90") + secret),
        }
    )
    head = world.change({"eu/es/tax.py": tax("1.00", rustic("0.90") + secret)})
    out = world.diff(first, head)
    assert out.exit_code == 0, out.output
    report = out.output
    ordered = [
        "<!-- realms-legislation-diff -->",
        f"## Legislation analysis: `{first[:7]}` → `{head[:7]}`",
        "**No failure**, 2 realm(s) reached.",
        "### Dependencies",
        "Changed: `eu/es/tax`",
        "| realm | domains reached (changed, or calling one that changed) |",
        "### `eu/es`",
        "#### settings folded in as this realm has them",
        "**tax_policy_propose.taxes**",
        '- code = "ibi_urban": rate more than 1.00 % (1000000) and at most 1.10 % (1100000) newly forbidden (proved)',
        "- newly allowed: none (proved)",
        "#### settings left open: for every realm that runs this source",
        'code = "ibi_urban", not capital: rate more than 1.00 % (1000000) and at most 1.10 % (1100000) newly forbidden (proved)',
        "#### Test cases",
        "1 of 1 pass.",
        "<summary>Flattened view of the changed domains, before and after</summary>",
        "for eu/es: maximum 1.00 % (1000000)",
        "<summary>Generated Cedar, diff</summary>",
        "+         && context.facts.rate > 1000000",
        f"### `{VALENCIA}`",
    ]
    at = -1
    for line in ordered:
        found = report.find(line, at + 1)
        assert found > at, f"{line!r} is missing or out of order in:\n{report}"
        at = found
    assert "Nothing newly forbidden or allowed" in report
    assert (
        report.count("`document_share`") == 3
    )  # the quiet line of eu/es folded and open, and of València
    assert "**document_share**" not in report  # nothing to say: no block of its own


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
