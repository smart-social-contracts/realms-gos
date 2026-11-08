"""The Cedar a forbid becomes (layered-codex.md §5.8, §6, §9.4): the fact schema the generator reads,
the checks on an ``F`` tree, the canonical text, and the schema the text validates against.

**The fact schema** is GGG's (ggg docs/layered-codex.md §3.3), as plain data, per Cedar action: the
operation's own action carries its args as facts, and ``<operation>.<list>`` the fields of one element
of that list, for forbids with ``each``. Each fact has a type, whether it is optional, and the range
GGG declares for it, where it has one::

    {"tax_policy_propose": {"facts": {"policy": "String"}},
     "tax_policy_propose.taxes": {"facts": {"code": "String",
                                            "rate": {"type": "Long", "optional": True, "min": 0, "max": 100000000}},
                                  "also": ["tax_policy_draft.taxes"]},
     "tax_policy_draft.taxes": {"facts": {...the same...}}}

``also`` names the actions a forbid on that action also applies to: the same forbids run when a draft
is saved (§6). The realm supplies the schema from GGG's args records (D4, #489).

**Canonical output** (rule 5): the same tree and the same settings give byte-identical text, on a
laptop and in the realm. ``&&`` and ``||`` chains at the top of the condition are broken one operand
per line; a group inside another is parenthesised and stays on one line; an arithmetic chain breaks
before each term when one of them is an ``if``; an ``if`` is always parenthesised; a Period or a
bounded comparison is a parenthesised group written by its polarity (expressions.py).

**Never errors** (rule 9): a comparison whose arithmetic reads facts is written with the facts bounded
by the schema's ranges first, and the generator checks with interval arithmetic that the sum of
in-range values cannot overflow. The generic output (settings left open as ``context.settings``, for
the analysis of §12.3) leaves a number setting's range to the analysis's assumptions, as R1 found; the
installed Cedar always folds the settings.
"""

from realms.legislation.errors import LegislationError, Refused
from realms.legislation.expressions import (
    And,
    Arith,
    Between,
    Compare,
    Contains,
    Fact,
    Has,
    If,
    In,
    Literal,
    Not,
    Or,
    Setting,
    _facts_in_arithmetic,
    condition,
    fits,
)
from realms.legislation.periods import Period
from realms.legislation.sealed import MappingProxyType, Sealed

NAMESPACE = "Realm"
TYPES = ("Long", "String", "Bool", "Period", "Set<String>", "Set<Long>")
# Words Cedar does not accept as a bare attribute name: written as context.facts["name"].
RESERVED = frozenset(
    ("true", "false", "if", "then", "else", "in", "like", "has", "is", "principal", "action",
     "resource", "context", "permit", "forbid", "when", "unless", "__cedar")
)  # fmt: skip
INDENT = " " * 9  # under "  when { "
TERM_INDENT = " " * 14
FLIPPED = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\0": "\\0",
}


def quoted(text):
    """A Cedar string literal, of an exact str: a subclass could iterate as it likes."""
    if type(text) is not str:
        raise LegislationError(
            f"{text!r} is not a string: its type is {type(text).__name__}"
        )
    out = []
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u{%x}" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def is_identifier(name):
    """A Cedar identifier that may follow a dot: letters, digits and _, not starting with a digit, no
    reserved word."""
    return (
        isinstance(name, str)
        and name != ""
        and all(c.isascii() and (c.isalnum() or c == "_") for c in name)
        and not name[0].isdigit()
        and name not in RESERVED
    )


def attribute(record, name):
    """``record.name``, or ``record["name"]`` when the name can't follow a dot."""
    return f"{record}.{name}" if is_identifier(name) else f"{record}[{quoted(name)}]"


def has(record, name):
    return (
        f"{record} has {name}"
        if is_identifier(name)
        else f"{record} has {quoted(name)}"
    )


