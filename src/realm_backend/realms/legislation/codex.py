"""A layered codex as the library builds it: the realm's layers, top to bottom, and every chapter and
calendar loaded into them.

The resolver (S3, #485) computes the layers from the realm's links and loads the chapters in order;
the runtime (D3, #488) instantiates the resolved classes around GGG operations. Loading runs a
chapter's file with the built-ins the design bans removed and imports limited to the SDK and ``ggg``,
then checks one class per file, named after the domain, with nothing else at module level but imports
from the SDK and constants (layered-codex.md §4, §12.2).
"""

import builtins
import sys

from realms.legislation.calendar import Calendar
from realms.legislation.chapter import (
    _LOADING,
    Chapter,
    ChapterMeta,
    Load,
    camel,
    is_data,
)
from realms.legislation.context import Context
from realms.legislation.errors import LegislationError, Refused

BANNED = ("exec", "eval", "compile", "globals", "locals", "setattr", "open")
SDK, GGG = "realms.legislation", "ggg"
_SOURCE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
_DOMAIN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def source_id(source):
    """``eu/es/vc``: folder names of lowercase letters, digits and ``-``."""
    parts = source.split("/") if isinstance(source, str) else []
    if not parts or not all(p and set(p) <= _SOURCE_CHARS for p in parts):
        raise LegislationError(
            "%r is not a source id: folder names of lowercase letters, digits and -"
            % (source,)
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
        raise LegislationError("%r is not a domain id" % (domain,))
    return domain


class Codex:
    """``layers`` are the source ids of the realm's layer paths, top to bottom; ``realm`` is the realm's own
    source id; ``operations`` the GGG operation names, when known, for the hooks, guards and forbids to be
    checked against; ``modules`` what a chapter's ``import ggg`` resolves to where ``ggg`` is not importable.
    """

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
                "%s is not a layer of this codex %s" % (source, list(self.layers))
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
            raise LegislationError("this realm has no %s chapter" % domain)
        return chain[-1]

    def resolved_at(self, domain, source):
        """The class of ``domain`` down to layer ``source`` only: the source's own view, ignoring lower layers."""
        at = self.index(source)
        chain = [
            c for c in self.chapters.get(domain, ()) if self.index(c.__source__) <= at
        ]
        if not chain:
            raise LegislationError("no %s chapter at or above %s" % (domain, source))
        return chain[-1]

    def chapter_at(self, domain, source):
        """The class loaded for exactly ``<source>/<domain>.py``."""
        for cls in self.chapters.get(domain, ()):
            if cls.__source__ == source:
                return cls
        raise LegislationError("no %s/%s.py is loaded" % (source, domain))

    def load(self, source, domain, text):
        """Run chapter ``<source>/<domain>.py`` and add its class below the chapters of ``domain``."""
        file = "%s/%s.py" % (source_id(source), domain_id(domain))
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
                "chapters load top to bottom: %s is already loaded"
                % chain[-1].__file__,
                None,
                file,
            )
        load = Load(self, source, domain, source == self.realm, text)
        space = {"__name__": file, "__builtins__": self._builtins(file)}
        _LOADING.append(load)
        try:
            exec(compile(text, file, "exec"), space)
        except Refused:
            raise
        except SyntaxError as e:
            raise Refused(e.msg, e.lineno, file)
        except LegislationError as e:
            raise Refused(str(e), None, file)
        except Exception as e:
            raise Refused("%s: %s" % (type(e).__name__, e), None, file)
        finally:
            _LOADING.pop()
        cls = self._the_chapter(space, load)
        self.chapters.setdefault(domain, []).append(cls)
        return cls

    def _builtins(self, file):
        """The built-ins a chapter runs with: the banned ones removed, imports limited to the SDK and ``ggg``."""

        def _import(name, globals=None, locals=None, fromlist=(), level=0):
            if level or name not in (SDK, GGG) or (name == SDK and not fromlist):
                raise Refused(
                    "import %s: a chapter imports only from %s and %s"
                    % (name, SDK, GGG),
                    None,
                    file,
                )
            if name in self.modules:
                return self.modules[name]
            return builtins.__import__(name, globals, locals, fromlist, level)

        env = {k: v for k, v in vars(builtins).items() if k not in BANNED}
        env["__import__"] = _import
        return env

    def _the_chapter(self, space, load):
        """The one class of the file; everything else at module level is an import or a constant."""
        import realms.legislation as sdk

        sdk.Chapter  # noqa: B018 - a bundled module in the canister fills vars() on first use
        allowed = list(vars(sdk).values())
        ggg = self.modules.get(GGG) or sys.modules.get(GGG)
        if ggg is not None:
            allowed += [ggg] + list(vars(ggg).values())
        found = []
        for key, value in space.items():
            if key in ("__name__", "__builtins__", "__doc__"):
                continue
            if isinstance(value, ChapterMeta) and value.__file__ == load.file:
                found.append(value)
            elif not any(value is v for v in allowed) and not is_data(value):
                raise Refused(
                    "%s: nothing else at module level except imports from the SDK and constants"
                    % key,
                    None,
                    load.file,
                )
        if len(found) != 1:
            raise Refused(
                "one class per file, %s, extending above(%r)"
                % (camel(load.domain), load.domain),
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
            raise Refused(str(e), None, "%s/calendar.json" % source)
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
        whose value fails is refused, naming the chapter that set the value and the check it fails.
        """
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
                    why = "%s = %r fails the check of %s%s" % (
                        name,
                        value,
                        own.file,
                        own.cited,
                    )
                    try:
                        passed = own.check(value)
                    except LegislationError as e:
                        raise Refused("%s: %s" % (why, e), member.line, member.file)
                    if not passed:
                        raise Refused(why, member.line, member.file)

    def context(self, now=0, approvals=()):
        """A call's context: the time and the approvals the operation carries."""
        return Context(self, now, approvals, self.operations)

    def instance(self, domain, context):
        """The resolved chapter of ``domain``, ready to run for ``context``."""
        return self.resolved(domain)(context)
