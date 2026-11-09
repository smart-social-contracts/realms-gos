"""The readable form of a difference between two sets of forbids (docs/codices/layered-codex.md §12.3 point 2, §5.9):
"urban IBI between 1.10 % and 1.24 % for capitals: newly allowed".

SymCC proves **whether** something changed and gives one request. The range is ours: the ``F`` trees compare a fact
only with constants (the settings folded or taken from the example), so what a set forbids is the same between two
neighbouring constants. ``regions`` evaluates the trees there, at each constant and one beside it, and joins what comes
out the same. ``claim`` writes the region as a forbid, and the caller asks SymCC to prove it equal to the difference:
a region that is not proved is not printed, only the example is.
"""

from typing import Callable, List, Optional, Tuple

from realms.cli.commands.legislation_analysis import endpoints, literals
from realms.legislation.cedar import NAMESPACE, attribute, children, has, quoted
from realms.legislation.expressions import Fact, Setting

RATIO_MAXIMA = (
    100_000_000,
    10_000_000_000,
)  # GGG's Ratio and Rate: hundred-millionths, 100 % and 10 000 %


def facts_read(node, found: Optional[list] = None) -> list:
    """The names of the facts a tree reads, in order of first appearance."""
    found = [] if found is None else found
    if isinstance(node, Fact) and node.name not in found:
        found.append(node.name)
    for child in children(node):
        facts_read(child, found)
    return found


def settings_read(node, found: Optional[list] = None) -> list:
    found = [] if found is None else found
    if isinstance(node, Setting) and node.name not in found:
        found.append(node.name)
    for child in children(node):
        settings_read(child, found)
    return found


def constants(forbids: list, settings: Optional[dict] = None) -> List[int]:
    """Every whole number the trees compare with: the literals and the folded sides. ``settings`` (by domain) replaces
    the forbids' own, for the analysis that leaves them open."""
    found = []
    for forbid in forbids:
        literals(forbid.expression, found)
        endpoints(
            forbid, found, None if settings is None else settings.get(forbid.domain, {})
        )
    return sorted({n for n in found if isinstance(n, int)})


def regions(
    decide: Callable[[int], bool], points: List[int], low: int, high: int
) -> List[Tuple[int, int]]:
    """The runs of whole numbers in ``low..high`` for which ``decide`` is true, given that ``decide`` only changes at a
    number in ``points`` or beside one."""
    breaks = sorted(
        {low, high, *[q for p in points for q in (p - 1, p, p + 1) if low <= q <= high]}
    )
    pieces = []  # (first, last, value): a break, then the numbers up to the next one
    for here, there in zip(breaks, breaks[1:] + [None]):
        pieces.append((here, here, decide(here)))
        if there is not None and there - here > 1:
            pieces.append((here + 1, there - 1, decide(here + 1)))
    runs = []
    for first, last, value in pieces:
        if not value:
            continue
        if runs and runs[-1][1] + 1 == first:
            runs[-1] = (runs[-1][0], last)
        else:
            runs.append((first, last))
    return runs


def percent(n: int) -> str:
    """A ratio in hundred-millionths as people write it: 1240000 is 1.24 %."""
    whole, fraction = divmod(abs(n), 1_000_000)
    return (
        f"{'-' if n < 0 else ''}{whole}.{f'{fraction:06d}'.rstrip('0').ljust(2, '0')} %"
    )


def shown(n: int, kind: Optional[tuple]) -> str:
    """A number of a fact: with its percentage when GGG's range for the fact is a ratio's."""
    return f"{percent(n)} ({n})" if kind and kind[1] in RATIO_MAXIMA else str(n)


def describe(name: str, first: int, last: int, bounds: Optional[tuple]) -> str:
    """``rate more than 1.14 % (1140000) and at most 1.24 % (1240000)``: the run ``first..last`` of a fact whose range
    is ``bounds``."""
    low, high = bounds if bounds else (None, None)
    if first == last:
        return f"{name} is {shown(first, bounds)}"
    parts = []
    if first != low:
        parts.append(f"more than {shown(first - 1, bounds)}")
    if last != high:
        parts.append(f"at most {shown(last, bounds)}")
    return f"{name} " + " and ".join(parts) if parts else f"any {name}"


# ── A region as a forbid ──────────────────────────────────────────────────────────────────────────────────────────


def condition_on(name: str, value, record: str = "context.facts") -> Optional[str]:
    """``record.name == value`` for a string, a Boolean or a whole number, ``!(record has name)`` for a fact the
    request leaves out, and None for a value Cedar compares less simply (a Period, a set).
    """
    if value is None:
        return f"!({has(record, name)})"
    if isinstance(value, bool):
        return f"{attribute(record, name)} == {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"{attribute(record, name)} == {value}"
    if isinstance(value, str):
        return f"{attribute(record, name)} == {quoted(value)}"
    return None


def claim(
    action: str,
    name: str,
    runs: List[Tuple[int, int]],
    around: List[str],
    label: str,
    effect: str = "forbid",
) -> str:
    """A policy of ``action`` (a forbid, or with ``effect="permit"`` a permit of exactly the same requests) that holds
    when the conditions ``around`` hold and the fact ``name`` is in one of ``runs``."""
    inside = " || ".join(
        f"({attribute('context.facts', name)} >= {first} && {attribute('context.facts', name)} <= {last})"
        for first, last in runs
    )
    when = " && ".join([*around, has("context.facts", name), f"({inside})"])
    return (
        f'@id("claim/{label}")\n'
        f"{effect} (principal, action == {NAMESPACE}::Action::{quoted(action)}, resource)\n"
        f"  when {{ {when} }};"
    )


def around(
    facts_names: List[str], example: dict, skip: str, settings: Optional[dict] = None
) -> Optional[List[str]]:
    """The conditions on the other facts (and, with the settings left open, on the settings) that hold in ``example``,
    for the facts and settings the forbids read: the case the range is stated in. None where one of them cannot be
    stated (a Period, a set, a record): a range that leaves it free would say more than the example shows, so none is
    given and the example stands."""
    conditions = []
    for fact in facts_names:
        if fact == skip:
            continue
        text = condition_on(fact, example.get("facts", {}).get(fact))
        if text is None:
            return None
        conditions.append(text)
    for domain, values in (settings or {}).items():
        record = attribute("context.settings", domain)
        for name, value in values.items():
            text = condition_on(name, value, record)
            if text is None:
                return None
            conditions.append(text)
    return conditions


def case_words(
    example: dict, names: List[str], skip: str, settings: Optional[dict] = None
) -> str:
    """The case in words: ``code = "ibi_urban"``, and the settings that are true, as ``capital``."""
    words = []
    for fact in names:
        if fact != skip and fact in example.get("facts", {}):
            value = example["facts"][fact]
            words.append(
                f'{fact} = "{value}"' if isinstance(value, str) else f"{fact} = {value}"
            )
    for values in (settings or {}).values():
        words += [
            f"{name}" if value is True else f"not {name}"
            for name, value in values.items()
            if isinstance(value, bool)
        ]
    return ", ".join(words)
