"""The generated forbids as the realm asks them (docs/codices/layered-codex.md §9.4).

A forbid names a Cedar action, an operation or ``<operation>.<list>``, and reads the facts of its request: for the
operation's own action, its arguments in GGG's form (``ggg_api.ggg_args``) and the facts it derives (``derived=`` on
``@op``); for a list's action, the fields of one element, one request per element. ``targets`` reads which actions the
generated policies name, and ``requests`` makes the requests one call needs.

The facts are those Cedar can compare (ggg_facts.py): text, whole numbers, booleans, periods and sets of text. A
Value, a blob, a reference or a record is not a fact, so a rule on one is a guard.
"""

from core import ranges
from core.db import Fail

LONG = 2**63  # Cedar's integers are 64-bit: -LONG <= n < LONG


def targets(policies, facts, reserved):
    """The Cedar actions the generated ``policies`` name, by operation: ``{"tax_policy_propose":
    ["tax_policy_propose.taxes"]}``. ``facts`` holds the actions the generator described, and ``reserved`` the
    operations no chapter may forbid (§10.4). Fail for what the generator must never write: a policy that names no
    action (it would forbid every operation), the action of a reserved operation, or an action without facts."""
    named = {}
    for policy in ("\n" + policies).split("\nforbid (")[1:]:
        head = policy.split("resource)", 1)[0]
        if "action ==" not in head and "action in [" not in head:
            raise Fail("a generated forbid names no action: it would forbid every operation")
        for part in head.split('Realm::Action::"')[1:]:
            action = part.split('"', 1)[0]
            operation = action.partition(".")[0]
            if operation in reserved:
                raise Fail(f"a generated forbid names {action}: no chapter may forbid {operation}")
            if action not in facts:
                raise Fail(f"a generated forbid names {action}, which has no facts")
            named.setdefault(operation, set()).add(action)
    return {operation: sorted(actions) for operation, actions in sorted(named.items())}


def requests(name, spec, args, proposal, targeted, types):
    """The Cedar requests the forbids on operation ``name`` need for a call with ``args``, as ``(action, label,
    facts)``: one for the operation's own action if a forbid names it, and one for each element of each list a forbid
    ranges over. ``targeted`` is ``targets``' answer and ``types`` the facts of each action in it, ``{fact: [type,
    optional]}``. ``proposal`` is the proposal whose replay the call is, for the facts the operation derives from it.
    A call that a forbid cannot judge, because its arguments do not fit GGG's types, is refused with Fail."""
    actions = targeted.get(name, ())
    if not actions:
        return
    ranges.check(name, args)  # the forbids assume the ranges GGG gives, which dispatch enforces after this
    given = facts_given(name, spec, args, proposal, types)
    for action in actions:
        if action == name:
            yield action, "its arguments", facts_of(types[action], given, name)
            continue
        field = action[len(name) + 1 :]
        for i, element in enumerate(elements(field, spec, args, given)):
            yield action, f"{field}[{i}]", facts_of(types[action], element, f"{name}: {field}[{i}]")


def facts_given(name, spec, args, proposal, types):
    """The facts of the operation's own action: its arguments in GGG's form and the facts it derives."""
    from ggg_api import ggg_args  # the realm's api modules import core.auth, so not before it is used

    try:
        given = ggg_args(name, args)
    except (ValueError, TypeError) as e:
        raise Fail(f"{name}: the arguments do not fit GGG's types: {e}")
    if not isinstance(given, dict):  # an operation of one bare argument has one fact, whatever its name
        given = dict(zip(types.get(name, ()), [given]))
    return {**given, **(spec["derived"](args, proposal) if spec["derived"] else {})}


def elements(field, spec, args, given):
    """The elements of the list ``field``: those the operation names, when they are not in its arguments (the saved
    policy's entries of ``tax_policy_propose``), and otherwise the argument's own."""
    source = spec["lists"].get(field)
    return source(args) if source else given.get(field) or []


def facts_of(types, record, where):
    """The facts of one request, from ``record`` (GGG's form): each fact of ``types`` as Cedar compares it. An
    optional fact the record lacks is left out; a required one it lacks is Fail."""
    if not isinstance(record, dict):
        raise Fail(f"{where} is not a record")
    facts = {}
    for fact, (kind, optional) in types.items():
        value = record.get(fact)
        if value is None:
            if not optional:
                raise Fail(f"{where}: {fact} is missing")
            continue
        facts[fact] = cedar_value(kind, value, f"{where}: {fact}")
    return facts


def cedar_value(kind, value, where):
    """``value`` as a Cedar value of type ``kind`` (a type of the fact schema), or Fail."""
    if kind == "String":
        return text_of(value, where)
    if kind == "Long":
        if isinstance(value, bool) or not isinstance(value, int):
            raise Fail(f"{where} is not a whole number")
        if not -LONG <= value < LONG:
            raise Fail(f"{where} does not fit a 64-bit integer")
        return value
    if kind == "Bool":
        if not isinstance(value, bool):
            raise Fail(f"{where} is not true or false")
        return value
    if kind == "Period":
        if not isinstance(value, dict):
            raise Fail(f"{where} is not a period")
        return {"count": cedar_value("Long", value.get("count"), where), "unit": text_of(value.get("unit"), where)}
    if kind == "Set<String>":
        if not isinstance(value, (list, tuple)):
            raise Fail(f"{where} is not a list")
        return sorted({text_of(v, where) for v in value})
    raise Fail(f"{where}: no Cedar value of type {kind}")


def text_of(value, where):
    """Text: a string, a principal, or the tag of a variant."""
    if isinstance(value, str):
        return str(value)
    if hasattr(value, "to_str"):
        return value.to_str()
    if isinstance(value, dict) and len(value) == 1:
        return next(iter(value))
    raise Fail(f"{where} is not text")