def value_type(value, what):
    """The Cedar type of a literal or a folded setting's value, or a refusal. Exact types only: an
    int or str subclass with a ``__str__`` or ``__iter__`` of its own would write Cedar of its own.
    """
    kind = type(value)
    if kind is bool:
        return "Bool"
    if kind is int:
        if not fits(value):
            raise LegislationError(
                f"{what} = {value}: does not fit Cedar's 64-bit integer"
            )
        return "Long"
    if kind is str:
        return "String"
    if kind is Period:
        return "Period"
    if kind in (list, tuple):
        kinds = {value_type(v, what) for v in value}
        if kinds not in ({"String"}, {"Long"}):
            raise LegislationError(
                f"{what}: a list is of strings or of integers, not {list(value)!r}"
            )
        return "Set<%s>" % kinds.pop()
    if value is None:
        raise LegislationError(f"{what} is not set in this realm (None)")
    if kind in (dict, MappingProxyType):
        raise LegislationError(
            f"{what} is a dict: a forbid reads a scalar, a list or a Period setting"
        )
    raise LegislationError(
        f"{what} = {value!r}: not a value a forbid can read, its type is {kind.__name__}"
    )


class FactType(metaclass=Sealed):
    """One fact of an action: its Cedar type, whether it is optional, and ``(min, max)`` or None."""

    __slots__ = ("type", "optional", "range")

    def __init__(self, action, name, entry):
        if isinstance(entry, str):
            entry = {"type": entry}
        where = f"fact schema, {action}.{name}"
        if not isinstance(entry, dict) or set(entry) - {
            "type",
            "optional",
            "min",
            "max",
        }:
            raise LegislationError(
                f"{where}: a fact is a type name or {{type, optional, min, max}}"
            )
        kind = entry.get("type")
        if kind not in TYPES:
            raise LegislationError(f"{where}: {kind!r} is not one of {TYPES}")
        optional = entry.get("optional", False)
        if not isinstance(optional, bool):
            raise LegislationError(f"{where}: optional is true or false")
        bounds = (entry.get("min"), entry.get("max"))
        if bounds != (None, None):
            if kind != "Long" or any(
                isinstance(b, bool) or not isinstance(b, int) or not fits(b)
                for b in bounds
            ):
                raise LegislationError(
                    f"{where}: min and max bound a Long, both given, within Cedar's 64-bit integer"
                )
            if bounds[0] > bounds[1]:
                raise LegislationError(
                    f"{where}: min {bounds[0]} is above max {bounds[1]}"
                )
        object.__setattr__(self, "type", kind)
        object.__setattr__(self, "optional", optional)
        object.__setattr__(self, "range", None if bounds == (None, None) else bounds)

    def __setattr__(self, name, value):
        raise LegislationError("the fact schema never changes")


class Action(metaclass=Sealed):
    """One Cedar action: its facts by name, and the actions the same forbids also apply to."""

    __slots__ = ("name", "facts", "also")

    def __init__(self, name, entry):
        if (
            not isinstance(name, str)
            or not all(is_identifier(p) for p in name.split("."))
            or name.count(".") > 1
        ):
            raise LegislationError(
                f"fact schema: {name!r} is not an action name (<operation> or <operation>.<list>)"
            )
        if (
            not isinstance(entry, dict)
            or set(entry) - {"facts", "also"}
            or not isinstance(entry.get("facts"), dict)
        ):
            raise LegislationError(
                f"fact schema, {name}: an action is {{facts: {{...}}, also: [...]}}"
            )
        also = entry.get("also", [])
        if not isinstance(also, (list, tuple)) or not all(
            isinstance(a, str) for a in also
        ):
            raise LegislationError(f"fact schema, {name}: also lists action names")
        facts = {}
        for fact in sorted(entry["facts"]):
            if not isinstance(fact, str) or not fact:
                raise LegislationError(f"fact schema, {name}: a fact has a name")
            facts[fact] = FactType(name, fact, entry["facts"][fact])
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "facts", MappingProxyType(facts))
        object.__setattr__(self, "also", tuple(also))

    def __setattr__(self, name, value):
        raise LegislationError("the fact schema never changes")

    def ranges(self):
        return {
            name: fact.range
            for name, fact in self.facts.items()
            if fact.range is not None
        }

    def shape(self):
        return [(name, f.type, f.optional, f.range) for name, f in self.facts.items()]


