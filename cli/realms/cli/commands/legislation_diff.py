"""What changes when the forbids of a realm change (docs/codices/layered-codex.md §12.3, points 2 to 4 and 8).

For one Cedar action, ``analyse_action`` asks SymCC (``legislation_proof``) of the forbids of the old codex and of
the new one, over every request the fact schema allows:

- what is **newly forbidden** and **newly allowed**, each proved or given with a request that breaks it, and, where the
  difference is a range of one fact, its readable form (``legislation_ranges``) proved equal to the difference;
- whether **nothing is permitted** any more (a dead operation), which forbids **never fire**, which are **redundant**
  (the other forbids already cover them: a warning), and which **can error** (Cedar skips a forbid that errors, so it
  would not forbid);
- every query the solver did not **decide**, which is a failure and never a pass.

The same is asked with the realm's settings folded in, and once with them left open (``generic``), which gives the
statements that hold for every realm that runs the source: "for capitals".
"""

import itertools
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from realms.cli.commands.legislation_proof import (
    PERMIT_ALL,
    Answer,
    Solver,
    assumption,
    everything_permitted,
    forbids_on,
    policy_set,
)
from realms.cli.commands.legislation_ranges import (
    around,
    case_words,
    claim,
    constants,
    describe,
    facts_read,
    regions,
    settings_read,
    shown,
)

SOLVERS = 4  # SymCC runs at once: each starts cvc5, and a codex with hundreds of forbids asks hundreds of questions


@dataclass
class ActionResult:
    """What the analysis found for one Cedar action."""

    action: str
    forbidden: Answer = (
        None  # proved: nothing is newly forbidden; else the request that is
    )
    allowed: Answer = None
    statements: List[str] = field(
        default_factory=list
    )  # the differences in words, each proved
    dead: Answer = None  # proved: no request is permitted
    never_fires: List[str] = field(default_factory=list)
    off_here: Dict[str, List[str]] = field(
        default_factory=dict
    )  # forbid -> the settings it reads, when it never fires with this realm's settings
    redundant: List[str] = field(default_factory=list)
    can_error: List[str] = field(default_factory=list)
    undecided: List[str] = field(default_factory=list)
    generic: bool = False  # asked with the settings left open

    def nothing_to_say(self) -> bool:
        """Whether the analysis found nothing for the action: no difference, no dead operation, no forbid that never
        fires (or is off in this realm), overlaps or can error, and every query decided.
        """
        return (
            self.forbidden.proved
            and self.allowed.proved
            and self.dead.proved is False
            and not (
                self.never_fires
                or self.off_here
                or self.redundant
                or self.can_error
                or self.undecided
            )
        )

    def failures(self) -> List[str]:
        out = (
            [
                f"{self.action}: nothing is permitted: every request the fact schema allows is forbidden"
            ]
            if self.dead.proved
            else []
        )
        when = " with the settings left open" if self.generic else ""
        out += [f"{self.action}: {f} never fires{when}" for f in self.never_fires]
        out += self.can_error
        return out + [f"{self.action}: undecided: {u}" for u in self.undecided]


def request_of(answer: Answer) -> str:
    """The request a counterexample names, or why there is none to name."""
    return (
        str(answer.example)
        if answer.example is not None
        else "(SymCC named no request that could be read)"
    )


def analyse_action(
    solver: Solver, action: str, old, new, generic: bool, errors: dict
) -> ActionResult:
    """The questions for ``action``, of ``old`` (a codex or None) and ``new``. ``errors`` remembers, by Cedar text and
    action, which forbids were proved never to error."""
    assumed = assumption(new, action)
    old_forbids = forbids_on(old, action, generic) if old else {}
    new_forbids = forbids_on(new, action, generic)
    before, after = policy_set(old_forbids, assumed), policy_set(new_forbids, assumed)
    result = ActionResult(action, generic=generic)
    result.forbidden = solver.implies(action, before, after)
    result.allowed = solver.implies(action, after, before)
    result.dead = solver.always_denies(action, after)
    if result.dead.proved is None:
        result.undecided.append(f"nothing permitted: {result.dead.said}")
    for kind, answer in (
        ("newly forbidden", result.forbidden),
        ("newly allowed", result.allowed),
    ):
        if answer.proved is None:
            result.undecided.append(f"{kind}: {answer.said}")
    settings = {
        f.id: settings_read(f.expression) for f in new.forbids() if action in f.actions
    }
    sweep(solver, action, new_forbids, assumed, result, settings, generic)
    if not generic:
        result.can_error = cannot_error(
            solver, action, new, new_forbids, errors, result
        )
    if result.forbidden.proved is False or result.allowed.proved is False:
        result.statements = explain(
            solver, action, old, new, generic, result, before, after, assumed
        )
    return result


