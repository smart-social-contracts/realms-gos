import base64
import json

import chain
import sandbox
from db import Fail, each, find, view
from ggg import Codex, Realm
from op import op
from proposals import realm_wide

API = 2  # the hook contract codices declare as `codex_api_version`
SHOWN = ("id", "version", "status", "checksum", "capabilities", "installed_at", "previous")
REALM_FIELDS = ("name", "status", "accounting_currency", "accounting_currency_decimals", "is_capital", "is_quarter",
                "open_registration")


def active():
    return next((c for c in each(Codex) if c.status == "active"), None)


def merge(base, over):
    return {**base, **{k: merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
                       for k, v in over.items()}}


def config(codex):
    """The codex manifest with this realm's parameter choices applied."""
    return merge(codex.manifest or {}, (Realm["1"].manifest_data or {}).get("config_overrides", {}))


def checksum(manifest, source):
    return sandbox.sha256(json.dumps(manifest, sort_keys=True) + "\n" + source)


def trust(digest, approval):
    """I-EXT-3: new code needs the trust policy: an allowlisted checksum, or an approval by a trusted approver."""
    realm = Realm["1"]
    if not realm.require_marketplace_approval:
        return
    pins = [a for a in realm.trusted_approvers or [] if a.startswith("sha256:")]
    approvers = [a for a in realm.trusted_approvers or [] if a not in pins] or [realm.marketplace_canister_id]
    if f"sha256:{digest}" in pins:
        return
    if approval is None:
        raise Fail(f"code sha256:{digest} is neither allowlisted nor approved by the marketplace")
    where = approval.get("namespace", "the codex")
    if not approval.get("approved"):
        raise Fail(f"{where} is not approved by the marketplace ({approval.get('status', 'unapproved')})")
    if approval.get("approver") not in approvers:
        raise Fail(f"{where} is approved by {approval.get('approver')}, whom this realm does not trust")


def fetch(name, version):
    """Manifest, hook source and marketplace approval of a published codex, from the file registry."""
    registry, namespace = Realm["1"].file_registry_canister_id, f"ext/{name}/{version}"
    if not registry:
        raise Fail("no file registry is configured")
    manifest = json.loads((yield from pull(registry, namespace, "manifest.json")))
    source = yield from pull(registry, namespace, "backend/hooks.py")
    approval = json.loads((yield from chain.call_text(registry, "get_namespace_approval_icc", namespace)))
    return manifest, source, approval


def pull(registry, namespace, path):
    reply = json.loads((yield from chain.call_text(registry, "get_file",
                                                   json.dumps({"namespace": namespace, "path": path}))))
    if "error" in reply:
        raise Fail(reply["error"])
    return base64.b64decode(reply["content_b64"]).decode()


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
    problem = {} if out["ok"] else {"codex": codex.id, "hook": name, "error": out["error"], "at": chain.now()}
    if realm.codex_install_state != problem:
        realm.codex_install_state = problem
    return out


@op("codex.install", governed=realm_wide)
def codex_install(c, args):
    """Install and activate a codex: ``{manifest, source}``, or ``{codex, version}`` from the file registry."""
    if "source" in args:
        manifest, source, approval = args["manifest"], args["source"], None
    else:
        manifest, source, approval = yield from fetch(args["codex"], args["version"])
    if manifest.get("codex_api_version") != API:
        raise Fail(f"this realm runs codex API {API}, not {manifest.get('codex_api_version')}")
    digest = checksum(manifest, source)
    trust(digest, approval)
    compile(source, "hooks.py", "exec")
    key, current = f"{manifest['id']}@{manifest['version']}", active()
    if current and current.id == key:
        raise Fail(f"{key} is already active")
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
    codex = active()
    if codex is None:
        raise Fail("no codex is installed")
    codex.status = "inactive"
    return {"codex": codex.id}


@op("codex.revert", governed=realm_wide)
def codex_revert(c, args):
    """Go back to the version the active codex replaced."""
    codex = active()
    if codex is None or not codex.previous:
        raise Fail("there is no previous codex to revert to")
    prior = find(Codex, codex.previous)
    codex.status, prior.status = "reverted", "active"
    return {"codex": prior.id}


@op("realm.data_view", read=True)
def codex_status(c, args):
    codex, problem = active(), Realm["1"].codex_install_state or {}
    problem = problem if codex and problem.get("codex") == codex.id else {}
    return {"codex": view(codex, *SHOWN) if codex else None, "healthy": codex is not None and not problem,
            "problem": problem, "hooks": (codex.manifest or {}).get("hooks", []) if codex else [],
            "config": config(codex) if codex else {},
            "history": [view(x, "id", "version", "status", "installed_at") for x in each(Codex)]}