class FactSchema(metaclass=Sealed):
    """GGG's fact schema as the generator reads it: ``actions`` by name (see the module docstring)."""

    __slots__ = ("actions",)

    def __init__(self, data):
        if not isinstance(data, dict):
            raise LegislationError("the fact schema is a dict of actions")
        actions = {name: Action(name, data[name]) for name in sorted(data)}
        for action in actions.values():
            for other in action.also:
                if other not in actions:
                    raise LegislationError(
                        f"fact schema, {action.name}: also names {other!r}, which is not an action"
                    )
                if actions[other].shape() != action.shape():
                    raise LegislationError(
                        f"fact schema, {action.name}: {other} runs the same forbids, so it has the same facts"
                    )
        object.__setattr__(self, "actions", MappingProxyType(actions))

    def __setattr__(self, name, value):
        raise LegislationError("the fact schema never changes")

    def action_of(self, op, each):
        """The Cedar action a forbid names: the operation, or ``<operation>.<list>`` under ``each``."""
        return f"{op}.{each}" if each else op


def settings_used(node, found=None):
    """The ``S.`` names a tree reads, in order of first appearance."""
    found = [] if found is None else found
    if isinstance(node, Setting):
        if node.name not in found:
            found.append(node.name)
    for child in children(node):
        settings_used(child, found)
    return found


def children(node):
    if isinstance(node, (And, Or)):
        return node.children
    if isinstance(node, Not):
        return (node.child,)
    if isinstance(node, Compare):
        return (node.left, node.right)
    if isinstance(node, Arith):
        return tuple(term for _, term in node.terms)
    if isinstance(node, If):
        return (node.cond, node.then, node.otherwise)
    if isinstance(node, (Has,)):
        return (node.fact,)
    if isinstance(node, Contains):
        return (node.fact, node.value)
    if isinstance(node, In):
        return (node.fact, node.values)
    if isinstance(node, Between):
        return (node.fact, node.low, node.high)
    return ()


def established(node):
    """The facts an ``&`` chain establishes as present for what follows it: its ``is_some()`` members,
    through nested ``&`` groups (Cedar's validator reasons the same way)."""
    if isinstance(node, Has):
        return {node.fact.name}
    if isinstance(node, And):
        out = set()
        for child in node.children:
            out |= established(child)
        return out
    return set()


