"""What a chapter sees through ``self`` (layered-codex.md §4.5), and the plain data that crosses out.

A chapter changes nothing itself: ``self.ggg.<op>(**args)`` queues a GGG operation as an effect, and
the realm applies the queue after the call, as codex effects work today. A guard refuses with
``self.deny(reason)``. A hook gets GGG's ``Event``. What a layer above set, a lower layer reads and
never changes: lists become tuples, dicts read-only.

The internals these objects carry (the context, the chapter behind a view) sit under dunder names a
chapter cannot spell (subset.py), and no class here can be changed once made (sealed.py).
"""

from realms.legislation.errors import LegislationError
from realms.legislation.periods import Period
from realms.legislation.rates import Money
from realms.legislation.sealed import MappingProxyType, Sealed, freeze

# Nested calls from one domain's chapter into another's (layered-codex.md §4.4, §9.3). The resolver
# refuses a codex whose ``uses`` chain is deeper, and ``as_layer`` stops a call that would be.
MAX_DEPTH = 8


class Ref(metaclass=Sealed):
    """GGG's ``Ref``: a reference to any entity by kind and id, ``Ref("external", "INE")``."""

    __slots__ = ("kind", "id")

    def __init__(self, kind, id):
        if type(kind) is not str or type(id) is not str:
            raise LegislationError("Ref(kind, id) takes two strings")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "id", id)

    def __setattr__(self, name, value):
        raise LegislationError("a Ref never changes")

    def to_json(self):
        return {"kind": self.kind, "id": self.id}

    def __eq__(self, other):
        return isinstance(other, Ref) and (self.kind, self.id) == (other.kind, other.id)

    def __hash__(self):
        return hash(("Ref", self.kind, self.id))

    def __repr__(self):
        return f"Ref({self.kind!r}, {self.id!r})"


def plain(value):
    """``value`` as plain data: Periods, Refs and Money in the form GGG stores them, inside lists and dicts. Only the
    SDK's own classes: a subclass would bring a ``to_json`` of its own."""
    kind = type(value)
    if kind in (Period, Ref, Money):
        return value.to_json()
    if kind is Queued:
        return str(value)
    if kind is Args:
        return plain(value.__data__)
    if isinstance(value, (dict, MappingProxyType)):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, frozenset, set)):
        return [plain(v) for v in value]
    return value


class Args(metaclass=Sealed):
    """An operation's arguments as a guard or a ``compute_`` method reads them: ``args.basis.legal_basis``,
    ``"checksum" in args.action.args``, ``args["recipient"]``. Read-only, down to the lists and dicts in it; a
    dict inside is an ``Args`` too. An argument the call did not give reads None, as GGG's optional fields do.
    """

    __slots__ = ("__data__",)

    def __init__(self, data):
        if type(data) not in (dict, MappingProxyType):
            raise LegislationError(
                f"Args wraps a dict of arguments, not {type(data).__name__}"
            )
        object.__setattr__(self, "__data__", freeze(data))

    def __setattr__(self, name, value):
        raise LegislationError("an operation's arguments never change")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name] if name in self.__data__ else None

    def __getitem__(self, key):
        value = self.__data__[key]
        return Args(value) if type(value) is MappingProxyType else value

    def __contains__(self, key):
        return key in self.__data__

    def __iter__(self):
        return iter(self.__data__)

    def __len__(self):
        return len(self.__data__)

    def get(self, key, default=None):
        return self[key] if key in self.__data__ else default

    def __repr__(self):
        return f"Args({plain(self.__data__)!r})"


class Denial(metaclass=Sealed):
    """A guard's refusal: what ``self.deny(reason)`` returns. The operation is refused with ``reason``."""

    __slots__ = ("reason",)

    def __init__(self, reason):
        self.reason = reason

    def __repr__(self):
        return f"deny({self.reason!r})"


class Queued(metaclass=Sealed):
    """The reference ``self.ggg.<op>()`` returns: ``queued["invoice"]`` stands for the id the realm will
    create, usable in later effects (``$eff:<n>:<key>``, as today)."""

    __slots__ = ("index",)

    def __init__(self, index):
        self.index = index

    def __getitem__(self, key):
        return f"$eff:{self.index}:{key}"

    def __str__(self):
        return f"$eff:{self.index}"


class Effects(metaclass=Sealed):
    """The GGG operations a call queued, in order, as ``[op, args]`` plain data."""

    def __init__(self, operations=None):
        self.queued = []
        self.operations = operations

    def queue(self, op, args):
        if self.operations is not None and op not in self.operations:
            raise LegislationError(f"{op!r} is not a GGG operation")
        self.queued.append([op, plain(args)])
        return Queued(len(self.queued) - 1)


class GGG(metaclass=Sealed):
    """``self.ggg``: any attribute is a GGG operation, called with its arguments, queued as an effect."""

    __slots__ = ("__effects__",)

    def __init__(self, effects):
        self.__effects__ = effects

    def __getattr__(self, op):
        if op.startswith("_"):
            raise AttributeError(op)
        return lambda **args: self.__effects__.queue(op, args)