def sweep(
    solver: Solver,
    action: str,
    forbids: dict,
    assumed: Optional[str],
    result: ActionResult,
    settings: Dict[str, List[str]],
    generic: bool,
) -> None:
    """Which forbids never fire, and which of the others the rest already cover. A forbid that reads a setting and
    never fires with this realm's settings is **off here**, which is information: another realm that runs the source may
    switch it on. It fails when it reads no setting, or when it never fires with the settings left open. Overlap is
    asked of the realm as it is (a forbid covered whatever the settings are is covered with these as well).
    """
    base = everything_permitted(assumed)
    everything = policy_set(forbids, assumed)

    def asked(id_):
        never = solver.equivalent(action, base, [*base, forbids[id_]])
        if generic:
            return never, None
        rest = policy_set({k: v for k, v in forbids.items() if k != id_}, assumed)
        return never, solver.equivalent(action, everything, rest)

    with ThreadPoolExecutor(SOLVERS) as pool:
        answers = dict(zip(forbids, pool.map(asked, forbids)))
    for id_, (never, covered) in answers.items():
        for kind, answer in (("never fires", never), ("redundant", covered)):
            if answer is not None and answer.proved is None:
                result.undecided.append(f"{id_} {kind}: {answer.said}")
        if never.proved:
            if settings.get(id_) and not generic:
                result.off_here[id_] = settings[id_]
            else:
                result.never_fires.append(id_)
        elif covered is not None and covered.proved:
            result.redundant.append(id_)


def cannot_error(
    solver: Solver, action: str, codex, forbids: dict, memo: dict, result: ActionResult
) -> List[str]:
    """The forbids of ``codex`` on ``action`` that SymCC does not prove never to error."""
    problems = []
    for bound in (f for f in codex.forbids() if action in f.actions):
        key = (forbids[bound.id], action)
        if key not in memo:
            memo[key] = solver.never_errors(action, forbids[bound.id])
        answer = memo[key]
        if answer.proved is None:
            result.undecided.append(f"{bound.id} never errors: {answer.said}")
        elif not answer.proved:
            problems.append(
                f"{bound.file}:{bound.line}: {bound.id} can error on {action}: {request_of(answer)}"
            )
    return problems


# ── The difference in words ───────────────────────────────────────────────────────────────────────────────────────

NEWLY = {  # (was forbidden, is forbidden) -> in the difference
    "forbidden": lambda was, is_: is_ and not was,
    "allowed": lambda was, is_: was and not is_,
}


def explain(
    solver, action, old, new, generic, result, before, after, assumed
) -> List[str]:
    """The newly forbidden and newly allowed in words: the range of one fact, found around each example (for each
    combination of the Boolean settings the change reads, when they are left open), written as forbids and proved equal
    to the difference. Nothing is said that is not proved: otherwise the examples stand.
    """
    old_bound = [f for f in old.forbids() if action in f.actions] if old else []
    new_bound = [f for f in new.forbids() if action in f.actions]
    old_text, new_text = cedars(old_bound, generic), cedars(new_bound, generic)
    differing = [f for f in old_bound if f.cedar(generic) not in new_text]
    differing += [f for f in new_bound if f.cedar(generic) not in old_text]
    found = {}
    for kind, newly in NEWLY.items():
        answer = getattr(result, kind)
        if answer.proved is False:
            found[kind] = find_ranges(
                answer.example,
                newly,
                differing,
                old_bound,
                new_bound,
                new,
                action,
                generic,
            )
    if not found or None in found.values():
        return []
    claims = {
        kind: [
            claim(action, x, runs, conditions, f"newly_{kind}_{n}")
            for n, (runs, conditions, _) in enumerate(cases)
        ]
        for kind, (x, cases) in found.items()
    }
    permits = {
        kind: [
            claim(action, x, runs, conditions, f"newly_{kind}_{n}", "permit")
            for n, (runs, conditions, _) in enumerate(cases)
        ]
        for kind, (x, cases) in found.items()
    }
    if not exactly(solver, action, claims, permits, before, after, assumed, result):
        return []
    schema = new.facts.actions[action].facts
    return [
        f"{words + ': ' if words else ''}{describe(x, first, last, schema[x].range)} newly {kind}"
        for kind, (x, cases) in found.items()
        for runs, _, words in cases
        for first, last in runs
    ]