class Check(metaclass=Sealed):
    """An ``F`` tree against one action's facts and the chapter's settings: types, rule 3 (optional
    facts after ``is_some()``), rule 9 (fact arithmetic bounded and unable to overflow), and what
    ``when()`` may hold. ``typeof`` returns the node's Cedar type or raises ``LegislationError``. The
    checks are those of the installed (folded) Cedar; the generic output reads the same tree.
    """

    __slots__ = ("action", "settings", "domain")

    def __init__(self, action, settings, domain):
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "settings", settings)
        object.__setattr__(self, "domain", domain)

    def __setattr__(self, name, value):
        raise LegislationError("a check never changes")

    def run(self, tree):
        if self.typeof(tree, frozenset(), False) != "Bool":
            raise LegislationError(
                "a forbid's expression is a condition, true when the operation is forbidden"
            )

    def fact(self, node, present):
        kind = self.action.facts.get(node.name)
        if kind is None:
            raise LegislationError(
                f"F.{node.name}: {self.action.name} has no fact {node.name}"
            )
        if kind.optional and node.name not in present:
            raise LegislationError(
                f"F.{node.name} is optional: compare it after F.{node.name}.is_some() in the same & chain (rule 3)"
            )
        return kind.type

    def setting(self, node):
        if node.name not in self.settings:
            raise LegislationError(
                f"S.{node.name}: {self.domain} has no setting {node.name}"
            )
        return value_type(self.settings[node.name], f"S.{node.name}")

    def typeof(self, node, present, in_when):
        if isinstance(node, Literal):
            return value_type(node.value, repr(node.value))
        if isinstance(node, Fact):
            return self.fact(node, present)
        if isinstance(node, Setting):
            return self.setting(node)
        if isinstance(node, Has):
            if node.fact.name not in self.action.facts:
                raise LegislationError(
                    f"F.{node.fact.name}: {self.action.name} has no fact {node.fact.name}"
                )
            return "Bool"
        if isinstance(node, Not):
            self.boolean(node.child, present, in_when)
            return "Bool"
        if isinstance(node, And):
            known = set(present)
            for child in node.children:
                self.boolean(child, frozenset(known), in_when)
                known |= established(child)
            return "Bool"
        if isinstance(node, Or):
            for child in node.children:
                self.boolean(child, present, in_when)
            return "Bool"
        if isinstance(node, If):
            self.boolean(node.cond, present, True)
            self.long(node.then, present | established(node.cond), in_when, "when()")
            self.long(node.otherwise, present, in_when, "when()")
            return "Long"
        if isinstance(node, Arith):
            for _, term in node.terms:
                self.long(term, present, in_when, "arithmetic")
            return "Long"
        if isinstance(node, Contains):
            if not isinstance(node.value, (Literal, Setting)):
                raise LegislationError("contains() takes a literal or a setting")
            kind = self.fact(node.fact, present)
            if not kind.startswith("Set<"):
                raise LegislationError(
                    f"F.{node.fact.name}.contains(): the fact is a {kind}, not a set"
                )
            self.same(
                self.typeof(node.value, present, in_when),
                kind[4:-1],
                f"F.{node.fact.name}.contains()",
            )
            return "Bool"
        if isinstance(node, In):
            if not isinstance(node.values, (Literal, Setting)):
                raise LegislationError("in_() takes a list of values or a list setting")
            kind = self.fact(node.fact, present)
            if kind not in ("String", "Long"):
                raise LegislationError(
                    f"F.{node.fact.name}.in_(): the fact is a {kind}, not a string or an integer"
                )
            self.same(
                self.typeof(node.values, present, in_when),
                f"Set<{kind}>",
                f"F.{node.fact.name}.in_()",
            )
            return "Bool"
        if isinstance(node, Between):
            self.same(
                self.fact(node.fact, present), "Long", f"F.{node.fact.name}.between()"
            )
            for bound in (node.low, node.high):
                if _facts_in_arithmetic(bound, True):
                    raise LegislationError(
                        "between() takes literals and settings as bounds, not facts"
                    )
                self.long(bound, present, in_when, "between()")
            return "Bool"
        if isinstance(node, Compare):
            return self.compare(node, present, in_when)
        raise LegislationError(f"{node!r} is not an expression")

    def boolean(self, node, present, in_when):
        kind = self.typeof(node, present, in_when)
        if kind != "Bool":
            raise LegislationError(f"{node!r} is a {kind}, not a condition")

    def long(self, node, present, in_when, where):
        """An integer expression that cannot overflow: every bound, term and branch goes through here."""
        kind = self.typeof(node, present, in_when)
        if kind != "Long":
            raise LegislationError(f"{where} needs integers: {node!r} is a {kind}")
        self.interval(node)

    @staticmethod
    def same(got, wanted, where):
        if got != wanted:
            raise LegislationError(f"{where}: {wanted} and {got} do not compare")

    def compare(self, node, present, in_when):
        left, right = self.typeof(node.left, present, in_when), self.typeof(
            node.right, present, in_when
        )
        if "Period" in (left, right):
            fact, other = (
                (node.left, node.right)
                if isinstance(node.left, Fact)
                else (node.right, node.left)
            )
            if not (
                left == right == "Period"
                and isinstance(fact, Fact)
                and (
                    isinstance(other, Setting)
                    or (isinstance(other, Literal) and isinstance(other.value, Period))
                )
            ):
                raise LegislationError(
                    f"{node!r}: a Period fact compares with the Period the rule names, months(6), or a Period setting"
                )
            if in_when:
                raise LegislationError(
                    "a Period comparison can't be the condition of when(): it has no polarity there"
                )
            return "Bool"
        if node.op in ("==", "!=") and left.startswith("Set<"):
            raise LegislationError(f"{node!r}: a set fact is tested with contains()")
        if "Bool" in (left, right):
            # A Bool fact or setting against true or false, and nothing else: a condition is never an
            # operand (expressions.py), so every condition keeps its polarity.
            sides = (node.left, node.right)
            if not (
                left == right == "Bool"
                and node.op in ("==", "!=")
                and any(isinstance(s, Literal) for s in sides)
                and any(isinstance(s, (Fact, Setting)) for s in sides)
            ):
                raise LegislationError(
                    f"{node!r}: a Bool fact or setting compares with True or False only"
                )
            return "Bool"
        if node.op not in ("==", "!=") or node.bounded:
            self.same(left, "Long", repr(node))
            self.same(right, "Long", repr(node))
        self.same(right, left, repr(node))
        if node.bounded:
            if in_when:
                raise LegislationError(
                    "arithmetic on facts can't be compared in the condition of when()"
                )
            for name in node.bounded:
                if self.action.facts[name].range is None:
                    raise LegislationError(
                        f"F.{name} is read inside arithmetic, but the fact schema gives it no range: "
                        f"the forbid could overflow (rule 9)"
                    )
                if self.action.facts[name].optional and name not in present:
                    # The bounds are written before the comparison, outside any `if` that
                    # established the fact: strict validation would refuse them too.
                    raise LegislationError(
                        f"F.{name} is optional: compare it after F.{name}.is_some() in the same & chain (rule 3)"
                    )
        self.interval(node.left)
        self.interval(node.right)
        return "Bool"

    def interval(self, node):
        """``(low, high)`` of an integer expression within the fact schema's ranges, refusing any step
        that could overflow Cedar's 64-bit integer; None for a bare fact with no range (no arithmetic
        reads it, or the compare check refused it already)."""
        if isinstance(node, Literal):
            return (node.value, node.value)
        if isinstance(node, Fact):
            return self.action.facts[node.name].range
        if isinstance(node, Setting):
            value = self.settings[node.name]
            return (value, value)
        if isinstance(node, If):
            then, otherwise = self.interval(node.then), self.interval(node.otherwise)
            if then is None or otherwise is None:
                return None
            return (min(then[0], otherwise[0]), max(then[1], otherwise[1]))
        if isinstance(node, Arith):
            total = None
            for op, term in node.terms:
                span = self.interval(term)
                if span is None:
                    return None
                if total is None:
                    total = span
                elif op == "+":
                    total = (total[0] + span[0], total[1] + span[1])
                else:
                    total = (total[0] - span[1], total[1] - span[0])
                if not (fits(total[0]) and fits(total[1])):
                    raise LegislationError(
                        f"{node!r} may overflow Cedar's 64-bit integer even within the fact schema's ranges (rule 9)"
                    )
            return total
        return None


