"""The generated forbids as the realm asks them (docs/codices/layered-codex.md §9.4).

A forbid names a Cedar action, an operation or ``<operation>.<list>``, and reads the facts of its request: for the
operation's own action, its arguments in GGG's form (``ggg_api.ggg_args``) and the facts it derives (``derived=`` on
``@op``); for a list's action, the fields of one element, one request per element. ``targets`` reads which actions the
generated policies name, and ``requests`` makes the requests one call needs.

The facts are those Cedar can compare or test (ggg_facts.py): text, whole numbers, booleans, periods, sets of text,
and records, which a forbid can only test for. A Value, a blob or a list of records is not a fact, so a rule on one
is a guard.
"""

from core import ranges
from core.db import Fail

LONG = 2**63  # Cedar's integers are 64-bit: -LONG <= n < LONG
# The most elements of a list that the forbids judge in one call, a request each. The call that would pass it is
# refused (invalid_argument) where the list is given, at draft or at propose, so that no replay can be asked for more.
MAX_ELEMENTS = 500
# What the generator writes outside its strings, and nothing else: words, spaces, line breaks and Cedar's punctuation.
# Not a `/`, so no comment of either kind; not a `\r`, which ends a comment as `\n` does; not a `#`, a backslash or any
# non-ASCII character. Whatever else is there, Cedar's lexer could read it differently from this check.
BARE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ \n(){}[],;.=<>!&|+-:@")
# Everything in the punctuation of BARE, for finding the words outside strings.
PUNCTUATION = str.maketrans({c: " " for c in "(){}[],;.=<>!&|+-:@"})


def strings_of(text):
    """``(outside, inside)``: the pieces of ``text`` outside its Cedar strings and the contents of the strings. A string
    ends at a quote that an even number of backslashes (none, or pairs) precedes; Cedar's lexer reads it so. Fail when
    one does not end."""
    outside, inside, start = [], [], 0
    while True:
        opening = text.find('"', start)
        if opening < 0:
            outside.append(text[start:])
            return outside, inside
        outside.append(text[start:opening])
        end = opening
        while True:
            end = text.find('"', end + 1)
            if end < 0:
                raise Fail("a generated policy has a string that does not close")
            slashes = 0
            while text[end - 1 - slashes] == "\\" and end - 1 - slashes > opening:
                slashes += 1
            if slashes % 2 == 0:
                break
        inside.append(text[opening + 1 : end])
        start = end + 1


def only_forbids(policies):
    """The heads of the generated ``policies``, as the text after each ``forbid (`` at the start of a line. Fail unless
    every policy is a forbid written that way: the realm loads this text into its own policy set, so a ``permit`` in
    it would grant what the realm never did. What the sandbox hands over is read, not trusted, and this is a grammar
    the generator writes and no more: outside the strings only the characters of BARE; inside them no control
    character (the generator escapes those); no word ``permit``; as many ``;`` and ``forbid`` words as heads, and a
    head at the start of a line, which a string cannot hold."""
    outside, inside = strings_of(policies)
    bare = " ".join(outside)
    if not set(bare) <= BARE:
        raise Fail("a generated policy has a character the generator never writes outside a string")
    if any(c < " " or c == "\x7f" for c in set("".join(inside))):
        raise Fail("a generated policy has a control character in a string")
    words = bare.translate(PUNCTUATION).split()
    heads = ("\n" + policies).split("\nforbid (")[1:]
    if "permit" in words:
        raise Fail("a generated policy permits: the legislation may only forbid")
    if not len(heads) == words.count("forbid") == bare.count(";"):
        raise Fail("a generated policy is not a forbid written as the generator writes one")
    return heads


def head_of(actions):
    """The text of a generated forbid after ``forbid (`` up to its condition: the scope the generator writes for
    ``actions``, with the principal and the resource unconstrained."""
    named = [f'Realm::Action::"{action}"' for action in actions]
    if len(named) == 1:
        return f"principal, action == {named[0]}, resource)"
    return "principal,\n        action in [" + ",\n                   ".join(named) + "],\n        resource)"


def names_principal_or_resource(policy, head):
    """Whether the condition of a generated forbid, the text of ``policy`` after its ``head``, uses the word
    ``principal`` or ``resource`` outside a string. The scope keeps them unconstrained, so a condition that names one
    would reach one person again; the generator never writes either (``F`` has no way to name them, and a fact called
    so is written as ``context.facts["principal"]``)."""
    outside, _ = strings_of(policy[len(head):])
    return not {"principal", "resource"}.isdisjoint(" ".join(outside).translate(PUNCTUATION).split())


def targets(policies, facts, operations, reserved):
    """The Cedar actions the generated ``policies`` name, by operation: ``{"tax_policy_propose":
    ["tax_policy_propose.taxes"]}``. ``operations`` are the realm's own, which no one else's word replaces, ``facts``
    the actions the generator described, and ``reserved`` the operations no chapter may forbid (§10.4). Fail for what
    the generator must never write: anything but forbids, a scope other than the one it writes (a forbid of one
    person, of `call` itself, of an action that is no operation of this realm, of a reserved operation) or a
    condition that names the principal or the resource, or an action without facts."""
    named = {}
    for policy in only_forbids(policies):
        actions = [part.split('"', 1)[0] for part in policy.split("resource)", 1)[0].split('Realm::Action::"')[1:]]
        if not actions or not policy.startswith(head_of(actions)):
            raise Fail("a generated forbid is not scoped as the generator writes one: it would reach further than "
                       "the operations it names")
        if names_principal_or_resource(policy, head_of(actions)):
            raise Fail("a generated forbid names the principal or the resource in its condition, which the generator "
                       "never writes: it would reach one person")
        for action in actions:
            operation, _, field = action.partition(".")
            if operation not in operations or "." in field or action.endswith("."):
                raise Fail(f"a generated forbid names {action}, which is no operation of this realm")
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
        found = elements(field, spec, args, given)
        if len(found) > MAX_ELEMENTS:
            raise Fail(f"{name}: {field} has {len(found)} elements; the legislation's forbids judge at most "
                       f"{MAX_ELEMENTS} in a call")
        for i, element in enumerate(found):
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
    if kind == "Record":  # a forbid can only test that it is there
        if not isinstance(value, dict):
            raise Fail(f"{where} is not a record")
        return {}
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
