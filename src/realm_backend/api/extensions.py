import base64
import json

from core import chain
from core.candid import quote
from core import sandbox
from core.db import Fail, Forbidden, Unavailable, find, page, view
from ggg import Department, Extension, Realm, User, UserProfile
from core.op import dispatch, op, run
from api.proposals import appointed, realm_wide

SIDEBAR = ("show_in_sidebar", "sidebar_label", "categories", "icon")
# What the frontend needs from the installed manifest to host an extension.
HOST = ("description", "runtime", "sdk_version", "capabilities", "entry_access", "backend")


def registry():
    cid = Realm["1"].file_registry_canister_id
    if not cid:
        raise Unavailable("no file registry is configured")
    return cid


def checksum(manifest, source):
    return sandbox.sha256(json.dumps(manifest, sort_keys=True) + "\n" + source)


def ask(method, namespace, path):
    arg = json.dumps({"namespace": namespace, "path": path})
    return json.loads((yield from chain.call_text(registry(), method, arg)))


def trust(digest, namespace=None, path=None):
    """I-EXT-3, I-ADM-3: code is accepted when ``sha256:<digest>`` is pinned in the realm's ``trusted_approvers``,
    or when the file registry reports ``namespace`` approved by a trusted approver (the other entries, by default
    the marketplace). With ``path``, that file of ``namespace`` must hash to ``digest``: the registry's approval
    holds only while the namespace's files are the ones approved, but it does not say which hash that is.
    A generator; returns the pin or the approver ("" when the realm requires no approval)."""
    realm = Realm["1"]
    if not realm.require_marketplace_approval:
        return ""
    pins = [a for a in realm.trusted_approvers or [] if a.startswith("sha256:")]
    approvers = [a for a in realm.trusted_approvers or [] if a not in pins] or [realm.marketplace_canister_id]
    if f"sha256:{digest}" in pins:
        return f"sha256:{digest}"
    if namespace is None:
        raise Forbidden(f"code sha256:{digest} is neither allowlisted nor approved by the marketplace")
    if path is not None:
        found = (yield from ask("get_file_size", namespace, path)).get("sha256")
        if not found or found != digest:
            raise Fail(f"{namespace}/{path} is sha256:{found or 'missing'}, not sha256:{digest}")
    approval = json.loads((yield from chain.call_text(registry(), "get_namespace_approval_icc", namespace)))
    if not approval.get("approved"):
        raise Forbidden(f"{namespace} is not approved by the marketplace "
                   f"({approval.get('status') or approval.get('error') or 'unapproved'})")
    if approval.get("approver") not in approvers:
        raise Forbidden(f"{namespace} is approved by {approval.get('approver')}, whom this realm does not trust")
    return approval["approver"]


def fetch(namespace, path):
    reply = yield from ask("get_file", namespace, path)
    if "content_b64" not in reply:
        raise Fail(f"{namespace}/{path}: {reply.get('error')}")
    return base64.b64decode(reply["content_b64"]).decode()


def publish_frontend(namespace, prefix):
    """Copy the registry's ``frontend/**`` files of ``namespace`` into the realm's frontend under ``prefix``, where
    the realm frontend loads extension bundles from. The realm holds ``Commit`` on its frontend for this. Nothing to
    do when the realm has no frontend of its own. A generator; returns how many files it copied."""
    frontend = Realm["1"].frontend_canister_id
    if not frontend:
        return 0
    files = json.loads((yield from chain.call_text(registry(), "list_files", json.dumps({"namespace": namespace}))))
    copied = 0
    for entry in files:
        if not entry["path"].startswith("frontend/"):
            continue
        data, offset, eof = b"", 0, False
        while not eof:  # the registry hands out at most 128 KB per call
            arg = json.dumps({"namespace": namespace, "path": entry["path"], "offset": offset})
            reply = json.loads((yield from chain.call_text(registry(), "get_file_chunk", arg)))
            if "content_b64" not in reply:
                raise Fail(f"{namespace}/{entry['path']}: {reply.get('error')}")
            data += base64.b64decode(reply["content_b64"])
            offset, eof = offset + reply["length"], reply["eof"] or reply["length"] == 0
        content = data.hex()
        content = "".join("\\" + content[i:i + 2] for i in range(0, len(content), 2))
        yield from chain.call(frontend, "store", f"(record {{ key = {quote(prefix + entry['path'])}; "
                              f"content_type = {quote(entry.get('content_type') or 'application/octet-stream')}; "
                              f'content_encoding = "identity"; content = blob "{content}"; sha256 = null }})')
        copied += 1
    return copied


