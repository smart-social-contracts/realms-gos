"""The ``F`` expression language of forbids (layered-codex.md §6): the tree a chapter writes, and its
evaluation in plain Python.

A forbid's expression is built from ``F`` (the facts of the operation), ``S`` (the settings of the
chapter's own domain), literals (int, str, bool, lists of those, ``pct()`` rates and Periods) and
``when(c, a, b)``::

    ibi_urban_cap = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10") + when(S.capital, pct("0.07"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

The operators ``& | ~`` are and, or, not; ``== != < <= > >=`` compare; ``+ -`` are integer arithmetic.
Python's own ``and``, ``or``, ``not`` and chained comparisons cannot be redefined, so a node refuses to
become a bool (rule 2). Every node is immutable; the tree is data, read by ``cedar`` to write the
Cedar text and evaluated here for tests (rule 7).

**Polarity.** A Period comparison (``F.p > months(6)``) is true, in the forbid's sense, for a deadline
in another unit: it counts towards the forbid firing. The same holds for a comparison whose arithmetic
reads facts (``F.rate + F.surcharge > x``) when a fact lies outside the range the fact schema gives it
(rule 9: a forbid never errors, so the facts are bounded before the arithmetic runs). Under an odd
number of ``~`` such a node is written and evaluated the other way round, so that the ``~`` above it
still makes "another unit" or "out of range" count towards the forbid. ``evaluate`` carries that
polarity down the tree exactly as the Cedar generator writes it. Neither node can be the condition of
``when()``: inside arithmetic it has no polarity.
"""

from realms.legislation.errors import LegislationError
from realms.legislation.periods import Period
from realms.legislation.sealed import Sealed

I64_MIN, I64_MAX = -(2**63), 2**63 - 1
NO_PLAIN_BOOL = "use &, | and ~ in a forbid; see legislation/README.md"
COMPARISONS = ("==", "!=", "<", "<=", ">", ">=")
ORDERED = ("<", "<=", ">", ">=")


def fits(n):
    """Whether an integer is a Cedar Long (signed 64 bits)."""
    return I64_MIN <= n <= I64_MAX


def literal(value, what="a literal"):
    """``value`` as a Literal node, or a refusal: int, str, bool or Period only (rule 1), and an int
    that fits Cedar's 64-bit integer."""
    if isinstance(value, Expr):
        return value
    if isinstance(value, bool) or isinstance(value, (str, Period)):
        if isinstance(value, Period) and not fits(value.count):
            raise LegislationError(
                f"{value!r}: a Period's count must fit Cedar's 64-bit integer"
            )
        return Literal(value)
    if isinstance(value, int):
        if not fits(value):
            raise LegislationError(
                f"{value} does not fit Cedar's 64-bit integer (rule 1)"
            )
        return Literal(value)
    if value is None:
        raise LegislationError(
            f"{what} cannot be None: an optional fact is tested with is_none() or is_some()"
        )
    if isinstance(value, float):
        raise LegislationError(
            f'{value!r}: no floats in a forbid, Cedar has none; write a rate as pct("1.10") (rule 1)'
        )
    if isinstance(value, (list, tuple)):
        raise LegislationError(f"{what} is one value; a list belongs in F.x.in_([...])")
    raise LegislationError(
        f"{value!r} is not {what}: a forbid is built from F, S, when() and int, str, bool or Period literals"
    )


def condition(value):
    """``value`` as a node that can be a condition: an expression or a bool literal."""
    if isinstance(value, Expr):
        return value
    if isinstance(value, bool):
        return Literal(value)
    raise LegislationError(
        f"{value!r} is not a condition: write (F.a == 1) & (F.b == 2), with each comparison in parentheses"
    )


def values(items, what="in_()"):
    """A non-empty list of str or of int literals, as a Literal list node, or a Setting that holds one."""
    if isinstance(items, Setting):
        return items
    if not isinstance(items, (list, tuple)) or not items:
        raise LegislationError(f"{what} takes a non-empty list of values")
    kinds = {bool if isinstance(v, bool) else type(v) for v in items}
    if kinds not in ({str}, {int}):
        raise LegislationError(
            f"{what} takes a list of str or a list of int, not {list(items)!r}"
        )
    for v in items:
        literal(v, "a value")
    return Literal(list(items))


