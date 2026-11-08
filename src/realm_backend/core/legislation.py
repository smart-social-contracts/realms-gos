"""The runtime of a layered codex (docs/codices/layered-codex.md §9.3): the active codex's chapters run in one
resident codex subinterpreter, never in the realm's own interpreter, and the realm asks it around every GGG operation.

- ``build(plan, sources)``: the install's step 5 (§9.2): every chapter of a codex loaded into a fresh sandbox, top
  to bottom, the in-canister checks of §12.2 run, and the flattened view, the generated Cedar and the errors returned.
- ``load(codex)``, then ``activate(codex)``: at the switch (§9.2 step 6): the codex becomes the running one, which
  fails before the switch when it does not load, and then the ``on_codex_activate`` hooks run, once per realm (not
  in a quarter). A revert, and the realm's start, call ``warm`` instead, which runs no hook and never raises.
- ``run(op, ...)``: around a GGG operation: the guards ``check_<op>`` before it, the hooks ``on_<op>`` after it
  succeeds. ``compute(op, ...)`` answers ``compute_<op>`` when an operation asks during its work.

Loading: after an upgrade (``warm``, from setup.start) or on first use, the active codex's chapters are read from
``ChapterSource`` and loaded through core.legislation_sandbox, the program of the subinterpreter. Failures: a guard
that raises denies ("legislation error in <chapter>"), a hook that raises never fails its caller; both count per
chapter and method on the codex row (``failures``), which ``codex_status`` shows, and an entry clears when that
method next succeeds. Nothing reverts by itself. Reserved operations (§10.4) run no chapter, nor does a proposal
about one. Effects go through the codex caller (origin ``codex``) like a legacy codex's; a chapter's own effects
run no legislation again.
"""

import json

from core import chain
from core import civil
from core import localtime
from core import sandbox
from core.db import PAGE, Fail, Forbidden, Row, first, view
from core.op import GENERATOR, SECRET, Call, ops
import core.legislation_sandbox  # noqa: F401  bundled with the realm for its text (core.sandbox.module_source); never run here

# The operations no chapter may hook, guard or forbid, as realms.legislation.chapter.RESERVED lists them; written here
# too because the realm never compiles the library in its own interpreter (a test keeps the two equal).
RESERVED = ("codex_revert", "codex_revert_fast", "it_team_set", "sync_source_remove")
ACTIONS = ("ggg_read", "local_day")  # what a chapter may ask the realm, through the sandbox's rpc
# GGG operations the runtime raises as events itself, before an operation of that name exists in this realm: the
# switch to a codex is GGG's codex_activate (§9.2 step 6), which the install task performs.
EVENTS = ("codex_activate",)
LIBRARY, PROGRAM = "realms.legislation", "core.legislation_sandbox"
UNKNOWN = object()  # the running codex before the first look in a new heap
# codex: the id of the codex the runtime runs (None: a legacy codex or none); box: its sandbox, None when it did not
# load; failing: whether its row records failures, so a success need not read the row to clear one.
_state = {"codex": UNKNOWN, "box": None, "failing": False}


def active():
    """The active layered codex, or None."""
    from ggg import LayeredCodex

    return first(LayeredCodex, "status", "active")


def warm(codex=UNKNOWN):
    """Run ``codex`` (the active layered codex by default), recording a codex that does not load instead of
    raising: where the realm cannot stop, at its start (init and post_upgrade, as the loaded sandbox is gone after
    an upgrade and a query could not keep one it loads) and after a revert. Until a codex that does not load is
    reverted, every operation its legislation could guard denies."""
    codex = active() if codex is UNKNOWN else codex
    try:
        load(codex)
    except Fail as e:
        _drop()
        _state.update(codex=codex.id, failing=bool(codex.failures))
        _record("codex", "load", str(e))


def load(codex):
    """Make ``codex`` (a LayeredCodex row, or None for none) the one the runtime runs, its chapters loaded into a
    fresh sandbox, and run no hook. A codex that does not load raises Fail and leaves the running one as it was:
    the install task calls this before the switch, so that a switch to it never happens (§9.2 step 6)."""
    box = _spawn(_plan_of(codex), _texts(codex)) if codex is not None else None
    _drop()
    _state.update(codex=codex.id if codex is not None else None, box=box, failing=bool(codex.failures) if codex else False)
    if codex is not None:
        _clear(["codex"], "load")


