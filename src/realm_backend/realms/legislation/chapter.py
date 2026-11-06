"""A chapter's class: ``above()``, the metaclass that applies the marks, and the ``self`` API
(layered-codex.md §4, §4.1, §4.5).

A chapter is one class, named after its domain, extending ``above("<domain>")``: the class of the
nearest layer above with a chapter in that domain, or ``Chapter`` at the top. Its members are hooks
``on_<op>``, guards ``check_<op>`` and ``compute_<op>`` named after GGG operations, helpers, settings
and forbids. The metaclass reads each member's mark and refuses what the layer above does not allow,
in the form the checker prints: ``<source>/<domain>.py:<line>: <reason>``.
"""

from realms.legislation.context import GGG, Denial, Domains, Event, Ref
from realms.legislation.errors import LegislationError, Refused
from realms.legislation.marks import EXTENSION, FIXED, Forbid, Method, Setting
from realms.legislation.periods import Period

# Operations no chapter may hook, guard or forbid (layered-codex.md §10.4).
RESERVED = ("codex_revert", "codex_revert_fast", "it_team_set", "sync_source_remove")
KINDS = (("on_", "hook"), ("check_", "guard"), ("compute_", "compute"))
METHODS = ("hook", "guard", "compute", "helper")
# What CPython itself puts in a class body (3.10 to 3.14), passed through untouched.
_CLASS_BODY = (
    "__module__",
    "__qualname__",
    "__doc__",
    "__annotations__",
    "__annotate__",
    "__classcell__",
    "__classdictcell__",
    "__firstlineno__",
    "__static_attributes__",
)
_NOTHING = object()
_LOADING = []  # the chapter being loaded, set by Codex.load while it runs the file


class Load:
    """One chapter being loaded: what ``above()`` and the metaclass need to know about it."""

    __slots__ = ("codex", "source", "domain", "own", "text", "base")

    def __init__(self, codex, source, domain, own, text):
        self.codex, self.source, self.domain, self.own, self.text = (
            codex,
            source,
            domain,
            own,
            text,
        )
        self.base = None

    @property
    def file(self):
        return "%s/%s.py" % (self.source, self.domain)


def loading():
    if not _LOADING:
        raise LegislationError(
            "a chapter is defined by loading it into a codex: Codex.load(source, domain, text)"
        )
    return _LOADING[-1]


def camel(domain):
    """``tax`` is ``Tax``, ``data_protection`` is ``DataProtection``."""
    return "".join(part.capitalize() for part in domain.split("_"))


def line_of(text, *starts):
    """The first line of ``text`` that starts with one of ``starts``, or None."""
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(starts):
            return number
    return None