class Event(metaclass=Sealed):
    """GGG's ``Event`` as a hook sees it: ``user``, ``args``, ``result`` and ``at``.

    Extension hooks share one event down the chain: a layer may add attributes to it, and lower
    layers can read but not change what a layer above set, down to the lists and dicts in it
    (tuples and read-only views).
    """

    FIELDS = ("user", "args", "result", "at")

    def __init__(self, user, args, result=None, at=0):
        for name, value in zip(self.FIELDS, (user, args, result, at)):
            object.__setattr__(self, name, freeze(value))
        object.__setattr__(self, "__sealed__", frozenset(self.FIELDS))

    def __setattr__(self, name, value):
        if name in self.__sealed__:
            raise LegislationError(
                f"event.{name} was set by a layer above: lower layers read it, never change it"
            )
        object.__setattr__(self, name, value)

    def seal(self):
        """Freeze every attribute set so far, before the next layer's hook runs."""
        names = [name for name in vars(self) if name != "__sealed__"]
        for name in names:
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "__sealed__", frozenset(names))


class Context(metaclass=Sealed):
    """What one call of the legislation runs with: the codex, the time, the approvals the operation
    carries, the effects queue every chapter of the call shares, the realm's local-day function, and which
    domains' methods are running (``running``, outermost first) and how many calls between domains are in
    progress (``depth``, at most ``MAX_DEPTH``)."""

    def __init__(self, codex, now=0, approvals=(), operations=None, local_day=None):
        self.codex, self.now, self.approvals = codex, now, frozenset(approvals)
        self.effects = Effects(operations)
        self.running, self.depth = [], 0
        self.local_day = local_day  # the realm's: a UTC timestamp to its local day, YYYY-MM-DD (layered-codex.md §4.5)


def is_result(value):
    """What a method may hand to another domain: a setting's kind of data (chapter.is_data), a Money, or
    lists and dicts of those."""
    from realms.legislation.chapter import is_data

    kind = type(value)
    if kind is Money:
        return True
    if kind in (list, tuple):
        return all(is_result(v) for v in value)
    if kind in (dict, MappingProxyType):
        return all(type(k) is str and is_result(v) for k, v in value.items())
    return is_data(value)


def crossing(value, file, way="returned"):
    """``value``, as it crosses between the chapter of ``file`` and another domain, ``returned`` by its method or
    ``handed`` to it: plain data passes, a chapter instance is seen through ``Public``, and anything else (a
    method, a view of other domains, an object a chapter made) is refused (realms-gos#543)."""
    from realms.legislation.chapter import Chapter

    if isinstance(value, Chapter):
        return Public(value)
    if isinstance(value, Public) or not is_result(value):
        raise LegislationError(
            f"{file} {way} {type(value).__name__}: only plain data crosses a domain boundary"
        )
    return value


class Public(metaclass=Sealed):
    """Another domain's chapter as a caller sees it: its public members only, never the ``self`` API; what
    its methods are handed and what they return goes through ``crossing``."""

    __slots__ = ("__chapter__",)

    def __init__(self, chapter):
        self.__chapter__ = chapter

    def __getattr__(self, name):
        chapter = self.__chapter__
        cls = type(chapter)
        if name.startswith("_"):
            raise LegislationError(
                f"{name} is private to its chapter: other chapters cannot call it"
            )
        if name not in cls.__members__:
            raise LegislationError(
                f"{name} is not a public member of {cls.__file__}: other chapters reach its members only"
            )
        value = getattr(chapter, name)
        if not callable(value):
            return value  # a setting: frozen data

        def call(*args, **kwargs):
            args = tuple(crossing(a, cls.__file__, "was handed") for a in args)
            kwargs = {k: crossing(v, cls.__file__, "was handed") for k, v in kwargs.items()}
            return crossing(value(*args, **kwargs), cls.__file__)

        return call


class Domains(metaclass=Sealed):
    """``self.legislation`` and ``self.layer``: the declared domains, resolved through the realm's
    layers (``legislation``) or only down to this chapter's own layer (``layer``) (layered-codex.md §4.4).
    """

    __slots__ = ("__chapter__", "__scope__")

    def __init__(self, chapter, scope):
        self.__chapter__, self.__scope__ = chapter, scope

    def __getattr__(self, domain):
        if domain.startswith("_"):
            raise AttributeError(domain)
        chapter = self.__chapter__
        codex, source = chapter.__context__.codex, chapter.running_layer()
        caller = codex.chapter_at(type(chapter).__domain__, source)
        if domain not in caller.__uses__:
            raise LegislationError(
                f"{caller.__file__} does not declare {domain!r} in uses: only declared domains can be reached"
            )
        cls = (
            codex.resolved_at(domain, source)
            if self.__scope__ == "layer"
            else codex.resolved(domain)
        )
        return Public(cls(chapter.__context__))