def exactly(solver, action, claims, permits, before, after, assumed, result) -> bool:
    """Whether the claims are exactly the difference: ``claims["forbidden"]`` hold for the requests ``after`` forbids and
    ``before`` does not, ``claims["allowed"]`` for the requests ``before`` forbids and ``after`` does not. Five proofs
    (X is what ``after`` forbids, Y what ``before`` forbids, F and A the two claims):

    - F lies inside X, and A inside Y: every request ``after`` allows is allowed with F's forbid added, and the same for
      ``before`` and A;
    - F misses Y, and A misses X: the permit of F's requests (bar those the fact schema rules out, which both sets forbid)
      is allowed by ``before``, and the permit of A's by ``after``;
    - nothing of the difference is left out: ``after`` with A forbidden is the same set as ``before`` with F forbidden.

    A claim wider than the difference breaks one of the first four (a range with a fact left free, or a fact that a
    forbid both sets share already forbids); one narrower breaks the last. The first undecided answer is a failure.
    """
    questions = [
        (
            "the range of the difference",
            solver.equivalent(
                action,
                [*after, *claims.get("allowed", [])],
                [*before, *claims.get("forbidden", [])],
            ),
        )
    ]
    if "forbidden" in claims:
        questions += [
            (
                "the range newly forbidden lies inside what is forbidden",
                solver.implies(action, after, [PERMIT_ALL, *claims["forbidden"]]),
            ),
            (
                "the range newly forbidden misses what was forbidden",
                solver.implies(action, plus(permits["forbidden"], assumed), before),
            ),
        ]
    if "allowed" in claims:
        questions += [
            (
                "the range newly allowed lies inside what was forbidden",
                solver.implies(action, before, [PERMIT_ALL, *claims["allowed"]]),
            ),
            (
                "the range newly allowed misses what is forbidden",
                solver.implies(action, plus(permits["allowed"], assumed), after),
            ),
        ]
    proved = True
    for what, answer in questions:
        if answer.proved is None:
            result.undecided.append(f"{what}: {answer.said}")
        proved = proved and bool(answer.proved)
    return proved


def plus(policies: List[str], extra: Optional[str]) -> List[str]:
    return [*policies, extra] if extra else policies


def cedars(forbids: list, generic: bool) -> set:
    return {f.cedar(generic) for f in forbids}


def forbidden_by(forbids: list, facts: dict, settings: Optional[dict]) -> bool:
    """Whether any of ``forbids`` fires on ``facts`` (plain Python), with the example's settings when they are open."""
    return any(
        f.evaluate(facts, None if settings is None else settings.get(f.domain, {}))
        for f in forbids
    )