def is_data(value):
    """A setting's value: str, int, bool, null, lists or dicts of those, a Period, or a GGG Ref. Never a float."""
    if value is None or isinstance(value, (bool, int, str, Period, Ref)):
        return True
    if isinstance(value, list):
        return all(is_data(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and is_data(v) for k, v in value.items())
    return False


def merge(upper, lower):
    """An extension's values, layer order: lists concatenate without duplicates, sets join, dicts take
    new keys only. Anything else is an error."""
    if isinstance(upper, list) and isinstance(lower, list):
        out = []
        for item in upper + lower:
            if item not in out:
                out.append(item)
        return out
    if isinstance(upper, set) and isinstance(lower, set):
        return upper | lower
    if isinstance(upper, dict) and isinstance(lower, dict):
        reused = [k for k in lower if k in upper]
        if reused:
            raise LegislationError(
                "an extension adds new keys only: %s already set above"
                % ", ".join(map(repr, reused))
            )
        return {**upper, **lower}
    raise LegislationError(
        "extension values merge lists, sets and dicts, not %s and %s"
        % (type(upper).__name__, type(lower).__name__)
    )


def above(domain):
    """The class of the nearest layer above with a chapter in ``domain``, in this realm's layer path: the
    base of every chapter class. The top layer gets ``Chapter``. The file never names a version: the codex
    being built decides which class this is."""
    load = loading()
    if domain != load.domain:
        raise Refused(
            'above(%r) in the %s chapter: a chapter extends above("%s")'
            % (domain, load.domain, load.domain),
            line_of(load.text, "class "),
            load.file,
        )
    load.base = load.codex.top(domain)
    return load.base


class Member:
    """What the metaclass keeps of one member: its kind (hook, guard, compute, helper, setting or
    forbid), mark, citation, ``check=``, the layer it came from and its line."""

    __slots__ = (
        "name",
        "kind",
        "mark",
        "legislation",
        "check",
        "source",
        "domain",
        "line",
        "value",
    )

    def __init__(self, name, kind, value, mark, legislation, check, load, line):
        self.name, self.kind, self.value, self.mark = name, kind, value, mark
        self.legislation, self.check, self.line = legislation, check, line
        self.source, self.domain = load.source, load.domain

    @property
    def file(self):
        return "%s/%s.py" % (self.source, self.domain)

    @property
    def cited(self):
        return " (%s)" % self.legislation if self.legislation else ""

    @property
    def category(self):
        return "method" if self.kind in METHODS else self.kind


def kind_of(name):
    """``on_tax_assess`` is a hook on ``tax_assess``; a name with no prefix is a helper."""
    for prefix, kind in KINDS:
        if name.startswith(prefix):
            return kind, name[len(prefix) :]
    return "helper", None


def as_layer(chapter, source, fn, args, kwargs):
    """Run ``fn`` on ``chapter`` as the layer ``source`` that defined it, so ``self.layer`` and ``uses``
    are those of the layer whose code runs, whatever lower class the instance has."""
    layers = chapter.__dict__.setdefault("_layers", [])
    layers.append(source)
    try:
        return fn(chapter, *args, **kwargs)
    finally:
        layers.pop()


def bound(fn, source):
    """A method of layer ``source``, as installed on the class."""

    def run(self, *args, **kwargs):
        return as_layer(self, source, fn, args, kwargs)

    run.__name__, run.__doc__, run.__wrapped__ = fn.__name__, fn.__doc__, fn
    return run


def extend(name, kind):
    """The method the runtime installs for an ``extension``: every layer's definition, top to bottom.

    Hooks run each; guards stop at the first denial; helpers merge what they return. A definition
    that calls ``super()`` re-enters this method and is refused."""

    def run(self, *args, **kwargs):
        running = self.__dict__.setdefault("_running", set())
        if name in running:
            raise LegislationError(
                "%s is an extension: it must not call super(); every layer's definition runs by itself"
                % name
            )
        running.add(name)
        try:
            chain = [
                (c.__source__, c.__own__[name].value)
                for c in reversed(type(self).__mro__)
                if name in getattr(c, "__own__", {})
            ]
            if kind == "hook":
                event = args[0] if args and isinstance(args[0], Event) else None
                for source, fn in chain:
                    as_layer(self, source, fn, args, kwargs)
                    if event is not None:
                        event.seal()
                return None
            if kind == "guard":
                for source, fn in chain:
                    out = as_layer(self, source, fn, args, kwargs)
                    if out is not None:
                        return out
                return None
            result = _NOTHING
            for source, fn in chain:
                out = as_layer(self, source, fn, args, kwargs)
                result = out if result is _NOTHING else merge(result, out)
            return result
        finally:
            running.discard(name)

    run.__name__, run.__extension__ = name, True
    return run


class ChapterMeta(type):
    """Reads a chapter's members and applies the marks of the layer above (layered-codex.md §4.1)."""

    def __new__(meta, name, bases, namespace):
        if not bases:  # Chapter itself
            cls = super().__new__(meta, name, bases, namespace)
            cls.__members__, cls.__own__, cls.__uses__ = {}, {}, ()
            cls.__source__ = cls.__domain__ = cls.__file__ = None
            return cls
        load = loading()

        def refuse(reason, line=None):
            raise Refused(reason, line, load.file)

        class_line = line_of(load.text, "class ")
        if len(bases) != 1 or bases[0] is not load.base:
            refuse(
                '%s extends above("%s") and nothing else' % (name, load.domain),
                class_line,
            )
        if name != camel(load.domain):
            refuse(
                "the class of %s is named %s" % (load.file, camel(load.domain)),
                class_line,
            )
        base = bases[0]
        uses = namespace.get("uses", ())
        if (
            not isinstance(uses, (list, tuple))
            or not all(isinstance(d, str) and d for d in uses)
            or len(set(uses)) != len(uses)
        ):
            refuse(
                "uses lists the domains this chapter calls, each once, as strings",
                line_of(load.text, "uses"),
            )
        own, attrs = {}, {}
        for key, value in namespace.items():
            if key in _CLASS_BODY or key == "uses":
                continue
            member = meta.member(key, value, load, refuse)
            upper = base.__members__.get(key)
            if upper is not None:
                meta.rule(member, upper, refuse)
            own[key] = member
            attrs[key] = meta.install(member)
        body = {k: v for k, v in namespace.items() if k in _CLASS_BODY or k == "uses"}
        cls = super().__new__(meta, name, bases, {**body, **attrs})
        cls.__members__ = {**base.__members__, **own}
        cls.__own__, cls.__uses__ = own, tuple(uses)
        cls.__source__, cls.__domain__, cls.__file__ = (
            load.source,
            load.domain,
            load.file,
        )
        return cls

    @staticmethod
    def member(key, value, load, refuse):
        """One class attribute as a Member, or a refusal."""
        if key.startswith("__"):
            refuse("%s: a chapter has no dunder members" % key, line_of(load.text, key))
        if key in API:
            refuse(
                "%s is reserved by the SDK (self.%s)" % (key, key),
                line_of(load.text, key, "def " + key),
            )
        mark = legislation = check = op = None
        line = line_of(load.text, key)
        if isinstance(value, Setting):
            kind, raw = "setting", value.value
            mark, legislation, check = value.mark, value.legislation, value.check
            if not is_data(raw):
                refuse(
                    "%s: a setting's value is JSON data or a Period, never a float, not %r"
                    % (key, raw),
                    line,
                )
        elif isinstance(value, Method):
            kind, op = kind_of(key)
            raw, mark, legislation = value.fn, value.mark, value.legislation
            line = raw.__code__.co_firstlineno
        elif isinstance(value, Forbid):
            kind, raw, op, legislation = "forbid", value, value.op, value.legislation
        elif isinstance(value, float):
            refuse("%s: floats are not allowed in a chapter" % key, line)
        elif isinstance(value, type):
            refuse(
                "%s: no nested classes in a chapter" % key,
                line_of(load.text, "class " + key),
            )
        elif hasattr(value, "__code__"):
            kind, op = kind_of(key)
            raw, line = value, value.__code__.co_firstlineno
        elif is_data(value):
            kind, raw = "setting", value
        else:
            refuse(
                "%s: a member is a method, a setting or a forbid, not %s"
                % (key, type(value).__name__),
                line,
            )
        if kind in ("hook", "guard", "compute") and not op:
            refuse(
                "%s names no operation: a %s is on_<operation>, check_<operation> or compute_<operation>"
                % (key, kind),
                line,
            )
        if op in RESERVED:
            refuse(
                "%s: %s is reserved; no chapter may hook, guard or forbid it"
                % (key, op),
                line,
            )
        if (
            op is not None
            and load.codex.operations is not None
            and op not in load.codex.operations
        ):
            refuse("%s must name a GGG operation: %r is not one" % (key, op), line)
        if not key.startswith("_") and not load.own and legislation is None:
            refuse(
                '%s needs legislation="...": %s is not the realm\'s own layer'
                % (key, load.source),
                line,
            )
        return Member(key, kind, raw, mark, legislation, check, load, line)

    @staticmethod
    def rule(member, upper, refuse):
        """What the layer above allows for this name (the table of layered-codex.md §4.1)."""
        name, line = member.name, member.line
        if upper.kind == "forbid":
            refuse(
                "%s is a forbid of %s%s: forbids are never overridable"
                % (name, upper.file, upper.cited),
                line,
            )
        if upper.mark == FIXED:
            refuse("%s is fixed by %s%s" % (name, upper.file, upper.cited), line)
        if member.category != upper.category:
            refuse(
                "%s is a %s in %s%s: a lower layer cannot make it a %s"
                % (name, upper.category, upper.file, upper.cited, member.category),
                line,
            )
        if upper.mark == EXTENSION:
            if member.mark not in (None, EXTENSION):
                refuse(
                    "%s is an extension of %s%s: a lower layer adds to it and cannot mark it %s"
                    % (name, upper.file, upper.cited, member.mark),
                    line,
                )
            member.mark = EXTENSION
            if member.kind == "setting":
                try:
                    member.value = merge(upper.value, member.value)
                except LegislationError as e:
                    refuse(
                        "%s extends %s%s: %s" % (name, upper.file, upper.cited, e), line
                    )

    @staticmethod
    def install(member):
        """What the class attribute becomes: the value, the forbid, the method, or the extension runner."""
        if member.kind in METHODS:
            return (
                extend(member.name, member.kind)
                if member.mark == EXTENSION
                else bound(member.value, member.source)
            )
        return member.value


class Chapter(metaclass=ChapterMeta):
    """The base of the top layer's chapter in every domain, and the ``self`` API every chapter has.

    A chapter changes nothing itself: ``self.ggg.<op>(**args)`` queues GGG operation ``<op>`` as an
    effect. ``self.deny(reason)`` is a guard's refusal. ``self.legislation.<d>`` and ``self.layer.<d>``
    reach a declared domain (layered-codex.md §4.4). ``self.calendar`` is the realm's calendar.
    """

    uses = ()

    def __init__(self, context):
        self._context = context

    @property
    def ggg(self):
        return GGG(self._context.effects)

    def deny(self, reason):
        return Denial(reason)

    def now(self):
        """The current time, a UTC timestamp, as the realm supplies it."""
        return self._context.now

    def approved_by(self, department):
        """Whether the current operation carries ``department``'s approval."""
        return department in self._context.approvals

    @property
    def calendar(self):
        return self._context.codex.calendar

    @property
    def legislation(self):
        return Domains(self, "legislation")

    @property
    def layer(self):
        return Domains(self, "layer")

    def running_layer(self):
        """The source whose method is running: its ``uses`` and its view of other domains apply."""
        layers = self.__dict__.get("_layers")
        return layers[-1] if layers else type(self).__source__


# Names a chapter may not define: the self API.
API = tuple(k for k in vars(Chapter) if not k.startswith("__") and k != "uses")
