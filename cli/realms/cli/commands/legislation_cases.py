"""The test cases of a chapter (docs/codices/layered-codex.md §12.1, §12.3): ``<source>/<domain>.cases.json``, beside
the chapter it tests, run by ``realms legislation check`` on the codex of the realm they name.

A case is an object with a ``name``, an optional ``realm`` (a source id; the chapter's own source by default, seen
with the layers above it) and one kind of test, run in-process with the library:

- ``{"call": "ibi_bonus", "args": ["User:alice"], "returns": 0}``: a public member of the resolved domain, a
  helper called with plain arguments or a setting read;
- ``{"guard": "proposal_create", "args": {...}, "approvals": [...], "now": 1800000000, "denies": "reason" | null}``:
  the domain's ``check_<op>`` on the operation's arguments. The realm's time zone is UTC here;
- ``{"forbid": "tax_policy_propose", "each": "taxes", "facts": {...}, "fires": ["eu/es/tax/ibi_urban_cap"]}``: the
  forbids of the realm that fire on those facts, evaluated in plain Python with the settings folded;
- ``{"compute": "tax_assess", "args": {"entry": {...}, "subject": "Land:L-1"}, "decimals": 2, "records": {"Land":
  [...], "Human": [...]}, "returns": [...]}``: the domain's ``compute_<op>``, run by the realm's own sandbox program
  (``core.legislation_sandbox``) on those arguments, reading GGG's entities from ``records`` (rows with an ``id``)
  and money in a token of ``decimals``.

The shape of the file is checked with the rest of the layout (``legislation_layout.cases_reasons``); here it is run.
A case that does not come out as written is a problem, ``<source>/<domain>.cases.json: case "<name>": <what>``.
"""

import importlib
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, List

from realms.cli.commands.legislation_layout import CASES_SUFFIX
from realms.legislation import Codex, LegislationError
from realms.legislation.context import Args, plain

SANDBOX = "realms.src.realm_backend.core.legislation_sandbox"
# The fields of the entities a chapter reads that it reads as Money (core.legislation.MONEY, which a test keeps equal).
MONEY = {"Land": ("assessed_value",)}


def case_problems(tree: Path, codex: Codex, realm: str, reload: Callable) -> List[str]:
    """The cases of ``codex``'s layers that name ``realm``, run on ``codex``; those that do not come out as written.
    ``reload(ggg)`` loads the realm's codex again with ``ggg`` as the module chapters import ``ggg`` from.
    """
    problems = []
    for source in codex.layers:
        for file in sorted((tree / source).glob("*" + CASES_SUFFIX)):
            domain = file.name[: -len(CASES_SUFFIX)]
            for case in json.loads(file.read_text()):
                if case.get("realm", source) != realm:
                    continue
                reason = run_case(codex, domain, case, reload)
                if reason:
                    problems.append(
                        f'{source}/{file.name}: case "{case["name"]}": {reason}'
                    )
    return problems


def run_case(codex: Codex, domain: str, case: dict, reload: Callable):
    """Why ``case`` does not come out as written, or None."""
    try:
        if "call" in case:
            return call_case(codex, domain, case)
        if "guard" in case:
            return guard_case(codex, domain, case)
        if "compute" in case:
            return compute_case(case, reload)
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


def compute_case(case: dict, reload: Callable):
    """The realm's sandbox program, in this process, answering ``compute_<op>`` with GGG's entities read from the
    case's ``records``: the same code the realm runs, so the subject is the parcel's view and money carries the
    token's decimals."""
    sandbox = importlib.import_module(SANDBOX)
    records = case["records"]
    entities = sorted(set(records) | set(MONEY))
    sandbox.rpc = reads_from(records)
    codex = reload(sandbox.ggg_module(entities, MONEY))
    sandbox.STATE.update(
        codex=codex, files=set(), entities=frozenset(entities), ready=True
    )
    outcome = sandbox.compute(
        case["compute"],
        case["args"],
        case.get("now", 0),
        case.get("approvals", ()),
        case["decimals"],
    )
    if not outcome["found"]:
        return f"no chapter has compute_{case['compute']}"
    if "error" in outcome:
        return f"raised: {outcome['error']}"
    return differs(case["returns"], outcome["result"], "returns")


def reads_from(records: dict):
    """The sandbox's ``rpc``, answering a read of an entity from the case's rows, as the realm answers from its
    tables: one row by id, a page of all, or those whose field has a value. A money field a row leaves out is
    None, as a parcel never assessed has none."""

    def row(entity, found):
        return {**{f: None for f in MONEY.get(entity, ())}, **found}

    def rpc(action, **kw):
        if action == "local_day":
            return utc_day(kw["at"])
        rows = [row(kw["entity"], r) for r in records.get(kw["entity"], [])]
        if kw["method"] == "get":
            return next((r for r in rows if str(r["id"]) == kw["id"]), None)
        if kw["method"] == "find":
            rows = [r for r in rows if str(r.get(kw["field"])) == str(kw["value"])]
        return {"rows": rows, "next": None}

    return rpc


def differs(expected, got, what: str):
    return None if got == expected else f"{what} {got!r}, written {expected!r}"


def utc_day(at: int) -> str:
    """The day of a time in seconds. A realm's own time zone is not part of a case."""
    return datetime.utcfromtimestamp(at).strftime("%Y-%m-%d")
