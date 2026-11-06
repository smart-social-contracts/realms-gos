"""What a chapter sees through ``self`` (layered-codex.md §4.5), and the plain data that crosses out.

A chapter changes nothing itself: ``self.ggg.<op>(**args)`` queues a GGG operation as an effect, and
the realm applies the queue after the call, as codex effects work today. A guard refuses with
``self.deny(reason)``. A hook gets GGG's ``Event``.
"""

from realms.legislation.errors import LegislationError
from realms.legislation.periods import Period
from realms.legislation.rates import Money


class Ref:
    """GGG's ``Ref``: a reference to any entity by kind and id, ``Ref("external", "INE")``."""

    __slots__ = ("kind", "id")

    def __init__(self, kind, id):
        if not isinstance(kind, str) or not isinstance(id, str):
            raise LegislationError("Ref(kind, id) takes two strings")
        self.kind, self.id = kind, id

    def to_json(self):
        return {"kind": self.kind, "id": self.id}

    def __eq__(self, other):
        return isinstance(other, Ref) and (self.kind, self.id) == (other.kind, other.id)

    def __hash__(self):
        return hash(("Ref", self.kind, self.id))

    def __repr__(self):
        return "Ref(%r, %r)" % (self.kind, self.id)


def plain(value):
    """``value`` as plain data: Periods, Refs and Money in the form GGG stores them, inside lists and dicts."""
    if isinstance(value, (Period, Ref, Money)):
        return value.to_json()
    if isinstance(value, Queued):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


class Denial:
    """A guard's refusal: what ``self.deny(reason)`` returns. The operation is refused with ``reason``."""

    __slots__ = ("reason",)

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return "deny(%r)" % self.reason


class Queued:
    """The reference ``self.ggg.<op>()`` returns: ``queued["invoice"]`` stands for the id the realm will
    create, usable in later effects (``$eff:<n>:<key>``, as today)."""

    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __getitem__(self, key):
        return "$eff:%d:%s" % (self.index, key)

    def __str__(self):
        return "$eff:%d" % self.index


class Effects:
    """The GGG operations a call queued, in order, as ``[op, args]`` plain data."""

    def __init__(self, operations=None):
        self.queued = []
        self.operations = operations

    def queue(self, op, args):
        if self.operations is not None and op not in self.operations:
            raise LegislationError("%r is not a GGG operation" % op)
        self.queued.append([op, plain(args)])
        return Queued(len(self.queued) - 1)


class GGG:
    """``self.ggg``: any attribute is a GGG operation, called with its arguments, queued as an effect."""

    __slots__ = ("_effects",)

    def __init__(self, effects):
        self._effects = effects

    def __getattr__(self, op):
        if op.startswith("_"):
            raise AttributeError(op)
        return lambda **args: self._effects.queue(op, args)


class Event:
    """GGG's ``Event`` as a hook sees it: ``user``, ``args``, ``result`` and ``at``.

    Extension hooks share one event down the chain: a layer may add attributes to it, and lower
    layers can read but not change what a layer above set."""

    FIELDS = ("user", "args", "result", "at")

    def __init__(self, user, args, result=None, at=0):
        object.__setattr__(self, "_sealed", frozenset(self.FIELDS))
        for name, value in zip(self.FIELDS, (user, args, result, at)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        if name in self._sealed:
            raise LegislationError(
                "event.%s was set by a layer above: lower layers read it, never change it"
                % name
            )
        object.__setattr__(self, name, value)

    def seal(self):
        """Freeze every attribute set so far, before the next layer's hook runs."""
        object.__setattr__(
            self, "_sealed", frozenset(k for k in vars(self) if k != "_sealed")
        )


class Context:
    """What one call of the legislation runs with: the codex, the time, the approvals the operation
    carries, and the effects queue every chapter of the call shares."""

    def __init__(self, codex, now=0, approvals=(), operations=None):
        self.codex, self.now, self.approvals = codex, now, frozenset(approvals)
        self.effects = Effects(operations)


class Public:
    """Another domain's chapter as a caller sees it: public members only."""

    __slots__ = ("_chapter",)

    def __init__(self, chapter):
        self._chapter = chapter

    def __getattr__(self, name):
        if name.startswith("_"):
            raise LegislationError(
                "%s is private to its chapter: other chapters cannot call it" % name
            )
        return getattr(self._chapter, name)


class Domains:
    """``self.legislation`` and ``self.layer``: the declared domains, resolved through the realm's
    layers (``legislation``) or only down to this chapter's own layer (``layer``) (layered-codex.md §4.4).
    """

    __slots__ = ("_chapter", "_scope")

    def __init__(self, chapter, scope):
        self._chapter, self._scope = chapter, scope

    def __getattr__(self, domain):
        if domain.startswith("_"):
            raise AttributeError(domain)
        chapter = self._chapter
        codex, source = chapter._context.codex, chapter.running_layer()
        caller = codex.chapter_at(type(chapter).__domain__, source)
        if domain not in caller.__uses__:
            raise LegislationError(
                "%s does not declare %r in uses: only declared domains can be reached"
                % (caller.__file__, domain)
            )
        cls = (
            codex.resolved_at(domain, source)
            if self._scope == "layer"
            else codex.resolved(domain)
        )
        return Public(cls(chapter._context))