def build(plan, sources, facts=None):
    """Install step 5 (§9.2): load every chapter of ``plan`` (realms.legislation's ``Plan``, or a dict with its
    ``realm``, ``layers``, ``chapters`` and ``calendars``) from ``sources`` (checksum to text) into a fresh sandbox,
    top to bottom, and run the in-canister checks (§12.2). Returns ``{"views", "cedar", "errors"}``: the flattened
    view per domain, the generated Cedar when GGG's fact schema ``facts`` is given (D4 validates it), and the errors
    (the first refusal; nothing else is loaded after it)."""
    box = None
    try:
        box = _spawn(plan if isinstance(plan, dict) else vars(plan), sources, facts)
        out = {"views": box.call("views"), "cedar": _ok(box.call("cedar"))["cedar"] if facts else None, "errors": []}
    except Fail as e:
        out = {"views": {}, "cedar": None, "errors": [str(e)]}
    finally:
        if box is not None:
            box.close()
    return out


def activate(codex):
    """At the switch (§9.2 step 6): every ``on_codex_activate`` hook of ``codex`` runs, once per realm: in the
    capital, not in a quarter. ``codex`` is loaded first unless ``load`` already did. A generator."""
    if _state["codex"] != codex.id or _state["box"] is None:
        load(codex)
    from ggg import Realm

    if Realm["1"].is_quarter:
        return
    realm = chain.self_id()
    yield from _hooks("codex_activate", {"codex": codex.id}, {"codex": codex.id}, Call(realm, realm, "codex"), None)


def run(op, spec, call, args, department):
    """Run the operation ``spec`` (``core.op``'s record of ``op``) for ``call`` with ``args``, under the legislation:
    its guards first, then the operation, then its hooks once it succeeded. ``department`` is the one deciding a
    governed call directly, if any. A generator, as the operation and the effects may call other canisters."""
    legislated = not spec["read"] and call.origin != "codex" and not _reserved(op, args) and _ensure()
    if legislated:
        _guard(op, args, call, department)
    out = spec["fn"](call, args)
    if isinstance(out, GENERATOR):
        out = yield from out
    out = out or {}
    if legislated:
        yield from _hooks(op, args, out, call, department)
    return out


def compute(op, **kwargs):
    """The running codex's answer to ``compute_<op>`` for ``kwargs``, or None when no chapter defines it or no
    layered codex is active. A ``compute_`` method that fails fails the operation asking, and counts."""
    if not _ensure():
        return None
    method = f"compute_{op}"
    try:
        outcome = _call("compute", op=op, kwargs=kwargs, now=chain.now(), approvals=[])
    except Fail as e:
        _record("codex", method, str(e))
        raise Fail(f"legislation error: {e}")
    _clear(["codex"], method)
    if not outcome["found"]:
        return None
    if "error" in outcome:
        file = outcome["chapter"] or outcome["files"][-1]
        _record(file, method, outcome["error"])
        raise Fail(f"legislation error in {file}: {outcome['error']}")
    _clear(outcome["files"], method)
    return outcome["result"]


def _ensure():
    """Whether a layered codex is active, loading it on first use: a new heap, or a query after one."""
    if _state["codex"] is UNKNOWN:
        warm()
    return _state["codex"] is not None


def _reserved(op, args):
    """Whether ``op`` is a reserved operation, or a proposal operation about one (§10.4): no chapter runs for it."""
    if op in RESERVED:
        return True
    action = None
    if op == "proposal_create":
        action = args.get("action")
    elif op.startswith("proposal_") and isinstance(args.get("proposal"), str):
        from ggg import Proposal

        proposal = Proposal[args["proposal"]]
        action = proposal.action if proposal is not None else None
    return isinstance(action, dict) and action.get("op") in RESERVED


def _approvals(call, department):
    """The departments whose approval the operation carries: the one deciding it directly, or the one whose
    accepted proposal it replays."""
    if department is not None:
        return [department.id]
    if call.proposal:
        from ggg import Proposal

        proposal = Proposal[call.proposal]
        if proposal is not None and proposal.department is not None:
            return [proposal.department.id]
    return []


