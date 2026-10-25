import base64
import json

import chain
import sandbox
from db import Fail, each, find, view
from ggg import Department, Extension, Realm, User, UserProfile
from op import dispatch, op, run
from proposals import appointed, realm_wide

SIDEBAR = ("show_in_sidebar", "sidebar_label", "categories", "icon")


def registry():
    cid = Realm["1"].file_registry_canister_id
    if not cid:
        raise Fail("no file registry is configured")
    return cid


def approved(namespace):
    """Who approved ``namespace`` under the realm's trust policy (I-EXT-3); refuses anything else."""
    realm = Realm["1"]
    if not realm.require_marketplace_approval:
        return ""
    reply = json.loads((yield from chain.call_text(registry(), "get_namespace_approval_icc", namespace)))
    if not reply.get("approved"):
        raise Fail(f"{namespace} is not approved by the marketplace ({reply.get('status') or reply.get('error')})")
    trusted = realm.trusted_approvers or [realm.marketplace_canister_id]
    if reply.get("approver") not in trusted:
        raise Fail(f"{namespace} is approved by {reply.get('approver')}, whom this realm does not trust")
    return reply["approver"]


def fetch(namespace, path):
    reply = json.loads((yield from chain.call_text(registry(), "get_file",
                                                    json.dumps({"namespace": namespace, "path": path}))))
    if "content_b64" not in reply:
        raise Fail(f"{namespace}/{path}: {reply.get('error')}")
    return base64.b64decode(reply["content_b64"]).decode()


def available(ext, principal):
    access, user = ext.access or {}, User[principal]
    profiles = {p.id for p in user.profiles} if user else set()
    departments = {a.position.department.id for a in appointed(principal) if a.position.department}
    return bool(profiles & set(access.get("profiles", [])) or departments & set(access.get("departments", [])))


@op("extension.install", governed=realm_wide)
def extension_install(c, args):
    """Install or upgrade an extension from the file registry (``ext/<extension>/<version>``)."""
    namespace = f"ext/{args['extension']}/{args['version']}"
    approver = yield from approved(namespace)
    manifest = json.loads((yield from fetch(namespace, "manifest.json")))
    source = (yield from fetch(namespace, "backend/entry.py")) if manifest.get("operations") else ""
    ext = Extension[args["extension"]] or Extension(
        id=args["extension"], access={"profiles": manifest.get("profiles", ["member"]), "departments": []},
        sidebar={k: manifest[k] for k in SIDEBAR if k in manifest})
    ext.version, ext.status, ext.manifest, ext.source = args["version"], "installed", manifest, source
    ext.operations = list(manifest.get("operations", []))
    return {"extension": ext.id, "version": ext.version, "approved_by": approver}


@op("extension.uninstall", governed=realm_wide)
def extension_uninstall(c, args):
    find(Extension, args["extension"]).delete()


@op("extension.call")
def extension_call(c, args):
    """Run ``function`` of an extension's backend; it can call only its declared operations, as you (I-EXT-1)."""
    ext = find(Extension, args["extension"])
    if not ext.source:
        raise Fail(f"{ext.id} has no backend")
    if not available(ext, c.user):
        raise Fail(f"{ext.id} is not available to you")
    declared = ext.operations

    def rpc(name, payload):
        return json.loads(run(dispatch(name, json.dumps(payload), origin="extension", signer=c.user,
                                       declared=declared)))

    return {"result": sandbox.execute(ext.source, args["function"], args.get("args") or {}, rpc, declared)}


@op("realm.configure", governed=realm_wide)
def extension_set_access(c, args):
    """Who gets an extension: ``profiles`` and ``departments``."""
    ext = find(Extension, args["extension"])
    profiles, departments = list(args.get("profiles", [])), list(args.get("departments", []))
    for p in profiles:
        find(UserProfile, p)
    for d in departments:
        find(Department, d)
    ext.access = {"profiles": profiles, "departments": departments}


@op("realm.configure", governed=realm_wide)
def sidebar_configure(c, args):
    """Where an extension shows in the menu: ``sidebar`` (label, category, icon, order, show)."""
    ext = find(Extension, args["extension"])
    ext.sidebar = {**ext.sidebar, **args["sidebar"]}


@op("realm.data_view", read=True)
def extension_list(c, args):
    """Installed extensions, with whether each is available to you."""
    return {"extensions": [{**view(e, "id", "version", "status", "operations", "sidebar", "access"),
                            "available": available(e, c.user)} for e in each(Extension)]}
