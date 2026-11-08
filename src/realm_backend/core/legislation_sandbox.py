"""What runs inside the codex subinterpreter (docs/codices/layered-codex.md §9.3, docs/architecture/sandboxing.md).

core.legislation spawns one subinterpreter per active layered codex with this file's text as its program (plus
``LIBRARY``, the sources of ``realms.legislation``, appended by the realm) and calls the functions below with plain
data: ``setup`` installs the library and builds an empty ``Codex``, ``load`` adds one chapter, ``finish`` runs the
install checks; ``views`` and ``cedar`` are the install's report; ``guard``, ``hook_domains`` with ``hook``, and
``compute`` answer for a GGG operation. The only way out is ``rpc``: ``ggg_read`` for the read-only view of GGG's entities that chapters import,
and ``local_day`` for the realm's local day of a timestamp.

Nothing here runs in the realm's own interpreter: the realm reads this file's text (core.sandbox.module_source) and
never imports it. Every import is inside a function, so the text is a program, not a module.
"""

import sys

# "codex": the Codex; "files": the chapter files loaded; "ready": the load is complete; "entities": the names of the
# entities a chapter reads; "decimals": the token a compute_ call works in, while it runs (the Money views carry it).
STATE = {"ready": False, "entities": (), "decimals": None}
# The argument of a compute_<op> that names the row the operation is about, as "<Entity>:<id>": the chapter is given
# the view ``ggg.<Entity>.get(id)`` gives of that row, not the reference.
SUBJECTS = {"tax_assess": "subject"}


class Finder:
    """Imports the library from source: ``sys.path`` is empty here, so a finder on ``sys.meta_path`` is how
    ``from realms.legislation import ...`` finds its modules."""

    def __init__(self, sources):
        self.sources = sources  # name -> (text, is_package)

    def find_spec(self, name, path=None, target=None):
        if name not in self.sources:
            return None
        spec_type = type(sys.modules["_frozen_importlib"].ModuleSpec("", None))
        return spec_type(name, self, is_package=self.sources[name][1])

    def create_module(self, spec):
        return None  # the default module object

    def exec_module(self, module):
        text, _ = self.sources[module.__name__]
        exec(compile(text, module.__name__.replace(".", "/") + ".py", "exec"), module.__dict__)


def ggg_module(entities, money):
    """The ``ggg`` module a chapter imports: one read-only entity per name, read through the realm (``rpc``). The
    fields in ``money`` (entity name to field names) are read as ``Money`` of the decimals the call works in."""
    from realms.legislation.errors import LegislationError
    from realms.legislation.rates import Money
    from realms.legislation.sealed import Sealed, freeze

    class View(metaclass=Sealed):
        """One row of a GGG entity as a chapter reads it: its fields as attributes, read-only."""

        __slots__ = ("__kind__", "__data__")

        def __init__(self, kind, data):
            amounts = {f: Money(data[f], STATE["decimals"]) for f in money.get(kind, ()) if data[f] is not None}
            object.__setattr__(self, "__kind__", kind)
            object.__setattr__(self, "__data__", freeze({**data, **amounts}))

        def __getattr__(self, name):
            if name.startswith("_") or name not in self.__data__:
                raise AttributeError(f"{self.__kind__} has no field {name!r}")
            return self.__data__[name]

        def __setattr__(self, name, value):
            raise LegislationError("GGG's entities are read-only in a chapter")

        def __repr__(self):
            return f"{self.__kind__}({self.__data__.get('id', '')!r})"

    class Entity(metaclass=Sealed):
        """A GGG entity as a chapter reads it: ``get(id)``, ``each()`` and ``find(field, value)`` (an indexed field)."""

        __slots__ = ("__kind__",)

        def __init__(self, kind):
            object.__setattr__(self, "__kind__", kind)

        def __setattr__(self, name, value):
            raise LegislationError("GGG's entities are read-only in a chapter")

        def get(self, id):
            row = self._ask(method="get", id=str(id))
            return None if row is None else View(self.__kind__, row)

        def each(self):
            return self._rows("page")

        def find(self, field, value):
            return self._rows("find", field=field, value=value)

        def _rows(self, method, **kw):
            found, start = [], 1
            while start is not None:
                page = self._ask(method=method, start=start, **kw)
                found += [View(self.__kind__, row) for row in page["rows"]]
                start = page["next"]
            return tuple(found)

        def _ask(self, **kw):
            """One read, through the realm: at run time only, never while the codex loads (a class body, a check=)."""
            if not STATE["ready"]:
                raise LegislationError("GGG's entities are read when a method runs, not while the codex loads")
            return rpc("ggg_read", entity=self.__kind__, **kw)  # noqa: F821  the sandbox's builtin

        def __repr__(self):
            return f"ggg.{self.__kind__}"

    module = type(sys)("ggg")
    for name in entities:
        setattr(module, name, Entity(name))
    return module