def _guard(op, args, call, department):
    """The guards ``check_<op>``: a denial refuses the operation with the guard's reason; a guard that raises, or a
    sandbox that fails, refuses it too (fail closed), naming the chapter, and counts."""
    method = f"check_{op}"
    try:
        outcomes = _call("guard", op=op, args=args, now=chain.now(), approvals=_approvals(call, department))
    except Fail as e:
        _record("codex", method, str(e))
        raise Forbidden(f"legislation error: {e}")
    _clear(["codex"], method)
    for outcome in outcomes:
        if "error" in outcome:
            file = outcome["chapter"] or outcome["files"][-1]
            _record(file, method, outcome["error"])
            raise Forbidden(f"legislation error in {file}: {outcome['error']}")
        _clear(outcome["files"], method)
        if "denied" in outcome:
            raise Forbidden(outcome["denied"])


def _hooks(op, args, result, call, department):
    """The hooks ``on_<op>`` after the operation, with GGG's event, and their effects applied in order. A hook that
    raises, or an effect that fails, never fails the caller: it counts against the chapter. A generator."""
    method, event = f"on_{op}", {"user": call.user, "args": args, "result": result, "at": chain.now()}
    try:
        outcomes = _call("hooks", op=op, event=event, now=chain.now(), approvals=_approvals(call, department))
    except Fail as e:
        _record("codex", method, str(e))  # the sandbox itself failed, a spent budget twice over among the causes
        return
    _clear(["codex"], method)
    for outcome in outcomes:
        if "error" in outcome:
            _record(outcome["chapter"] or outcome["files"][-1], method, outcome["error"])
            continue
        errors = yield from _effects(outcome["effects"])
        if errors:
            _record(outcome["files"][-1], method, "; ".join(errors))
        else:
            _clear(outcome["files"], method)


def _effects(effects):
    """Apply a hook's queued effects in order, as the codex: GGG operations, with ``$eff`` references to earlier
    results resolved as a legacy codex's are. A reserved operation, and an effect that would set a time zone
    (§4.5), are refused. Returns the errors. A generator."""
    results, errors = [], []
    for name, args in effects:
        args = sandbox.resolve(args, results)
        if name in RESERVED:
            reply = {"ok": False, "error": f"{name} is reserved: no chapter may call it"}
        elif "timezone" in args:
            reply = {"ok": False, "error": "a chapter never sets a time zone"}
        else:
            reply = yield from sandbox.apply(name, args, (name,))
        results.append(reply)
        if not reply.get("ok"):
            errors.append(f"{name}: {reply.get('error')}")
    return errors


def _call(fn, **kwargs):
    """Ask the running codex's sandbox. A spent budget reloads the codex once and asks again, so a long-lived
    sandbox never fails a call for the calls before it; a sandbox that fails otherwise, or a codex that did not
    load, raises Fail."""
    box = _state["box"]
    if box is None:
        raise Fail(f"the codex {_state['codex']} is not loaded")
    try:
        return box.call(fn, **kwargs)
    except RuntimeError as e:
        if "BudgetExceeded" not in str(e):
            raise Fail(f"the codex {_state['codex']} failed: {e}")
    from ggg import LayeredCodex

    warm(LayeredCodex[_state["codex"]])
    if _state["box"] is None:
        raise Fail(f"the codex {_state['codex']} is not loaded")
    try:
        return _state["box"].call(fn, **kwargs)
    except RuntimeError as e:
        raise Fail(f"the codex {_state['codex']} failed: {e}")


def _spawn(plan, sources, facts=None):
    """A sandbox with the chapters of ``plan`` loaded from ``sources`` and checked. Raises Fail with the first
    refusal, in the form the checker prints (§5.5)."""
    program, _ = sandbox.module_source(PROGRAM)
    if sandbox.NATIVE:  # the subinterpreter imports the library from text; a laptop imports it as it is
        program += f"\nLIBRARY = {sandbox.module_sources(LIBRARY)!r}\n"
    box = sandbox.Resident(program, _answer, ACTIONS)
    try:
        _ok(box.call("setup", operations=sorted(set(ops()) | set(EVENTS)), layers=list(plan["layers"]), realm=plan["realm"],
                     calendars=[[c["source"], _calendar(sources, c)] for c in plan["calendars"]],
                     entities=_entities(), facts=facts))
        for c in plan["chapters"]:
            _ok(box.call("load", source=c["source"], domain=c["domain"], text=_text(sources, c)))
        _ok(box.call("finish"))
    except BaseException:
        box.close()
        raise
    return box


def _ok(reply):
    if not reply["ok"]:
        raise Fail(reply["error"])
    return reply


