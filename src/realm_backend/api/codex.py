import json

from core import chain
from core import layered_codex as store
from core import legislation
from core import sandbox
from core.db import Conflict, Fail, find, first, latest, view, window
from ggg import Codex, InstallJob, LayeredCodex, Realm
from core.op import op
from api import extensions
from api.extensions import checksum, trust
from api.proposals import realm_wide

API = 2  # the hook contract codices declare as `codex_api_version`
SHOWN = ("id", "version", "status", "checksum", "capabilities", "installed_at", "previous")
LAYERED_SHOWN = ("id", "status", "layers", "layer_paths", "chapters", "previous", "installed_at", "activates_at",
                 "proposal")
JOB_SHOWN = ("id", "proposal", "step", "status", "error", "started_at", "finished_at")
REALM_FIELDS = ("name", "status", "accounting_currency", "accounting_currency_decimals", "is_capital", "is_quarter",
                "open_registration")


def active_legacy():
    """The active codex when it is a legacy one: the kind with a manifest and hooks."""
    return first(Codex, "status", "active")


def active():
    """The codex the realm runs, of either kind: a legacy ``Codex`` or a ``LayeredCodex``. At most one is active."""
    return active_legacy() or first(LayeredCodex, "status", "active")


def pending():
    """The layered codex installed and waiting for ``codex_activate`` or its time, if any; at most one waits at a time."""
    return first(LayeredCodex, "status", "pending")


def find_codex(codex_id):
    """A codex of either kind by its id: ``name@version`` for a legacy one, ``sha256:…`` for a layered one."""
    return find(Codex if "@" in codex_id else LayeredCodex, codex_id)


def runnable(manifest):
    if manifest.get("codex_api_version") != API:
        raise Fail(f"this realm runs codex API {API}, not {manifest.get('codex_api_version')}")


def merge(base, over):
    return {**base, **{k: merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
                       for k, v in over.items()}}


def config(codex):
    """The codex manifest with this realm's parameter choices applied."""
    return merge(codex.manifest or {}, (Realm["1"].manifest_data or {}).get("config_overrides", {}))


def fetch(namespace):
    """Manifest and hook source of a published codex, from the file registry."""
    manifest = json.loads((yield from extensions.fetch(namespace, "manifest.json")))
    return manifest, (yield from extensions.fetch(namespace, "backend/hooks.py"))


def hook(name, context=None):
    """Run hook ``name`` of the active codex with ``context`` and apply its effects.

    A generator (``out = yield from codex.hook(...)``). Returns None when no codex is active or it has no such
    hook, else ``{ok, result, effects}`` or ``{ok: false, error}``. A failing hook never fails the caller; it
    marks the codex unhealthy until a hook succeeds again.
    """
    codex = active_legacy()
    if codex is None or name not in (codex.manifest or {}).get("hooks", []):
        return None
    realm = Realm["1"]
    facts = {"codex": codex.id, "config": config(codex), "now": chain.now(), "realm": view(realm, *REALM_FIELDS)}
    out = yield from sandbox.call(codex.source, name, context or {}, facts, codex.capabilities or [])
    failed = [e for e in out.get("effects", []) if not e.get("ok")]
    error = out.get("error") or "; ".join(f"{e['op']}: {e.get('error')}" for e in failed)
    problem = {"codex": codex.id, "hook": name, "error": error, "at": chain.now()} if error else {}
    if realm.codex_install_state != problem:
        realm.codex_install_state = problem
    return out


def compute(name, context):
    """The active codex's answer to GGG's ``compute_<name>`` for ``context`` (its keyword arguments), None when no
    chapter defines it. A generator, for the operations that drive it with ``yield from``."""
    return legislation.compute(name, **context)
    yield  # noqa: unreachable, on purpose


def switch_to(row, replaced):
    """Make ``row`` the active codex in place of ``replaced``, which is kept to revert to."""
    row.previous, row.status = replaced.id if replaced else "", "active"
    if replaced:
        replaced.status = "inactive"


def switch(waiting):
    """The switch of an install (docs/codices/layered-codex.md §9.2 step 6): the pending ``waiting`` becomes the
    active codex in place of the current one, kept to revert to, and its ``on_codex_activate`` hooks run. The
    runtime loads it first: a codex that does not load raises before anything changes. A generator."""
    legislation.load(waiting)
    switch_to(waiting, active())
    yield from legislation.activate(waiting)