class Writer(metaclass=Sealed):
    """The canonical Cedar text of one tree, with the settings folded in, or left open as
    ``context.settings.<domain>.<name>`` in the generic mode."""

    __slots__ = ("facts", "settings", "domain", "generic")

    def __init__(self, action, settings, domain, generic):
        object.__setattr__(self, "facts", action.facts)
        object.__setattr__(self, "settings", settings)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "generic", generic)

    def __setattr__(self, name, value):
        raise LegislationError("a writer never changes")

    def top(self, node):
        """The ``when`` body: a chain one operand per line, anything else as it is."""
        if isinstance(node, (And, Or)):
            joiner = "\n" + INDENT + ("&& " if isinstance(node, And) else "|| ")
            return joiner.join(self.expr(child, False, True) for child in node.children)
        return self.expr(node, False, False)

    # ── values ──

    def fact(self, name):
        return attribute("context.facts", name)

    def setting(self, name):
        if self.generic:
            return attribute(attribute("context.settings", self.domain), name)
        return self.literal(self.settings[name])

    @staticmethod
    def literal(value):
        """The Cedar text of an exact bool, int, str or tuple; never ``str()`` of an unknown object."""
        kind = type(value)
        if kind is bool:
            return "true" if value else "false"
        if kind is int:
            return int.__repr__(value)
        if kind is str:
            return quoted(value)
        if kind in (list, tuple):
            return "[" + ",".join(Writer.literal(v) for v in value) + "]"
        raise LegislationError(
            f"{value!r} has no Cedar literal: its type is {kind.__name__}"
        )

    def period(self, node):
        """The ``unit`` and ``count`` texts of the Period a rule names: a literal, or an open setting."""
        if isinstance(node, Literal):
            return quoted(node.value.unit), Writer.literal(node.value.count)
        if self.generic:
            path = attribute(attribute("context.settings", self.domain), node.name)
            return f"{path}.unit", f"{path}.count"
        value = self.settings[node.name]
        return quoted(value.unit), Writer.literal(value.count)

    def value(self, node, negated):
        """An operand: a fact, a setting, a literal, an ``if`` in parentheses, or a chain."""
        if isinstance(node, Fact):
            return self.fact(node.name)
        if isinstance(node, Setting):
            return self.setting(node.name)
        if isinstance(node, Literal):
            return self.literal(node.value)
        if isinstance(node, If):
            return (
                f"(if {self.expr(node.cond, False, False)} then {self.value(node.then, False)}"
                f" else {self.value(node.otherwise, False)})"
            )
        if isinstance(node, Arith):
            return self.chain(node)
        return self.expr(node, negated, True)

    def chain(self, node, inline=False):
        """``a + b - c``; each term on its own line when one of them is an ``if``, unless inline."""
        terms = [self.term(term) for _, term in node.terms]
        broken = not inline and any(isinstance(term, If) for _, term in node.terms)
        between = "\n" + TERM_INDENT if broken else " "
        return terms[0] + "".join(
            f"{between}{op} {text}" for (op, _), text in zip(node.terms[1:], terms[1:])
        )

    def term(self, node):
        if isinstance(node, Arith):
            return "(" + self.chain(node, inline=True) + ")"
        return self.value(node, False)

    # ── conditions ──

    def expr(self, node, negated, nested):
        """A condition. ``negated`` is its polarity, the parity of the ``~`` above it; ``nested`` says
        it sits inside another group, so a group gets parentheses."""
        if isinstance(node, (And, Or)):
            text = (" && " if isinstance(node, And) else " || ").join(
                self.expr(child, negated, True) for child in node.children
            )
            return self.group(text, nested)
        if isinstance(node, Not):
            child = node.child
            inner = self.expr(child, not negated, False)
            return "!" + (
                inner if isinstance(child, (Fact, Setting, Literal)) else f"({inner})"
            )
        if isinstance(node, Has):
            return has("context.facts", node.fact.name)
        if isinstance(node, Contains):
            return (
                f"{self.fact(node.fact.name)}.contains({self.value(node.value, False)})"
            )
        if isinstance(node, In):
            return f"{self.value(node.values, False)}.contains({self.fact(node.fact.name)})"
        if isinstance(node, Between):
            fact = self.fact(node.fact.name)
            return self.group(
                f"{fact} >= {self.value(node.low, False)} && {fact} <= {self.value(node.high, False)}",
                nested,
            )
        if isinstance(node, Compare):
            return self.compare(node, negated, nested)
        return self.value(node, negated)

    @staticmethod
    def group(text, nested):
        return f"({text})" if nested else text

    def is_period(self, node):
        """A Period comparison: against the Period the rule names, or against a Period setting."""
        return node.period or any(
            isinstance(side, Setting) and isinstance(self.settings[side.name], Period)
            for side in (node.left, node.right)
        )

    def compare(self, node, negated, nested):
        if self.is_period(node):
            fact, other, op = node.left, node.right, node.op
            if not isinstance(fact, Fact):
                fact, other, op = node.right, node.left, FLIPPED[node.op]
            path = self.fact(fact.name)
            unit, count = self.period(other)
            if negated:
                text = f"{path}.unit == {unit} && {path}.count {op} {count}"
            else:
                text = f"{path}.unit != {unit} || {path}.count {op} {count}"
            return self.group(text, nested)
        comparison = (
            f"{self.value(node.left, False)} {node.op} {self.value(node.right, False)}"
        )
        if not node.bounded:
            return comparison
        bounds = []
        for name in node.bounded:
            low, high = self.facts[name].range
            path = self.fact(name)
            if negated:
                bounds += [f"{path} >= {low}", f"{path} <= {high}"]
            else:
                bounds += [f"{path} < {low}", f"{path} > {high}"]
        joiner = " && " if negated else " || "
        return self.group(joiner.join(bounds + [comparison]), nested)


