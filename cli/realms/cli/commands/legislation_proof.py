"""Questions to Cedar's SymCC about sets of forbids (docs/codices/layered-codex.md §12.3).

SymCC (``cedar symcc`` of the Cedar CLI 4.14.1, experimental flavour, with cvc5 1.3.1) proves a property of policy sets
for every request, or gives one that breaks it. Each question is asked of one Cedar action, over a set that starts with
``permit`` for everything so that only the forbids decide: the legislation, not who holds which permission, and with
the assumptions of the fact schema added, since a Cedar schema cannot state a range (R1, realms-gos#481).

An ``Answer`` is ``proved`` True (VERIFIED), False (DOES NOT HOLD, with the request that breaks it as ``example``) or
None (the solver did not decide: never a pass).
"""

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

from realms.legislation.cedar import NAMESPACE, attribute, has, quoted
from realms.legislation.periods import UNITS

SYMCC_TIMEOUT = 300  # seconds for one query; the CLI itself stops cvc5 after 60
PERMIT_ALL = '@id("harness/permit_all")\npermit (principal, action, resource);'
PERIOD_COUNT_MAX = 1_000_000  # GGG's range for a Period's count


class OutOfTime(Exception):
    """The time budget of the command (``realms legislation diff --budget``) ran out before the query was answered."""


class Answer(NamedTuple):
    proved: Optional[bool]
    example: Optional[
        dict
    ]  # the context of a request that breaks the property, when it does not hold
    said: str