def setup(operations, layers, realm, calendars, entities, money, facts=None):
    """An empty codex of ``layers`` with its calendars, ready to load chapters into. Installs the library first when
    the realm appended its sources (``LIBRARY``); on a laptop the library is importable as it is."""
    sources = globals().get("LIBRARY")
    if sources:
        sys.meta_path.insert(0, Finder({name: tuple(v) for name, v in sources.items()}))
    from realms.legislation import Codex
    from realms.legislation.errors import LegislationError

    try:
        codex = Codex(layers, realm=realm, operations=operations, modules={"ggg": ggg_module(entities, money)},
                      facts=facts)
        for source, data in calendars:
            codex.calendar_layer(source, data)
    except LegislationError as e:
        return {"ok": False, "error": str(e)}
    STATE.update(codex=codex, files=set(), entities=frozenset(entities))
    return {"ok": True}


def load(source, domain, text):
    """Chapter ``<source>/<domain>.py``, below the chapters of its domain loaded so far."""
    from realms.legislation.errors import LegislationError

    try:
        cls = STATE["codex"].load(source, domain, text)
    except LegislationError as e:
        return {"ok": False, "error": str(e)}
    STATE["files"].add(cls.__file__)
    return {"ok": True}


def finish():
    """The checks a loaded codex passes before it is installed (§12.2): every setting's ``check=``, and the
    ``uses`` between domains (declared, no cycle, no chain deeper than the depth limit)."""
    from realms.legislation.errors import LegislationError
    from realms.legislation.resolver import check_uses

    try:
        STATE["codex"].check()
        check_uses(STATE["codex"])
    except LegislationError as e:
        return {"ok": False, "error": str(e)}
    STATE["ready"] = True
    return {"ok": True, "domains": sorted(STATE["codex"].chapters)}


def views():
    """The flattened view of every domain (§5.9)."""
    from realms.legislation.view import flatten

    return {domain: flatten(STATE["codex"], domain) for domain in sorted(STATE["codex"].chapters)}


def cedar():
    """The generated Cedar of every forbid, settings folded (§5.8); needs the fact schema ``setup`` was given."""
    from realms.legislation.errors import LegislationError

    try:
        return {"ok": True, "cedar": STATE["codex"].cedar()}
    except LegislationError as e:
        return {"ok": False, "error": str(e)}


def context(now, approvals):
    return STATE["codex"].context(now, approvals, local_day=lambda at: rpc("local_day", at=at))  # noqa: F821


def domains_with(name):
    """The domains whose resolved chapter has the member ``name``, by name: this design orders domains no other way."""
    codex = STATE["codex"]
    return [domain for domain in sorted(codex.chapters) if name in codex.resolved(domain).__members__]


def files_of(domain):
    return [cls.__file__ for cls in STATE["codex"].chapters[domain]]


def wrap(value):
    """An argument as a chapter reads it: a dict as ``Args`` (attribute access), a list as a tuple of those."""
    from realms.legislation.context import Args

    if isinstance(value, dict):
        return Args(value)
    if isinstance(value, list):
        return tuple(wrap(v) for v in value)
    return value


def is_plain(value):
    """Whether ``value`` can leave the sandbox: None, bool, int, str, and lists and dicts of those."""
    if value is None or type(value) in (bool, int, str):
        return True
    if type(value) is list:
        return all(is_plain(v) for v in value)
    return type(value) is dict and all(type(k) is str and is_plain(v) for k, v in value.items())


def failure(error):
    """``{"error", "chapter"}`` of an exception a chapter raised: its text and the chapter file nearest the raise."""
    from realms.legislation.errors import LegislationError

    chapter, tb = None, error.__traceback__
    while tb is not None:
        file = tb.tb_frame.f_code.co_filename
        if file in STATE["files"]:
            chapter = file
        tb = tb.tb_next
    text = str(error) if isinstance(error, LegislationError) else f"{type(error).__name__}: {error}"
    return {"error": text, "chapter": chapter}