class BoundForbid(metaclass=Sealed):
    """A forbid bound to a realm: its ``@id`` (``<source>/<domain>/<attribute>``), citation, Cedar
    actions, tree, the values of the settings it reads, and the ranges of the facts it bounds.
    """

    __slots__ = (
        "id",
        "legislation",
        "actions",
        "expression",
        "settings",
        "action",
        "domain",
        "file",
        "line",
    )

    def __init__(self, member, action, actions, expression, settings):
        for name, value in (
            ("id", f"{member.source}/{member.domain}/{member.name}"),
            ("legislation", member.legislation),
            ("actions", tuple(actions)),
            ("expression", expression),
            ("settings", MappingProxyType(dict(settings))),
            ("action", action),
            ("domain", member.domain),
            ("file", member.file),
            ("line", member.line),
        ):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise LegislationError("a bound forbid never changes")

    def evaluate(self, facts, settings=None):
        """Whether the forbid fires on ``facts`` (plain Python, rule 7); ``settings`` replaces the
        realm's values, for the generic mode."""
        return self.expression.evaluate(
            facts,
            dict(self.settings) if settings is None else settings,
            self.action.ranges(),
        )

    def cedar(self, generic=False):
        """The canonical Cedar policy (§5.8)."""
        lines = [f"@id({quoted(self.id)})"]
        if self.legislation is not None:
            lines.append(f"@legislation({quoted(self.legislation)})")
        actions = [f"{NAMESPACE}::Action::{quoted(a)}" for a in self.actions]
        if len(actions) == 1:
            lines.append(f"forbid (principal, action == {actions[0]}, resource)")
        else:
            lines.append(
                "forbid (principal,\n        action in ["
                + ",\n                   ".join(actions)
                + "],\n        resource)"
            )
        body = Writer(self.action, self.settings, self.domain, generic).top(
            self.expression
        )
        lines.append(f"  when {{ {body} }};")
        return "\n".join(lines)


