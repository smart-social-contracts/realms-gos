"""A layered codex as the library builds it: the realm's layers, top to bottom, and every chapter and
calendar loaded into them.

``Codex.load`` is the loader the realm runs *inside* its subinterpreter sandbox (layered-codex.md §9.3,
docs/architecture/sandboxing.md): the sandbox isolates the realm's data and code and meters the
call; this loader enforces the chapter rules of §4 and §12.2. It runs a chapter's file with only the
built-ins a chapter may use, imports limited to what the SDK publishes and to the read-only ``ggg``
view the realm supplies, no dunder name anywhere in the code (checked on the compiled code before
anything runs), one class per file named after the domain, built by the SDK's metaclass, and nothing
else at module level but imports and constants. The resolver (S3, #485) computes the layers from the
realm's links and loads the chapters in order; the runtime (D3, #488) instantiates the resolved
classes around GGG operations and supplies the ``ggg`` view.
"""

import builtins

from realms.legislation.calendar import Calendar
from realms.legislation.chapter import (
    _LOADING,
    CLASS_BODY,
    Chapter,
    ChapterMeta,
    Load,
    camel,
    is_data,
)
from realms.legislation.context import Context
from realms.legislation.errors import LegislationError, Refused

SDK, GGG = "realms.legislation", "ggg"
# The built-ins a chapter may use. Not here, so not there: exec, eval, compile, globals, locals, setattr,
# open, vars, input, breakpoint, __loader__, __spec__ and the rest of what builtins carries.
SAFE_BUILTINS = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes", "callable", "chr", "complex", "dict",
    "divmod", "enumerate", "filter", "float", "format", "frozenset", "hash", "hex", "id", "int", "isinstance",
    "issubclass", "iter", "len", "list", "map", "max", "min", "next", "object", "oct", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum", "super", "tuple", "type",
    "zip", "Ellipsis", "NotImplemented", "__build_class__",
)  # fmt: skip
_SOURCE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_DOMAIN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def source_id(source):
    """``eu/es/vc``: folder names of lowercase letters, digits and ``-``."""
    parts = source.split("/") if isinstance(source, str) else []
    if not parts or not all(p and set(p) <= _SOURCE_CHARS for p in parts):
        raise LegislationError(
            f"{source!r} is not a source id: folder names of lowercase letters, digits and -"
        )
    return source


def domain_id(domain):
    """``data_protection``: lowercase letters, digits and ``_``, starting with a letter."""
    if (
        not isinstance(domain, str)
        or not domain
        or not domain[0].isalpha()
        or not set(domain) <= _DOMAIN_CHARS
    ):
        raise LegislationError(f"{domain!r} is not a domain id")
    return domain


def dunder_names(code):
    """Every dunder name the compiled chapter reads, writes or looks up as an attribute, other than
    what CPython puts in a class body. The chapter is refused before it runs if there is one.
    """
    found = set()

    def walk(c):
        found.update(
            n for n in c.co_names if n.startswith("__") and n not in CLASS_BODY
        )
        for const in c.co_consts:
            if type(const) is type(c):
                walk(const)

    walk(code)
    return sorted(found)


def line_with(text, token):
    """The first line of ``text`` holding ``token``, or None."""
    for number, line in enumerate(text.splitlines(), 1):
        if token in line:
            return number
    return None


def no_dunder(fn):
    """``getattr``, ``hasattr`` and ``delattr`` as a chapter gets them: never for a dunder name."""

    def guarded(obj, name, *rest):
        if not isinstance(name, str) or name.startswith("__"):
            raise LegislationError(
                f"{fn.__name__}({name!r}): no dunder attribute access in a chapter"
            )
        return fn(obj, name, *rest)

    guarded.__name__ = fn.__name__
    return guarded


