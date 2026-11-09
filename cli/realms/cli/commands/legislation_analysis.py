"""What ``realms legislation check`` proves about the forbids of a codex: Python and Cedar agree on generated facts
(docs/codices/layered-codex.md §6 rule 7, §12.1), and Cedar never errors on them (§6 rule 9).

The facts are drawn around every literal and endpoint the forbids name, the 64-bit extremes and the edges of each
fact's range, and the same forbids are decided in plain Python and by Cedar (``cedarpy``, the canister's Cedar
line). ``agree`` returns how many requests it checked and every one on which they differ, or on which Cedar errors.
"""

import os
import random
import subprocess
import tempfile
from pathlib import Path

from realms.legislation.expressions import I64_MAX, I64_MIN
from realms.legislation.periods import UNITS

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
            for v in (node.value if isinstance(node.value, tuple) else [node.value])
            if isinstance(v, (int, str)) and not isinstance(v, bool)
        )
    for child in children(node):
        literals(child, found)
    return found


def endpoints(forbid, found, settings=None):
    """The folded value of every comparison side and between() bound that reads no fact (1240000 for
    València's urban cap), and the count of every Period a rule names or a setting holds. ``settings`` replaces the
    forbid's own, for the analysis that leaves them open.
    """
    from realms.legislation import expressions as e
    from realms.legislation.cedar import children

    def reads_a_fact(node):
        return isinstance(node, e.Fact) or any(reads_a_fact(c) for c in children(node))

    def value(node):
        got = e._eval(
            node,
            e.Env({}, dict(forbid.settings if settings is None else settings), None),
            False,
        )
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
        if (
            kind == "Record"
        ):  # a record a forbid can only test for (is_some, is_none): Cedar's empty record
            return {}
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
    report no evaluation error. Returns how many requests were checked and the disagreements.
    """
    import cedarpy

    forbids = c.forbids()
    schema = c.cedar_schema(generic, context={"op": "String"})
    policies = PERMIT_ALL + c.cedar(generic)
    draw, checked, problems = Draw(seed, forbids), 0, []
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
            facts = request["context"]["facts"]
            by_cedar = (
                set(result.diagnostics.id_annotations_by_reason.values())
                if result.decision == cedarpy.Decision.Deny
                else set()
            )
            if result.diagnostics.errors:
                problems.append(
                    f"{name}: Cedar errors on {facts}: {result.diagnostics.errors}"
                )
            elif by_cedar != fired:
                problems.append(
                    f"{name}: on {facts} Cedar forbids {sorted(by_cedar)}, Python {sorted(fired)}"
                )
        checked += len(requests)
    return checked, problems


# ── Cedar never errors (§6 rule 9) ────────────────────────────────────────────────────────────────────────────────

SYMCC_TIMEOUT = 300  # seconds for one forbid on one action; a solver that has not decided by then has not proved it


def symcc(cedar_cli, cvc5, schema, policy, action):
    """Whether the Cedar CLI's SymCC (with cvc5) proves ``policy`` never errors on ``action``, and what it said:
    ``True`` (VERIFIED), ``False`` (DOES NOT HOLD, with a request that errors) or ``None`` (undecided).
    """
    with tempfile.TemporaryDirectory() as folder:
        schema_file, policy_file = Path(folder, "realm.cedarschema"), Path(
            folder, "forbid.cedar"
        )
        schema_file.write_text(schema)
        policy_file.write_text(policy + "\n")
        try:
            out = subprocess.run(
                [cedar_cli, "symcc", "--principal-type", "Realm::User", "--action", f'Realm::Action::"{action}"',
                 "--resource-type", "Realm::Realm", "--schema", str(schema_file), "never-errors",
                 "--policies", str(policy_file)],
                capture_output=True, text=True, timeout=SYMCC_TIMEOUT, env={**os.environ, "CVC5": cvc5},
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            return None, f"no answer in {SYMCC_TIMEOUT} s"
    said = (out.stdout + out.stderr).strip()
    if out.returncode == 0 and "VERIFIED" in out.stdout:
        return True, said
    return (False if "DOES NOT HOLD" in out.stdout else None), said


def never_errors_problems(codex, cedar_cli, cvc5, proved=None):
    """The forbids of ``codex`` that SymCC does not prove never error, for each action they name: Cedar skips a
    forbid that errors (an integer overflow, a missing attribute), so such a forbid would allow what it forbids.
    An undecided answer is a problem too, never a pass. ``proved`` remembers the answer for each Cedar text and action.
    """
    proved = {} if proved is None else proved
    schema, problems = codex.cedar_schema(context={"op": "String"}), []
    for bound in codex.forbids():
        for action in bound.actions:
            key = (bound.cedar(), action)
            if key not in proved:
                answer, said = symcc(cedar_cli, cvc5, schema, key[0], action)
                what = (
                    "can error" if answer is False else "is not proved to never error"
                )
                proved[key] = (
                    None
                    if answer
                    else f"{bound.file}:{bound.line}: {bound.id} {what} on {action}: {said}"
                )
            if proved[key]:
                problems.append(proved[key])
    return problems


# ── What check runs ───────────────────────────────────────────────────────────────────────────────────────────────


class MissingTools(Exception):
    """The Cedar analysis cannot run here; the message says what to install."""


class Analysis:
    """The forbids of each realm's codex, bound and analysed: bound (the ``F`` rules of §6), Python and Cedar agreeing
    on generated facts, and Cedar never erroring. A forbid with the same Cedar in two realms is proved once.
    """

    def __init__(self, skip=False, per_action=500):
        self.skip, self.per_action, self.proved = skip, per_action, {}

    def problems(self, codex):
        """Every problem with ``codex``'s forbids. The binding refuses with ``Refused`` like a chapter does."""
        forbids = codex.forbids()
        if self.skip or not forbids:
            return []
        cedar_cli, cvc5 = self.tools()
        problems = []
        # with the realm's settings folded in, and left open as the analysis of §12.3 reads them
        for generic in (False, True):
            problems += agree(codex, generic, self.per_action)[1]
        return problems + never_errors_problems(codex, cedar_cli, cvc5, self.proved)

    def tools(self):
        try:
            import cedarpy  # noqa: F401  agree decides with it
        except ImportError:
            raise MissingTools(
                "the Cedar analysis needs cedarpy: pip install 'cedarpy==4.12.2'"
            )
        return cedar_tools(
            "proof that no forbid errors", " (or pass --no-analysis to go without)"
        )


def cedar_tools(what: str, hint: str = ""):
    """The pinned Cedar CLI and cvc5 that ``CEDAR_CLI`` and ``CVC5`` point at, or a refusal that says how to get them."""
    cedar_cli, cvc5 = os.environ.get("CEDAR_CLI"), os.environ.get("CVC5")
    if not (cedar_cli and cvc5 and os.path.exists(cedar_cli) and os.path.exists(cvc5)):
        raise MissingTools(
            f"the {what} needs the pinned Cedar CLI and cvc5: run scripts/cedar_tools.sh "
            f"and set CEDAR_CLI and CVC5 to what it prints{hint}"
        )
    return cedar_cli, cvc5