def plain_setting(value):
    """A frozen setting value as the generator reads it: a tuple for a list, a Period kept; the
    exact types only (``value_type`` refuses the rest when the forbid is checked)."""
    return tuple(value) if type(value) is list else value


def bind(member, resolved, schema):
    """``member`` (a forbid of a loaded chapter) bound to the realm: its settings read from
    ``resolved`` (the lowest class of its domain) and its tree checked against the fact schema, for
    the folded and the generic output alike. Raises ``Refused`` with the chapter's file and line.
    """
    forbid = member.value
    name = schema.action_of(forbid.op, forbid.each)
    try:
        action = schema.actions.get(name)
        if action is None:
            raise LegislationError(
                f'each="{forbid.each}" names no list of {forbid.op}: the fact schema has no {name}'
                if forbid.each
                else f"the fact schema has no facts for {forbid.op}"
            )
        expression = condition(forbid.expression)
        settings = {}
        for setting in settings_used(expression):
            upper = resolved.__members__.get(setting)
            if upper is None or upper.kind != "setting":
                raise LegislationError(
                    f"S.{setting}: {member.domain} has no setting {setting}"
                )
            settings[setting] = plain_setting(getattr(resolved, setting))
        Check(action, settings, member.domain).run(expression)
    except LegislationError as e:
        raise Refused(f"{member.name}: {e}", member.line, member.file)
    return BoundForbid(member, action, (name,) + action.also, expression, settings)


