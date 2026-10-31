import json

from core import chain
from core import sandbox
from core.db import Conflict, Fail, find, first, page, view
from ggg import Codex, Realm
from core.op import op
from api import extensions
from api.extensions import checksum, trust
from api.proposals import realm_wide

API = 2  # the hook contract codices declare as `codex_api_version`
SHOWN = ("id", "version", "status", "checksum", "capabilities", "installed_at", "previous")
REALM_FIELDS = ("name", "status", "accounting_currency", "accounting_currency_decimals", "is_capital", "is_quarter",
                "open_registration")


def active():
    return first(Codex, "status", "active")


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
    codex = active()
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


@op("codex.install", governed=realm_wide, returns="codex")
def codex_install(c, args):
    """Install and activate a codex: ``manifest`` and ``source``, or ``codex`` and ``version`` from the file registry.
    The code must pass the trust policy; the replaced codex is kept to revert to. Returns ``codex`` and ``init``."""
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
    row.previous, row.status = current.id if current else "", "active"
    if current:
        current.status = "inactive"
    init = yield from hook("init")
    return {"codex": key, "init": init}


@op("codex.uninstall", governed=realm_wide)
def codex_uninstall(c, args):
    """Deactivate the active codex; the realm then runs without one."""
    codex = active()
    if codex is None:
        raise Conflict("no codex is installed")
    codex.status = "inactive"
    return {"codex": codex.id}


@op("codex.revert", governed=realm_wide)
def codex_revert(c, args):
    """Go back to the version the active codex replaced, if this realm still runs its codex API."""
    codex = active()
    if codex is None or not codex.previous:
        raise Conflict("there is no previous codex to revert to")
    prior = find(Codex, codex.previous)
    runnable(prior.manifest or {})
    codex.status, prior.status = "reverted", "active"
    return {"codex": prior.id}


@op("realm.data_view", read=True)
def codex_status(c, args):
    """The active codex, whether it is healthy (its last hook succeeded), its hooks and configuration, and one page
    of the install ``history``: ``count`` installs (default 100, at most 500) from row ``from`` (default 1), with
    ``next``, the ``from`` of the next page (null at the end)."""
    codex, problem = active(), Realm["1"].codex_install_state or {}
    problem = problem if codex and problem.get("codex") == codex.id else {}
    rows, nxt = page(Codex, args)
    return {"codex": view(codex, *SHOWN) if codex else None, "healthy": codex is not None and not problem,
            "problem": problem, "hooks": (codex.manifest or {}).get("hooks", []) if codex else [],
            "config": config(codex) if codex else {},
            "history": [view(x, "id", "version", "status", "installed_at") for x in rows], "next": nxt}