def holds(principal):
    """The profiles and departments through which ``principal`` gets extensions."""
    user = User[principal]
    return ({p.id for p in user.profiles} if user else set(),
            {a.position.department.id for a in appointed(principal) if a.position.department})


def available(ext, held):
    """Whether ``held`` (from ``holds``) gets ``ext``. An extension open to no profile and no department, as one
    whose manifest lists no ``profiles`` (the public dashboard), is open to everyone, visitors included."""
    access, (profiles, departments) = ext.access or {}, held
    wanted, depts = set(access.get("profiles", [])), set(access.get("departments", []))
    return not (wanted or depts) or bool(profiles & wanted or departments & depts)


@op("extension.install", governed=realm_wide)
def extension_install(c, args):
    """Install or upgrade ``extension`` at ``version`` from the file registry (``ext/<extension>/<version>``).
    Its manifest's ``operations`` are the only realm operations its frontend and backend may call; its
    ``backend/entry.py`` is installed when the manifest sets ``backend``, and its ``frontend/`` files are copied
    to the realm's frontend under ``/ext/<extension>/<version>/``. Returns ``approved_by``: if the realm
    requires marketplace approval, its checksum must be pinned or an approver it trusts must have approved it."""
    namespace = f"ext/{args['extension']}/{args['version']}"
    manifest = json.loads((yield from fetch(namespace, "manifest.json")))
    source = (yield from fetch(namespace, "backend/entry.py")) if manifest.get("backend") else ""
    approver = yield from trust(checksum(manifest, source), namespace)
    yield from publish_frontend(namespace, f"/{namespace}/")
    ext = Extension[args["extension"]] or Extension(
        id=args["extension"], access={"profiles": manifest.get("profiles", ["member"]), "departments": []},
        sidebar={k: manifest[k] for k in SIDEBAR if k in manifest})
    ext.version, ext.status, ext.manifest, ext.source = args["version"], "installed", manifest, source
    ext.operations = list(manifest.get("operations", []))
    return {"extension": ext.id, "version": ext.version, "approved_by": approver}


@op("extension.uninstall", governed=realm_wide)
def extension_uninstall(c, args):
    """Remove ``extension``, with its access and menu settings."""
    find(Extension, args["extension"]).delete()


@op("extension.call")
def extension_call(c, args):
    """Run ``function`` of ``extension``'s backend with ``args`` (its keyword arguments); it can call only its declared
    operations, as you (I-EXT-1). The extension must be available to you. Returns ``result``."""
    ext = find(Extension, args["extension"])
    if not ext.source:
        raise Fail(f"{ext.id} has no backend")
    if not available(ext, holds(c.user)):
        raise Forbidden(f"{ext.id} is not available to you")
    declared = ext.operations

    def rpc(name, payload):
        return json.loads(run(dispatch(name, json.dumps(payload), origin="extension", signer=c.user,
                                       declared=declared)))

    return {"result": sandbox.execute(ext.source, args["function"], args.get("args") or {}, rpc, declared)}


@op("realm.configure", governed=realm_wide)
def extension_set_access(c, args):
    """Who gets ``extension``: ``profiles`` and ``departments``.
    Both lists replace the current ones; one left out becomes empty. Both empty opens it to everyone."""
    ext = find(Extension, args["extension"])
    profiles, departments = list(args.get("profiles", [])), list(args.get("departments", []))
    for p in profiles:
        find(UserProfile, p)
    for d in departments:
        find(Department, d)
    ext.access = {"profiles": profiles, "departments": departments}


@op("realm.configure", governed=realm_wide)
def sidebar_configure(c, args):
    """Where ``extension`` shows in the menu: ``sidebar`` (label, category, icon, order, show), merged into its
    current settings."""
    ext = find(Extension, args["extension"])
    ext.sidebar = {**ext.sidebar, **args["sidebar"]}


@op(None, read=True, public=True)
def extension_list(c, args):
    """Installed extensions with whether each is available to you (visitors included: the menu and the public
    dashboard need it), ``count`` rows at a time (default 100, at most
    500) from row ``from`` (default 1). Returns ``extensions`` and ``next``, the ``from`` of the next page (null at the
    end); a page can be short where extensions were uninstalled, so follow ``next``."""
    rows, nxt = page(Extension, args)
    held = holds(c.user)
    return {"extensions": [{**view(e, "id", "version", "status", "operations", "sidebar", "access"),
                            "manifest": {k: v for k, v in (e.manifest or {}).items() if k in HOST},
                            "available": available(e, held)} for e in rows], "next": nxt}