def policies(forbids, generic=False):
    """The policy set of every bound forbid, in their order, one blank line between policies. Two
    forbids never share an ``@id``."""
    seen = set()
    for forbid in forbids:
        if forbid.id in seen:
            raise LegislationError(f"two forbids share the id {forbid.id}")
        seen.add(forbid.id)
    return "\n\n".join(f.cedar(generic) for f in forbids) + ("\n" if forbids else "")


def setting_types(forbids):
    """Per action, per domain, the Cedar type of each setting an open (generic) forbid reads."""
    out = {}
    for forbid in forbids:
        for action in forbid.actions:
            for name, value in forbid.settings.items():
                domains = out.setdefault(action, {})
                domains.setdefault(forbid.domain, {})[name] = value_type(
                    value, f"S.{name}"
                )
    return out


def record(fields):
    """``{ a: T, b?: U }`` from ``[(name, type, optional)]``; a name Cedar reserves is quoted."""
    if not fields:
        return "{}"
    names = (n if is_identifier(n) else quoted(n) for n, _, _ in fields)
    return (
        "{ "
        + ", ".join(
            f"{n}{'?' if o else ''}: {t}" for n, (_, t, o) in zip(names, fields)
        )
        + " }"
    )


def schema(fact_schema, forbids=(), context=None):
    """The Cedar schema the generated policies validate against: one action per entry of the fact
    schema, each ``in [call]``, its context the ``context`` fields (the realm's, as core/auth.py
    declares them) plus ``facts``; with ``forbids`` (the generic mode) also ``settings``, typed from
    the values the forbids read."""
    base = [(name, kind, False) for name, kind in sorted((context or {}).items())]
    open_settings = setting_types(forbids)
    lines = [
        f"namespace {NAMESPACE} {{",
        "  entity User = { permissions: Set<String>, banned: Bool };",
        "  entity Realm;",
        "  type Period = { count: Long, unit: String };",
        f'  action "call" appliesTo {{ principal: [User], resource: [Realm], context: {record(base)} }};',
    ]
    for name, action in fact_schema.actions.items():
        facts = record([(f, t.type, t.optional) for f, t in action.facts.items()])
        fields = base + [("facts", facts, False)]
        if name in open_settings:
            domains = [
                (
                    domain,
                    record([(s, t, False) for s, t in sorted(types.items())]),
                    False,
                )
                for domain, types in sorted(open_settings[name].items())
            ]
            fields.append(("settings", record(domains), False))
        lines.append(
            f'  action {quoted(name)} in ["call"] appliesTo {{ principal: [User], resource: [Realm], '
            f"context: {record(fields)} }};"
        )
    lines.append("}")
    return "\n".join(lines) + "\n"