@op("codex.install", governed=realm_wide, returns="codex")
def codex_install(c, args):
    """Install and activate a codex: ``manifest`` and ``source``, or ``codex`` and ``version`` from the file registry.
    The code must pass the trust policy; the replaced codex is kept to revert to. Returns ``codex`` and ``init``.
    A change list's ``url`` and ``checksum`` (``sha256:<hex>``, GGG's Code action) install a layered codex instead:
    the install task fetches, checks and builds it step by step (api.installs), and the call returns its ``job``; a
    proposal with an ``effective_at`` stages it at acceptance and switches at that time. One install at a time."""
    if "checksum" in args:
        from api import installs  # it imports this module

        return (yield from installs.begin(c, args))
    if store.live_job() is not None:
        raise Conflict(f"an install is {store.live_job().status}")
    if "source" in args:
        manifest, source, namespace = args["manifest"], args["source"], None
    else:
        namespace = f"ext/{args['codex']}/{args['version']}"
        manifest, source = yield from fetch(namespace)
    runnable(manifest)
    digest = checksum(manifest, source)
    yield from trust(digest, namespace)
    compile(source, "hooks.py", "exec")
    if len(sandbox.readable(manifest.get("capabilities", []))) > sandbox.MAX_ACTIONS:
        raise Fail(f"a codex may declare at most {sandbox.MAX_ACTIONS} read operations")
    key, current = f"{manifest['id']}@{manifest['version']}", active()
    if current and current.id == key:
        raise Conflict(f"{key} is already active")
    row = Codex[key] or Codex(id=key)
    row.version, row.checksum, row.manifest, row.source = manifest["version"], digest, manifest, source
    row.capabilities, row.installed_at = manifest.get("capabilities", []), chain.now()
    switch_to(row, current)
    if isinstance(current, LayeredCodex):
        legislation.load(None)
    init = yield from hook("init")
    return {"codex": key, "init": init}


@op("codex.uninstall", governed=realm_wide)
def codex_uninstall(c, args):
    """Deactivate the active codex; the realm then runs without one."""
    codex = active()
    if codex is None:
        raise Conflict("no codex is installed")
    codex.status = "inactive"
    if isinstance(codex, LayeredCodex):
        legislation.load(None)
    return {"codex": codex.id}


@op("codex.revert", governed=realm_wide)
def codex_revert(c, args):
    """Go back to the codex the active one replaced, of either kind, if this realm still runs its codex API."""
    codex = active()
    if codex is None or not codex.previous:
        raise Conflict("there is no previous codex to revert to")
    prior = find_codex(codex.previous)
    if isinstance(prior, Codex):
        runnable(prior.manifest or {})
    codex.status, prior.status = "reverted", "active"
    if isinstance(codex, LayeredCodex) or isinstance(prior, LayeredCodex):
        legislation.warm(prior if isinstance(prior, LayeredCodex) else None)  # a revert runs no on_codex_activate
    return {"codex": prior.id}


def shown(codex):
    return view(codex, *(SHOWN if isinstance(codex, Codex) else LAYERED_SHOWN))


def history(args):
    """One page of every codex the realm has had: the legacy ones, then the layered ones, each in install order.
    ``next`` is the ``from`` of the next page, None at the end."""
    start, count = window(args)
    legacy_count = Codex.max_id()
    rows = Codex.load_some(start, count) if start <= legacy_count else []
    if len(rows) < count:
        rows += LayeredCodex.load_some(max(start - legacy_count, 1), count - len(rows))
    return rows, (start + count if start + count <= legacy_count + LayeredCodex.max_id() else None)


@op("realm.data_view", read=True)
def codex_status(c, args):
    """The active codex, whether it is healthy (a legacy codex: its last hook succeeded; a layered codex: no chapter's
    method is failing, see ``failures``: per chapter and method, the count, the last error and when), its hooks and
    configuration, and one page of the install ``history``: ``count`` installs (default 100, at most 500) from row ``from`` (default 1), with
    ``next``, the ``from`` of the next page (null at the end). The history lists the legacy codices, then the layered
    ones. A layered codex shows its layers, layer paths and chapters (source, version, checksum, who vouched for it,
    route, fetch time), its status and ``activates_at``. ``pending`` is the layered codex waiting for its time, and
    ``job`` the install job running or paused, else the latest, with its step, status and error."""
    codex, problem = active(), Realm["1"].codex_install_state or {}
    problem = problem if codex and problem.get("codex") == codex.id else {}
    legacy_codex = codex if isinstance(codex, Codex) else None
    failures = codex.failures if isinstance(codex, LayeredCodex) else {}
    rows, nxt = history(args)
    waiting, newest = pending(), latest(InstallJob, 1)
    job = store.live_job() or (newest[0] if newest else None)
    return {"codex": shown(codex) if codex else None, "healthy": codex is not None and not problem and not failures,
            "problem": problem, "failures": failures, "hooks": (legacy_codex.manifest or {}).get("hooks", []) if legacy_codex else [],
            "config": config(legacy_codex) if legacy_codex else {},
            "history": [view(x, "id", "version", "status", "installed_at") for x in rows], "next": nxt,
            "pending": shown(waiting) if waiting else None,
            "job": view(job, *JOB_SHOWN) if job else None}