class Expr(metaclass=Sealed):
    """A node of a forbid's expression. The operators build the tree; nothing here evaluates."""

    __slots__ = ()

    def __setattr__(self, name, value):
        raise LegislationError("an expression never changes")

    def __bool__(self):
        raise LegislationError(NO_PLAIN_BOOL)

    def __and__(self, other):
        return And(self, condition(other))

    def __rand__(self, other):
        return And(condition(other), self)

    def __or__(self, other):
        return Or(self, condition(other))

    def __ror__(self, other):
        return Or(condition(other), self)

    def __invert__(self):
        return Not(self)

    def __eq__(self, other):
        return Compare("==", self, literal(other, "an operand"))

    def __ne__(self, other):
        return Compare("!=", self, literal(other, "an operand"))

    def __lt__(self, other):
        return Compare("<", self, literal(other, "an operand"))

    def __le__(self, other):
        return Compare("<=", self, literal(other, "an operand"))

    def __gt__(self, other):
        return Compare(">", self, literal(other, "an operand"))

    def __ge__(self, other):
        return Compare(">=", self, literal(other, "an operand"))

    def __add__(self, other):
        return Arith(self, "+", literal(other, "a term"))

    def __radd__(self, other):
        return Arith(literal(other, "a term"), "+", self)

    def __sub__(self, other):
        return Arith(self, "-", literal(other, "a term"))

    def __rsub__(self, other):
        return Arith(literal(other, "a term"), "-", self)

    def evaluate(self, facts, settings=None, ranges=None):
        """The tree's value for ``facts`` (the operation's args, or the list element's fields), with the
        ``S.`` settings' values and, for arithmetic on facts, the ``ranges`` of the fact schema
        (``{fact: (min, max)}``). Periods may be ``Period`` objects or ``{"count", "unit"}`` records, as
        GGG stores them; a fact that is ``None`` is absent."""
        return _eval(self, Env(facts, settings, ranges), False)


class Literal(Expr):
    __slots__ = ("value",)

    def __init__(self, value):
        object.__setattr__(self, "value", value)

    def __repr__(self):
        return repr(self.value)


class Fact(Expr):
    """``F.<name>``: a fact of the operation, or a field of the list element under ``each``."""

    __slots__ = ("name",)

    def __init__(self, name):
        object.__setattr__(self, "name", name)

    def __repr__(self):
        return f"F.{self.name}"

    def is_some(self):
        """The optional fact is present."""
        return Has(self)

    def is_none(self):
        """The optional fact is absent."""
        return Not(Has(self))

    def contains(self, value):
        """The set fact holds ``value``."""
        return Contains(self, literal(value, "a value"))

    def in_(self, items):
        """The fact is one of ``items``, a list of str or of int, or a list setting ``S.x``."""
        return In(self, values(items))

    def between(self, low, high):
        """``low <= fact <= high``, for an integer fact; the bounds are literals or settings."""
        return Between(self, literal(low, "a bound"), literal(high, "a bound"))


class Setting(Expr):
    """``S.<name>``: a setting of the chapter's own domain, as it resolves in this realm. The Cedar
    generator folds its value in, or leaves it open as ``context.settings`` in the generic mode.
    """

    __slots__ = ("name",)

    def __init__(self, name):
        object.__setattr__(self, "name", name)

    def __repr__(self):
        return f"S.{self.name}"


class Names(metaclass=Sealed):
    """``F`` and ``S``: any attribute is a node, named after the fact or the setting."""

    __slots__ = ("__node__",)

    def __init__(self, node):
        object.__setattr__(self, "__node__", node)

    def __setattr__(self, name, value):
        raise LegislationError("F and S never change")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__node__(name)


F = Names(Fact)
S = Names(Setting)


def _facts_in_arithmetic(node, inside):
    """The facts read inside arithmetic (a term of ``+``/``-`` or a branch of ``when``) below ``node``,
    in order of first appearance."""
    found = []
    if isinstance(node, Fact):
        if inside:
            found.append(node.name)
    elif isinstance(node, Arith):
        for _, term in node.terms:
            found.extend(_facts_in_arithmetic(term, True))
    elif isinstance(node, If):
        found.extend(_facts_in_arithmetic(node.then, True))
        found.extend(_facts_in_arithmetic(node.otherwise, True))
    out = []
    for name in found:
        if name not in out:
            out.append(name)
    return out


def _compares(node):
    """The comparisons in a condition, at any depth below ``&``, ``|`` and ``~``."""
    if isinstance(node, Compare):
        return [node]
    if isinstance(node, (And, Or)):
        return [c for child in node.children for c in _compares(child)]
    if isinstance(node, Not):
        return _compares(node.child)
    return []


