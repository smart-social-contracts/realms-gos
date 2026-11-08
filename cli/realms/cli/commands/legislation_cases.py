"""The test cases of a chapter (docs/codices/layered-codex.md §12.1, §12.3): ``<source>/<domain>.cases.json``, beside
the chapter it tests, run by ``realms legislation check`` on the codex of the realm they name.

A case is an object with a ``name``, an optional ``realm`` (a source id; the chapter's own source by default, seen
with the layers above it) and one kind of test, run in-process with the library:

- ``{"call": "ibi_bonus", "args": ["User:alice"], "returns": 0}``: a public member of the resolved domain, a
  helper called with plain arguments or a setting read;
- ``{"guard": "proposal_create", "args": {...}, "approvals": [...], "now": 1800000000, "denies": "reason" | null}``:
  the domain's ``check_<op>`` on the operation's arguments. The realm's time zone is UTC here;
- ``{"forbid": "tax_policy_propose", "each": "taxes", "facts": {...}, "fires": ["eu/es/tax/ibi_urban_cap"]}``: the
  forbids of the realm that fire on those facts, evaluated in plain Python with the settings folded.

The shape of the file is checked with the rest of the layout (``legislation_layout.cases_reasons``); here it is run.
A case that does not come out as written is a problem, ``<source>/<domain>.cases.json: case "<name>": <what>``.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List

from realms.cli.commands.legislation_layout import CASES_SUFFIX
from realms.legislation import Codex, LegislationError
from realms.legislation.context import Args, plain


def case_problems(tree: Path, codex: Codex, realm: str) -> List[str]:
    """The cases of ``codex``'s layers that name ``realm``, run on ``codex``; those that do not come out as written."""
    problems = []
    for source in codex.layers:
        for file in sorted((tree / source).glob("*" + CASES_SUFFIX)):
            domain = file.name[: -len(CASES_SUFFIX)]
            for case in json.loads(file.read_text()):
                if case.get("realm", source) != realm:
                    continue
                reason = run_case(codex, domain, case)
                if reason:
                    problems.append(
                        f'{source}/{file.name}: case "{case["name"]}": {reason}'
                    )
    return problems


def run_case(codex: Codex, domain: str, case: dict):
    """Why ``case`` does not come out as written, or None."""
    try:
        if "call" in case:
            return call_case(codex, domain, case)
        if "guard" in case:
            return guard_case(codex, domain, case)
        return forbid_case(codex, case)
    except LegislationError as e:
        return f"raised: {e}"


def call_case(codex: Codex, domain: str, case: dict):
    chapter = codex.instance(domain, codex.context())
    if case["call"] not in type(chapter).__members__:
        return f"the {domain} chapter has no member {case['call']}"
    member = getattr(chapter, case["call"])
    got = plain(member(*case.get("args", [])) if callable(member) else member)
    return differs(case["returns"], got, "returns")


def guard_case(codex: Codex, domain: str, case: dict):
    context = codex.context(case.get("now", 0), case.get("approvals", ()), utc_day)
    guard = getattr(codex.instance(domain, context), "check_" + case["guard"], None)
    if guard is None:
        return f"the {domain} chapter has no check_{case['guard']}"
    answer = guard(Args(case.get("args", {})))
    return differs(case["denies"], None if answer is None else answer.reason, "denies")


def forbid_case(codex: Codex, case: dict):
    action = f"{case['forbid']}.{case['each']}" if case.get("each") else case["forbid"]
    fired = sorted(
        f.id
        for f in codex.forbids()
        if action in f.actions and f.evaluate(case["facts"])
    )
    return differs(sorted(case["fires"]), fired, "fires")


def differs(expected, got, what: str):
    return None if got == expected else f"{what} {got!r}, written {expected!r}"


def utc_day(at: int) -> str:
    """The day of a time in seconds. A realm's own time zone is not part of a case."""
    return datetime.utcfromtimestamp(at).strftime("%Y-%m-%d")