class Solver:
    """The Cedar CLI's ``symcc`` for one schema."""

    def __init__(
        self,
        cedar_cli: str,
        cvc5: str,
        schema: str,
        deadline: Optional[float] = None,
    ):
        """``deadline`` is a ``time.monotonic()`` value after which no query is started and a running one is stopped
        (``OutOfTime``)."""
        self.cedar_cli, self.cvc5, self.schema = cedar_cli, cvc5, schema
        self.deadline = deadline

    def never_errors(self, action: str, policy: str) -> Answer:
        return self.ask(action, "never-errors", {"-p": [policy]})

    def always_denies(self, action: str, policies: List[str]) -> Answer:
        return self.ask(action, "always-denies", {"-p": policies})

    def implies(self, action: str, first: List[str], second: List[str]) -> Answer:
        """Whether every request the set ``first`` allows is allowed by ``second``."""
        return self.ask(
            action, "implies", {"--policies1": first, "--policies2": second}
        )

    def equivalent(self, action: str, first: List[str], second: List[str]) -> Answer:
        return self.ask(
            action, "equivalent", {"--policies1": first, "--policies2": second}
        )

    def ask(self, action: str, check: str, sets: dict) -> Answer:
        left = None if self.deadline is None else self.deadline - time.monotonic()
        if left is not None and left <= 0:
            raise OutOfTime()
        allowed = SYMCC_TIMEOUT if left is None else min(SYMCC_TIMEOUT, left)
        with tempfile.TemporaryDirectory() as folder:
            schema = Path(folder, "realm.cedarschema")
            schema.write_text(self.schema)
            files = []
            for flag, policies in sets.items():
                path = Path(folder, f"{len(files)}.cedar")
                path.write_text("\n\n".join(policies) + "\n")
                files += [flag, str(path)]
            command = [
                self.cedar_cli, "symcc", "--principal-type", f"{NAMESPACE}::User", "--action",
                f'{NAMESPACE}::Action::"{action}"', "--resource-type", f"{NAMESPACE}::Realm",
                "--schema", str(schema), "--counterexample", check, *files,
            ]  # fmt: skip
            try:
                done = subprocess.run(
                    command, capture_output=True, text=True, timeout=allowed,
                    env={**os.environ, "CVC5": self.cvc5},
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                if allowed < SYMCC_TIMEOUT:
                    raise OutOfTime()  # stopped by the budget, not by the solver's own limit
                return Answer(None, None, f"no answer in {SYMCC_TIMEOUT} s")
        said = (done.stdout + done.stderr).strip()
        verdict = verdict_of(done.stdout)
        if verdict is False:
            return Answer(False, example_of(done.stdout), said)
        if verdict is True and done.returncode == 0:
            return Answer(True, None, said)
        return Answer(None, None, said)


VERDICT = re.compile(
    r"^\S+ .*: (VERIFIED|DOES NOT HOLD)\s*$"
)  # "✓ Policy set 1 implies policy set 2: VERIFIED"


def verdict_of(output: str) -> Optional[bool]:
    """True for VERIFIED, False for DOES NOT HOLD, None for neither, read from SymCC's verdict line alone: the first
    line that ends in one of the two. The counterexample after it holds the request's own values, and a request can
    carry the word VERIFIED as a string."""
    for line in output.splitlines():
        found = VERDICT.match(line)
        if found:
            return found.group(1) == "VERIFIED"
    return None


# ── The request a counterexample names ────────────────────────────────────────────────────────────────────────────

TOKEN = re.compile(r'\s*("(?:\\.|[^"\\])*"|-?\d+|[{}\[\],:]|[A-Za-z_][A-Za-z_0-9]*)')


def example_of(output: str) -> Optional[dict]:
    """The context of the request a counterexample names, from the ``context: {...}`` line SymCC prints."""
    line = next(
        (row for row in output.splitlines() if row.startswith("context:")), None
    )
    if line is None:
        return None
    tokens = TOKEN.findall(line[len("context:") :])
    try:
        value, _ = read_value(tokens, 0)
    except (IndexError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_value(tokens: List[str], i: int):
    """The Cedar value that starts at ``tokens[i]``: a record, a set, a string, a number or a Boolean."""
    token = tokens[i]
    if token == "{":
        record, i = {}, i + 1
        while tokens[i] != "}":
            key = tokens[i]
            if tokens[i + 1] != ":":
                raise ValueError(f"{key} is not followed by a colon")
            record[key.strip('"')], i = read_value(tokens, i + 2)
            i += tokens[i] == ","
        return record, i + 1
    if token == "[":
        items, i = [], i + 1
        while tokens[i] != "]":
            item, i = read_value(tokens, i)
            items.append(item)
            i += tokens[i] == ","
        return items, i + 1
    if token[0] == '"':
        return bytes(token[1:-1], "utf-8").decode("unicode_escape"), i + 1
    if token in ("true", "false"):
        return token == "true", i + 1
    return int(token), i + 1


# ── The requests GGG's fact schema allows ─────────────────────────────────────────────────────────────────────────


def assumption(codex, action: str) -> Optional[str]:
    """A forbid that fires on the requests of ``action`` that the fact schema rules out: a number outside the range GGG
    gives it, a Period of a count or a unit GGG does not have, a word outside the enumeration GGG gives a fact. Added to
    every set under analysis, it takes them out of the question ("a rate lies between 0 and 100 %"). It is analysis
    input and is never installed.
    """
    breaks = []
    for name, fact in codex.facts.actions[action].facts.items():
        outside = fact_outside(name, fact)
        if outside:
            breaks.append(
                outside
                if not fact.optional
                else f"({has('context.facts', name)} && {outside})"
            )
    if not breaks:
        return None
    return (
        f'@id("assume/{action}")\n'
        f"forbid (principal, action == {NAMESPACE}::Action::{quoted(action)}, resource)\n"
        f"  when {{ {' || '.join(breaks)} }};"
    )


def fact_outside(name: str, fact) -> Optional[str]:
    """What it is for the fact ``name`` to be outside what GGG allows (a range, a Period's count and unit, an
    enumeration), or None where it allows anything."""
    value = attribute("context.facts", name)
    if fact.type == "Long" and fact.range:
        low, high = fact.range
        return f"({value} < {low} || {value} > {high})"
    if fact.type == "String" and fact.values:
        return f"!({' || '.join(f'{value} == {quoted(v)}' for v in fact.values)})"
    if fact.type == "Period":
        units = " || ".join(f"{value}.unit == {quoted(u)}" for u in UNITS)
        return (
            f"({value}.count < 0 || {value}.count > {PERIOD_COUNT_MAX} || !({units}))"
        )
    return None


def forbids_on(codex, action: str, generic: bool = False) -> dict:
    """The Cedar of each forbid of ``codex`` on ``action``, by id."""
    return {f.id: f.cedar(generic) for f in codex.forbids() if action in f.actions}


def policy_set(forbids: dict, assumed: Optional[str]) -> List[str]:
    """The set a question is asked of: everything permitted, the forbids, the assumption."""
    return [PERMIT_ALL, *forbids.values(), *([assumed] if assumed else [])]


def everything_permitted(assumed: Optional[str]) -> List[str]:
    """The set before any forbid: everything permitted, bar what the fact schema rules out."""
    return [PERMIT_ALL, *([assumed] if assumed else [])]