class Compare(Expr):
    """``left <op> right``. ``period`` says whether a side is a Period literal (the rule names the unit);
    ``bounded`` lists the facts read inside arithmetic on either side, which the fact schema's ranges
    bound before the arithmetic runs (rule 9)."""

    __slots__ = ("op", "left", "right", "period", "bounded")

    def __init__(self, op, left, right):
        for side in (left, right):
            if isinstance(side, Literal) and isinstance(side.value, list):
                raise LegislationError(
                    "compare a fact with one value; a list belongs in F.x.in_([...])"
                )
        period = any(
            isinstance(s, Literal) and isinstance(s.value, Period)
            for s in (left, right)
        )
        bounded = _facts_in_arithmetic(left, False)
        bounded += [f for f in _facts_in_arithmetic(right, False) if f not in bounded]
        if period and bounded:
            raise LegislationError(
                "a Period is compared with a fact, not with arithmetic"
            )
        for name, value in (
            ("op", op),
            ("left", left),
            ("right", right),
            ("period", period),
            ("bounded", tuple(bounded)),
        ):
            object.__setattr__(self, name, value)

    def __repr__(self):
        return f"({self.left!r} {self.op} {self.right!r})"


class Arith(Expr):
    """A chain ``a + b - c``: ``terms`` is ``((" +", a), ("+", b), ("-", c))``. A chain written on the
    left of ``+`` or ``-`` grows (Python reads ``a + b + c`` as ``(a + b) + c``); one written on the
    right stays a term of its own, in parentheses."""

    __slots__ = ("terms",)

    def __init__(self, left, op, right):
        terms = left.terms if isinstance(left, Arith) else (("+", left),)
        object.__setattr__(self, "terms", terms + ((op, right),))

    def __repr__(self):
        first = repr(self.terms[0][1])
        return (
            "("
            + first
            + "".join(f" {op} {term!r}" for op, term in self.terms[1:])
            + ")"
        )


class And(Expr):
    """``a & b & c``: one chain, as Python reads it (left to right). A chain on the right of ``&``
    stays a group of its own, in parentheses."""

    __slots__ = ("children",)

    def __init__(self, left, right):
        children = left.children if isinstance(left, And) else (left,)
        object.__setattr__(self, "children", children + (right,))

    def __repr__(self):
        return "(" + " & ".join(repr(c) for c in self.children) + ")"


class Or(Expr):
    __slots__ = ("children",)

    def __init__(self, left, right):
        children = left.children if isinstance(left, Or) else (left,)
        object.__setattr__(self, "children", children + (right,))

    def __repr__(self):
        return "(" + " | ".join(repr(c) for c in self.children) + ")"


class Not(Expr):
    __slots__ = ("child",)

    def __init__(self, child):
        object.__setattr__(self, "child", child)

    def __repr__(self):
        return f"~{self.child!r}"


class If(Expr):
    """``when(c, a, b)``: ``if c then a else b``."""

    __slots__ = ("cond", "then", "otherwise")

    def __init__(self, cond, then, otherwise):
        cond = condition(cond)
        for compare in _compares(cond):
            if compare.period:
                raise LegislationError(
                    "a Period comparison can't be the condition of when(): inside arithmetic it has no polarity, "
                    "so another unit could raise a cap"
                )
            if compare.bounded:
                raise LegislationError(
                    "arithmetic on facts can't be compared in the condition of when(): it has no polarity there"
                )
        for name, value in (
            ("cond", cond),
            ("then", literal(then, "a branch of when()")),
            ("otherwise", literal(otherwise, "a branch of when()")),
        ):
            object.__setattr__(self, name, value)

    def __repr__(self):
        return f"when({self.cond!r}, {self.then!r}, {self.otherwise!r})"


class Has(Expr):
    """``F.x.is_some()``: the optional fact is present."""

    __slots__ = ("fact",)

    def __init__(self, fact):
        object.__setattr__(self, "fact", fact)

    def __repr__(self):
        return f"{self.fact!r}.is_some()"


class Contains(Expr):
    __slots__ = ("fact", "value")

    def __init__(self, fact, value):
        object.__setattr__(self, "fact", fact)
        object.__setattr__(self, "value", value)

    def __repr__(self):
        return f"{self.fact!r}.contains({self.value!r})"


class In(Expr):
    __slots__ = ("fact", "values")

    def __init__(self, fact, values):
        object.__setattr__(self, "fact", fact)
        object.__setattr__(self, "values", values)

    def __repr__(self):
        return f"{self.fact!r}.in_({self.values!r})"


class Between(Expr):
    __slots__ = ("fact", "low", "high")

    def __init__(self, fact, low, high):
        for bound in (low, high):
            if _facts_in_arithmetic(bound, True):
                raise LegislationError(
                    "between() takes literals and settings as bounds, not facts"
                )
        object.__setattr__(self, "fact", fact)
        object.__setattr__(self, "low", low)
        object.__setattr__(self, "high", high)

    def __repr__(self):
        return f"{self.fact!r}.between({self.low!r}, {self.high!r})"