def guard(op, args, now, approvals):
    """Every domain's ``check_<op>`` with the operation's ``args``, by domain name; the first denial or error stops.
    Returns one outcome per domain run: ``{"domain", "files"}`` plus ``denied`` (the reason) or ``error`` and
    ``chapter``. A guard queues no effects and returns None or ``self.deny(reason)``."""
    from realms.legislation.context import Args, Denial

    codex, name, facts, outcomes = STATE["codex"], f"check_{op}", Args(args), []
    ctx = context(now, approvals)
    for domain in domains_with(name):
        outcome = {"domain": domain, "files": files_of(domain)}
        outcomes.append(outcome)
        try:
            answer = getattr(codex.instance(domain, ctx), name)(facts)
            if ctx.effects.queued:
                raise problem(f"{name} queued an effect: a guard only allows or denies")
            if answer is not None and not isinstance(answer, Denial):
                raise problem(f"{name} returned {type(answer).__name__}: a guard returns None or self.deny(reason)")
        except Exception as e:
            outcome.update(failure(e))
            break
        if answer is not None:
            outcome["denied"] = answer.reason
            break
    return outcomes


def hook_domains(op):
    """The domains whose chapter defines ``on_<op>``, by name, each with its chapter files top to bottom."""
    return [{"domain": domain, "files": files_of(domain)} for domain in domains_with(f"on_{op}")]


def hook(op, domain, event, now, approvals):
    """One domain's ``on_<op>`` with GGG's event and an effects queue of its own, as one sandbox call. Returns
    ``{"effects": [[op, args], ...]}``, or ``error`` and ``chapter``."""
    from realms.legislation.context import Event

    codex, name, ctx = STATE["codex"], f"on_{op}", context(now, approvals)
    try:
        getattr(codex.instance(domain, ctx), name)(Event(event["user"], event["args"], event["result"], event["at"]))
        if not is_plain(ctx.effects.queued):
            raise problem(f"{name} queued an effect whose arguments are not plain data")
    except Exception as e:
        return failure(e)
    return {"effects": ctx.effects.queued}


def compute(op, kwargs, now, approvals, decimals=None):
    """The one domain's ``compute_<op>`` with ``kwargs`` (dicts as ``Args``; the row an operation is about, ``SUBJECTS``,
    as its view). ``decimals`` is the token the answer is in, which the ``Money`` of every view read during the call
    carries. Returns ``{"found": False}`` when no chapter defines it, else ``{"found": True, "domain", "files"}`` plus
    ``result`` or ``error`` and ``chapter``. Two domains defining it is an error: one domain answers what an operation
    asks."""
    codex, name = STATE["codex"], f"compute_{op}"
    found = domains_with(name)
    if not found:
        return {"found": False}
    outcome = {"found": True, "domain": found[0], "files": files_of(found[0])}
    if len(found) > 1:
        return {**outcome, "error": f"{name} is defined in {', '.join(found)}: one domain answers a compute",
                "chapter": None}
    ctx = context(now, approvals)
    STATE["decimals"] = decimals
    try:
        from realms.legislation.context import plain

        arguments = {k: wrap(v) for k, v in kwargs.items()}
        if SUBJECTS.get(op) in arguments:
            arguments[SUBJECTS[op]] = subject_view(kwargs[SUBJECTS[op]])
        result = plain(getattr(codex.instance(found[0], ctx), name)(**arguments))
        if ctx.effects.queued:
            raise problem(f"{name} queued an effect: a compute_ method only answers")
        if not is_plain(result):
            raise problem(f"{name} returned {type(result).__name__}: a compute_ method answers plain data")
    except Exception as e:
        outcome.update(failure(e))
    else:
        outcome["result"] = result
    finally:
        STATE["decimals"] = None
    return outcome


def subject_view(reference):
    """The view ``ggg.<Entity>.get(id)`` gives of the row ``reference`` (``"Land:L-1"``) names."""
    kind, _, id = reference.partition(":")
    if kind not in STATE["entities"]:
        raise problem(f"{reference!r}: a chapter does not read {kind}")
    row = getattr(STATE["codex"].modules["ggg"], kind).get(id)
    if row is None:
        raise problem(f"{reference!r} is not found")
    return row


def problem(message):
    """A ``LegislationError`` to raise for what a chapter's method did wrong, in the library's own class."""
    from realms.legislation.errors import LegislationError

    return LegislationError(message)
