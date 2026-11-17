"""Marks on a chapter's members, and forbids (layered-codex.md §4.1, §4.3, §6).

A mark says what lower layers may do with a member: ``fixed`` (only this definition; a lower layer
may not define the name), ``extension`` (every layer's definition runs, or values merge) or
``overridable`` (the lowest definition wins, and may call ``super()``). No mark means overridable.

Each mark works both ways a chapter writes it::

    @fixed(legislation="Ley 7/1985 Art. 30")          # on a method
    def working_days_after(self, day, n): ...

    co_official = fixed(["ca-valencia", "es"], legislation="Estatut Art. 6")      # on a setting
    comment_period = overridable(working_days(30), legislation="...", check=lambda v: v >= working_days(30))

``check=`` belongs to settings only: it runs at install on the value that wins in the realm's layer
path, and a codex whose value fails it is refused.
"""

from realms.legislation.errors import LegislationError
from realms.legislation.expressions import Expr
from realms.legislation.sealed import Sealed

FIXED, EXTENSION, OVERRIDABLE = "fixed", "extension", "overridable"
MARKS = (FIXED, EXTENSION, OVERRIDABLE)
_NOTHING = object()


class Setting(metaclass=Sealed):
    """A marked setting, as the class body holds it until the metaclass reads it."""

    __slots__ = ("value", "mark", "legislation", "check")

    def __init__(self, value, mark, legislation, check):
        if check is not None and not callable(check):
            raise LegislationError("check= is a function of the setting's value")
        self.value, self.mark, self.legislation, self.check = (
            value,
            mark,
            legislation,
            check,
        )


class Method(metaclass=Sealed):
    """A marked method, as the class body holds it until the metaclass reads it."""

    __slots__ = ("fn", "mark", "legislation")

    def __init__(self, fn, mark, legislation):
        self.fn, self.mark, self.legislation = fn, mark, legislation


def _mark(mark):
    def apply(value=_NOTHING, legislation=None, check=None):
        if value is _NOTHING:  # @mark(legislation=...) on the method below
            if check is not None:
                raise LegislationError("check= is for settings, not methods")
            return lambda fn: Method(fn, mark, legislation)
        if callable(value) and not isinstance(value, type):  # @mark with no arguments
            if check is not None or legislation is not None:
                raise LegislationError(
                    f"a method is marked @{mark}(legislation=...), a setting {mark}(value, ...)"
                )
            return Method(value, mark, None)
        return Setting(value, mark, legislation, check)

    apply.__name__ = mark
    apply.__doc__ = f"Mark a method or a setting {mark}."
    return apply


fixed = _mark(FIXED)
extension = _mark(EXTENSION)
overridable = _mark(OVERRIDABLE)


class Forbid(metaclass=Sealed):
    """A forbid: ``op`` is the GGG operation it forbids, ``expression`` the ``F`` tree that is true
    when the operation is forbidden (expressions.py), ``each`` the list the rule ranges over.

    Forbids are never overridable, never removable and never change once written. The Cedar they
    become, and their binding to a realm's settings and fact schema, is ``cedar.py``.
    """

    __slots__ = ("op", "expression", "each", "legislation")

    def __init__(self, op, expression, each, legislation):
        if type(op) is not str or not op:
            raise LegislationError(
                'a forbid names a GGG operation, such as forbid("tax_policy_propose", ...)'
            )
        if legislation is not None and type(legislation) is not str:
            raise LegislationError('legislation="..." is a string')
        if not isinstance(expression, (Expr, bool)):
            raise LegislationError(
                "a forbid's expression is built from F, S and when(), such as "
                '(F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10"))'
            )
        if each is not None and (type(each) is not str or not each):
            raise LegislationError(
                'each= names the list the forbid ranges over, such as each="taxes"'
            )
        for name, value in (
            ("op", op),
            ("expression", expression),
            ("each", each),
            ("legislation", legislation),
        ):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise LegislationError("a forbid never changes")


def forbid(op, expression, each=None, legislation=None):
    """A rule that refuses GGG operation ``op`` whenever ``expression`` is true (layered-codex.md §6)."""
    return Forbid(op, expression, each, legislation)