def when(cond, then, otherwise=0):
    """``if cond then then else otherwise`` (layered-codex.md §6)."""
    return If(cond, then, otherwise)


# ── Evaluation in plain Python (rule 7) ───────────────────────────────────────


def as_period(value):
    """``(count, unit)`` of a Period, or of the record GGG stores (``{"count", "unit"}``); None for
    anything else. The unit stays the string it is: Cedar compares it as one, and any unit the rule
    does not name is "another unit"."""
    if isinstance(value, Period):
        return value.count, value.unit
    if isinstance(value, dict) and set(value) == {"count", "unit"}:
        return _int(value["count"], "a Period's count"), value["unit"]
    return None


class Env(metaclass=Sealed):
    __slots__ = ("facts", "settings", "ranges")

    def __init__(self, facts, settings, ranges):
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "settings", settings)
        object.__setattr__(self, "ranges", ranges)

    def fact(self, name):
        if self.facts.get(name) is None:
            raise LegislationError(
                f"F.{name} is absent: an optional fact is compared after is_some() in the same & chain (rule 3)"
            )
        return self.facts[name]

    def present(self, name):
        return self.facts.get(name) is not None

    def setting(self, name):
        if self.settings is None or self.settings.get(name) is None:
            raise LegislationError(
                f"S.{name}: evaluate needs the setting's value: evaluate(facts, settings)"
            )
        return self.settings[name]

    def in_range(self, name):
        if self.ranges is None or name not in self.ranges:
            raise LegislationError(
                f"F.{name} is read inside arithmetic: evaluate needs its range from the fact schema (rule 9)"
            )
        low, high = self.ranges[name]
        return low <= self.fact(name) <= high


def _int(value, where):
    if isinstance(value, bool) or not isinstance(value, int):
        raise LegislationError(f"{where} needs integers, not {value!r}")
    return value


def _eval(node, env, negated):
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Fact):
        return env.fact(node.name)
    if isinstance(node, Setting):
        return env.setting(node.name)
    if isinstance(node, Has):
        return env.present(node.fact.name)
    if isinstance(node, Not):
        return not _eval(node.child, env, not negated)
    if isinstance(node, And):
        return all(_eval(c, env, negated) for c in node.children)
    if isinstance(node, Or):
        return any(_eval(c, env, negated) for c in node.children)
    if isinstance(node, If):
        return _eval(
            node.then if _eval(node.cond, env, False) else node.otherwise, env, False
        )
    if isinstance(node, Arith):
        total = 0
        for op, term in node.terms:
            value = _int(_eval(term, env, False), "arithmetic")
            total = total + value if op == "+" else total - value
        return total
    if isinstance(node, Contains):
        return _eval(node.value, env, False) in env.fact(node.fact.name)
    if isinstance(node, In):
        return env.fact(node.fact.name) in _eval(node.values, env, False)
    if isinstance(node, Between):
        value = _int(env.fact(node.fact.name), "between()")
        return (
            _int(_eval(node.low, env, False), "between()")
            <= value
            <= _int(_eval(node.high, env, False), "between()")
        )
    if isinstance(node, Compare):
        return _compare(node, env, negated)
    raise LegislationError(f"{node!r} is not an expression")


def _compare(node, env, negated):
    left, right = _eval(node.left, env, False), _eval(node.right, env, False)
    periods = as_period(left), as_period(right)
    if periods[0] is not None or periods[1] is not None:
        if periods[0] is None or periods[1] is None:
            raise LegislationError(f"{node!r}: a Period compares with a Period")
        if periods[0][1] != periods[1][1]:
            return (
                not negated
            )  # another unit is true in the forbid's sense (§6, the Period row)
        left, right = periods[0][0], periods[1][0]
    elif node.bounded and not all(env.in_range(name) for name in node.bounded):
        return (
            not negated
        )  # a fact outside the schema's range makes the forbid fire (rule 9)
    elif node.op in ORDERED:
        left, right = _int(left, node.op), _int(right, node.op)
    elif type(left) is not type(right):
        raise LegislationError(
            f"{node!r} compares values of different kinds: {left!r} and {right!r}"
        )
    if node.op == "==":
        return left == right
    if node.op == "!=":
        return left != right
    if node.op == "<":
        return left < right
    if node.op == "<=":
        return left <= right
    if node.op == ">":
        return left > right
    return left >= right