def find_ranges(example, newly, differing, old_bound, new_bound, new, action, generic):
    """Around the request ``example``, the fact ``x`` whose runs say where the sets differ in the way ``newly`` says, the
    other facts as the example has them and, with the settings open, each combination of the Boolean settings the change
    reads: ``(x, [(runs, conditions, the case in words), ...])``, or None where no fact explains the example.
    """
    facts, settings = example.get("facts", {}), (
        example.get("settings") if generic else None
    )
    names = facts_read_by(differing)
    schema = new.facts.actions[action].facts
    for x in names:
        fact = schema.get(x)
        if fact is None or fact.type != "Long" or not fact.range or x not in facts:
            continue
        cases = []
        for combination in combinations(settings, differing):

            def decide(value):
                changed = {**facts, x: value}
                return newly(
                    forbidden_by(old_bound, changed, combination),
                    forbidden_by(new_bound, changed, combination),
                )

            runs = regions(
                decide, constants(old_bound + new_bound, combination), *fact.range
            )
            if runs:
                fixed = read_settings(combination, differing)
                conditions = around(names, example, x, fixed)
                if conditions is None:
                    cases = None
                    break
                cases.append((runs, conditions, case_words(example, names, x, fixed)))
        if cases is not None and any(
            first <= facts[x] <= last for runs, _, _ in cases for first, last in runs
        ):
            return x, cases
    return None


def combinations(settings: Optional[dict], differing: list) -> List[Optional[dict]]:
    """The settings to try: the example's, or with them open, every setting of true and false for each Boolean setting
    the differing forbids read (the others as the example has them)."""
    if settings is None:
        return [None]
    slots = [
        (f.domain, n)
        for f in differing
        for n in settings_read(f.expression)
        if isinstance(settings.get(f.domain, {}).get(n), bool)
    ]
    slots = list(dict.fromkeys(slots))
    out = []
    for values in itertools.product((False, True), repeat=len(slots)):
        combination = {d: dict(v) for d, v in settings.items()}
        for (domain, name), value in zip(slots, values):
            combination[domain][name] = value
        out.append(combination)
    return out


def read_settings(settings: Optional[dict], differing: list) -> Optional[dict]:
    """``settings`` (by domain) restricted to the settings the differing forbids read."""
    if settings is None:
        return None
    read = {(f.domain, n) for f in differing for n in settings_read(f.expression)}
    return {
        d: {n: v for n, v in values.items() if (d, n) in read}
        for d, values in settings.items()
    }


def facts_read_by(forbids: list) -> List[str]:
    names = []
    for forbid in forbids:
        names += [n for n in facts_read(forbid.expression) if n not in names]
    return names


# ── The folded line of the flattened view (§5.9) ──────────────────────────────────────────────────────────────────


def folded_line(
    solver: Solver, codex, bound, action: str, undecided: List[str]
) -> Optional[str]:
    """``for eu/es/vc/46250-valencia: capital + surface_public_transport → maximum 1.24 % (1240000)`` for a forbid of one
    fact against a number the settings fold, proved equal to the forbid; None for any other forbid. A question the solver
    did not decide is added to ``undecided`` (a failure), not dropped.
    """
    assumed = assumption(codex, action)
    base = everything_permitted(assumed)
    fires = solver.equivalent(action, base, [*base, bound.cedar()])
    if fires.proved is None:
        undecided.append(f"{bound.id} (maximum in the flattened view): {fires.said}")
    if fires.proved is not False or not fires.example:
        return None
    facts = fires.example.get("facts", {})
    schema = codex.facts.actions[action].facts
    for x in facts_read(bound.expression):
        fact = schema.get(x)
        if fact is None or fact.type != "Long" or not fact.range or x not in facts:
            continue
        low, high = fact.range
        decide = lambda value: bound.evaluate({**facts, x: value})  # noqa: E731
        run = next(
            (
                r
                for r in regions(decide, constants([bound]), low, high)
                if r[0] <= facts[x] <= r[1]
            ),
            None,
        )
        if run is None or run[1] != high or run[0] == low:
            continue
        conditions = around(facts_read(bound.expression), fires.example, x)
        if conditions is None:
            continue
        proof = solver.equivalent(
            action,
            [*base, bound.cedar()],
            [*base, claim(action, x, [run], conditions, "maximum")],
        )
        if proof.proved is None:
            undecided.append(
                f"{bound.id} (maximum in the flattened view): {proof.said}"
            )
        if proof.proved:
            true = [
                n
                for n in settings_read(bound.expression)
                if bound.settings.get(n) is True
            ]
            return f"for {codex.realm}: {' + '.join(true) + ' → ' if true else ''}maximum {shown(run[0] - 1, fact.range)}"
    return None