def _plan_of(codex):
    """What the sandbox loads for a stored codex: its layers, its own source (the last layer), the chapters of each
    layer path top to bottom, and the calendars (``<source>/calendar`` entries)."""
    chapters = codex.chapters
    return {
        "realm": codex.layers[-1], "layers": codex.layers,
        "chapters": [{"source": source, "domain": domain, "sha256": chapters[f"{source}/{domain}"]["sha256"]}
                     for domain in sorted(codex.layer_paths) for source in codex.layer_paths[domain]],
        "calendars": [{"source": source, "sha256": chapters[f"{source}/calendar"]["sha256"]}
                      for source in codex.layers if f"{source}/calendar" in chapters],
    }


def _texts(codex):
    """The stored text of every chapter and calendar of ``codex``, by checksum."""
    from ggg import ChapterSource

    found = {}
    for entry in codex.chapters.values():
        row = ChapterSource[entry["sha256"]]
        if row is None:
            raise Fail(f"the text of {entry['sha256']} is not stored")
        found[entry["sha256"]] = row.source
    return found


def _text(sources, entry):
    if entry["sha256"] not in sources:
        raise Fail(f"{entry['source']}/{entry.get('domain', 'calendar')}: the bytes of {entry['sha256']} are missing")
    return sources[entry["sha256"]]


def _calendar(sources, entry):
    try:
        return json.loads(_text(sources, entry))
    except ValueError as e:
        raise Fail(f"{entry['source']}/calendar.json is not JSON: {e}")


def _entities():
    """The GGG entities a chapter may read: every entity the realm stores."""
    import ggg

    return sorted(name for name, cls in vars(ggg).items() if isinstance(cls, type) and issubclass(cls, Row))


def _answer(action, kw):
    """What the sandbox asks the realm: a read of GGG's entities, or the realm's local day of a timestamp."""
    if action == "local_day":
        day, _ = localtime.local_day(int(kw["at"]) * localtime.NS)
        year, month, dom = civil.civil_from_days(day)
        return f"{year:04d}-{month:02d}-{dom:02d}"
    if action == "ggg_read":
        return _read(kw)
    raise Fail(f"a chapter cannot ask the realm for {action!r}")


def _read(kw):
    """One row by id, or one page of rows (all, or those whose indexed ``field`` is ``value``), as plain data
    without the fields the audit keeps secret."""
    import ggg

    cls = getattr(ggg, kw["entity"], None)
    if not (isinstance(cls, type) and issubclass(cls, Row)):
        raise Fail(f"{kw['entity']!r} is not a GGG entity")
    if kw["method"] == "get":
        row = cls[str(kw["id"])]
        return None if row is None else _row(row)
    start = int(kw.get("start") or 1)
    if kw["method"] == "page":
        rows, after = cls.load_some(start, PAGE), start + PAGE
        after = after if after <= cls.max_id() else None
    elif kw["method"] == "find":
        rows, after = cls.find_by(kw["field"], kw["value"], from_id=start, count=PAGE)
    else:
        raise Fail(f"a chapter cannot {kw['method']!r} an entity")
    return {"rows": [_row(r) for r in rows], "next": after}


def _row(row):
    return {k: v for k, v in view(row).items() if k not in SECRET}


def _record(file, method, error):
    """One more failure of ``method`` of chapter ``file``, with its error, on the running codex's row."""
    codex = _row_of_running()
    if codex is None:
        return
    failures = codex.failures
    count = failures.get(file, {}).get(method, {}).get("count", 0)
    failures.setdefault(file, {})[method] = {"count": count + 1, "error": error[:500], "at": chain.now()}
    codex.failures, _state["failing"] = failures, True


def _clear(files, method):
    """``method`` succeeded: the failures recorded for it on any of ``files`` are over."""
    if not _state["failing"]:
        return
    codex = _row_of_running()
    if codex is None:
        return
    failures = codex.failures
    for file in files:
        if method in failures.get(file, {}):
            del failures[file][method]
            if not failures[file]:
                del failures[file]
    codex.failures, _state["failing"] = failures, bool(failures)


def _row_of_running():
    from ggg import LayeredCodex

    return LayeredCodex[_state["codex"]] if isinstance(_state["codex"], str) else None


def _drop():
    if _state["box"] is not None:
        _state["box"].close()
    _state.update(box=None, failing=False)