class Codex:
    """``layers`` are the source ids of the realm's layer paths, top to bottom; ``realm`` is the realm's own
    source id; ``operations`` the GGG operation names, when known, for the hooks, guards and forbids to be
    checked against; ``modules`` what a chapter's ``import ggg`` resolves to: the read-only view of GGG's
    entities the realm supplies (D3, #488). Without one, ``import ggg`` is refused."""

    def __init__(self, layers, realm=None, operations=None, modules=None):
        self.layers = tuple(source_id(s) for s in layers)
        if len(set(self.layers)) != len(self.layers):
            raise LegislationError("a source appears once in the layers")
        if realm is not None:
            self.index(realm)
        self.realm = realm
        self.operations = None if operations is None else frozenset(operations)
        self.modules = dict(modules or {})
        self.chapters = {}  # domain -> classes, top to bottom
        self.calendars = {}  # source -> its calendar.json
        self._calendar = None

    def index(self, source):
        if source not in self.layers:
            raise LegislationError(
                f"{source} is not a layer of this codex {list(self.layers)}"
            )
        return self.layers.index(source)

    def top(self, domain):
        """What ``above(domain)`` returns for the next chapter loaded in ``domain``."""
        chain = self.chapters.get(domain)
        return chain[-1] if chain else Chapter

    def resolved(self, domain):
        """The class of ``domain`` as it applies in this realm: the lowest layer's."""
        chain = self.chapters.get(domain)
        if not chain:
            raise LegislationError(f"this realm has no {domain} chapter")
        return chain[-1]

    def resolved_at(self, domain, source):
        """The class of ``domain`` down to layer ``source`` only: the source's own view, ignoring lower layers."""
        at = self.index(source)
        chain = [
            c for c in self.chapters.get(domain, ()) if self.index(c.__source__) <= at
        ]
        if not chain:
            raise LegislationError(f"no {domain} chapter at or above {source}")
        return chain[-1]

    def chapter_at(self, domain, source):
        """The class loaded for exactly ``<source>/<domain>.py``."""
        for cls in self.chapters.get(domain, ()):
            if cls.__source__ == source:
                return cls
        raise LegislationError(f"no {source}/{domain}.py is loaded")

    def load(self, source, domain, text):
        """Run chapter ``<source>/<domain>.py`` and add its class below the chapters of ``domain``."""
        file = f"{source_id(source)}/{domain_id(domain)}.py"
        at = self.index(source)
        if domain == "realm" and source != self.realm:
            raise Refused(
                "the realm domain may only be filled in the realm's own layer",
                None,
                file,
            )
        chain = self.chapters.get(domain, [])
        if chain and self.index(chain[-1].__source__) >= at:
            raise Refused(
                f"chapters load top to bottom: {chain[-1].__file__} is already loaded",
                None,
                file,
            )
        try:
            code = compile(text, file, "exec")
        except SyntaxError as e:
            raise Refused(e.msg, e.lineno, file)
        dunders = dunder_names(code)
        if dunders:
            raise Refused(
                f"{', '.join(dunders)}: no dunder names in a chapter",
                line_with(text, dunders[0]),
                file,
            )
        load = Load(self, source, domain, source == self.realm, text)
        space = {"__name__": file, "__builtins__": self._builtins(file)}
        _LOADING.append(load)
        try:
            exec(code, space)
        except Refused:
            raise
        except LegislationError as e:
            raise Refused(str(e), None, file)
        except Exception as e:
            raise Refused(f"{type(e).__name__}: {e}", None, file)
        finally:
            _LOADING.pop()
        cls = self._the_chapter(space, load)
        self.chapters.setdefault(domain, []).append(cls)
        return cls

    def _builtins(self, file):
        """The built-ins a chapter runs with: ``SAFE_BUILTINS``, the exceptions, guarded attribute access,
        and an import of the SDK's published names or of the ``ggg`` view only."""
        import realms.legislation as sdk

        def _import(name, globals=None, locals=None, fromlist=(), level=0):
            if level or name not in (SDK, GGG):
                raise Refused(
                    f"import {name}: a chapter imports only from {SDK} and {GGG}",
                    None,
                    file,
                )
            fromlist = fromlist or ()
            if name == SDK:
                unknown = [n for n in fromlist if n not in sdk.__all__]
                if not fromlist or unknown:
                    raise Refused(
                        f"from {SDK} import {', '.join(unknown) or '...'}: a chapter imports only {', '.join(sdk.__all__)}",
                        None,
                        file,
                    )
                return sdk
            if GGG not in self.modules:
                raise Refused(
                    f"import {GGG}: the realm supplies the read-only view of GGG's entities (D3, #488); this codex has none",
                    None,
                    file,
                )
            return self.modules[GGG]

        every = vars(builtins)
        env = {k: every[k] for k in SAFE_BUILTINS}
        env.update(
            (k, v)
            for k, v in every.items()
            if isinstance(v, type) and issubclass(v, BaseException)
        )
        env.update((f.__name__, no_dunder(f)) for f in (getattr, hasattr, delattr))
        env["__import__"] = _import
        return env

    def _the_chapter(self, space, load):
        """The one class of the file, built by the SDK's metaclass; everything else at module level is
        an import or a constant."""
        import realms.legislation as sdk

        sdk.Chapter  # noqa: B018 - a bundled module in the canister fills vars() on first use
        allowed = list(vars(sdk).values())
        ggg = self.modules.get(GGG)
        if ggg is not None:
            allowed += [ggg] + list(vars(ggg).values())
        found = []
        for key, value in space.items():
            if key in ("__name__", "__builtins__", "__doc__"):
                continue
            if isinstance(value, ChapterMeta) and value.__file__ == load.file:
                if type(value) is not ChapterMeta:
                    raise Refused(
                        f"{key} must use the SDK's metaclass, not {type(value).__name__}",
                        None,
                        load.file,
                    )
                found.append(value)
            elif not any(value is v for v in allowed) and not is_data(value):
                raise Refused(
                    f"{key}: nothing else at module level except imports from the SDK and constants",
                    None,
                    load.file,
                )
        if len(found) != 1:
            raise Refused(
                f"one class per file, {camel(load.domain)}, extending above({load.domain!r})",
                None,
                load.file,
            )
        return found[0]

    def calendar_layer(self, source, data):
        """Layer ``source``'s ``calendar.json``, parsed."""
        self.index(source)
        try:
            Calendar([data])
        except LegislationError as e:
            raise Refused(str(e), None, f"{source}/calendar.json")
        self.calendars[source] = data
        self._calendar = None

    @property
    def calendar(self):
        """The realm's calendar: the union of the layers' calendars, top to bottom."""
        if self._calendar is None:
            self._calendar = Calendar(
                [self.calendars[s] for s in self.layers if s in self.calendars]
            )
        return self._calendar

    def check(self):
        """Every setting's ``check=``, run on the value that wins in this realm's layer path. A codex
        whose value fails, or whose check raises, is refused, naming the chapter that set the value
        and the check it fails."""
        for domain, chain in self.chapters.items():
            lowest = chain[-1]
            for name, member in lowest.__members__.items():
                if member.kind != "setting":
                    continue
                value = getattr(lowest, name)
                for cls in chain:
                    own = cls.__own__.get(name)
                    if own is None or own.check is None:
                        continue
                    why = f"{name} = {value!r} fails the check of {own.file}{own.cited}"
                    try:
                        passed = own.check(value)
                    except Exception as e:
                        detail = (
                            str(e)
                            if isinstance(e, LegislationError)
                            else f"{type(e).__name__}: {e}"
                        )
                        raise Refused(f"{why}: {detail}", member.line, member.file)
                    if not passed:
                        raise Refused(why, member.line, member.file)

    def context(self, now=0, approvals=()):
        """A call's context: the time and the approvals the operation carries."""
        return Context(self, now, approvals, self.operations)

    def instance(self, domain, context):
        """The resolved chapter of ``domain``, ready to run for ``context``."""
        return self.resolved(domain)(context)
