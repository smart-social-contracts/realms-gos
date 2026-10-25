import json

from core import chain
import ggg
from core import sandbox
from api import tasks
from core.db import Fail, page, view
from api.extensions import trust
from ggg import Calendar, Department, Event, Member, Realm, Registry, Treasury, User, UserProfile
from ic_python_db import Entity
from api import codex
from core.op import call, op, ops
from api.proposals import root

VERSION = "VERSION_PLACEHOLDER"
RELEASE_WASM = "realm_backend.wasm.gz"  # as `realms files publish-release` stores it

PROFILES = {
    "admin": ["all"],
    "member": [
        "self.join", "self.update_public_profile", "self.update_private_data", "self.change_quarter",
        "self.invoice_refresh", "self.data_view", "self.data_manage", "self.delegate", "realm.data_view",
        "proposal.create", "proposal.vote", "federal_vote.propose", "dispute.create", "dispute.view",
        "identity.submit", "license.apply", "tax.declare", "bid.submit", "extension.call",
    ],
    # Held by the founder only while the realm is in setup (I-ADM-2).
    "founder": [
        "realm.configure", "realm.configure.branding", "realm.configure.tokens", "realm.configure.infrastructure",
        "realm.configure.trust_policy", "realm.register", "codex.install", "extension.install",
    ],
}
INSTALL = ("network", "can_test_mode", "founder")
FIELDS = {
    "realm": ("name", "manifesto", "welcome_message", "open_registration", "ai_assistant_enabled",
              "quarter_join_mode", "auto_scale_enabled", "test_mode", "test_flags"),
    "branding": ("logo_url", "background_image_url", "primary_color"),
    "tokens": ("accounting_currency", "accounting_currency_decimals", "token_canister_id", "nft_canister_id",
               "shared_tokens_json"),
    "infrastructure": ("frontend_canister_id", "file_registry_canister_id", "marketplace_canister_id",
                       "installer_canister_id", "baton_canister_id"),
    "trust_policy": ("require_marketplace_approval", "trusted_approvers", "trusted_principals"),
}
HEX = "0123456789abcdef"
COUNTED = ("User", "Member", "Department", "Organization", "Proposal", "Extension", "Task", "Notification")
SHELL = """class _Ops:
    def __getattr__(self, name):
        return lambda args=None, **kw: rpc("op", name=name, args={**(args or {}), **kw})


ops = _Ops()


def main():
    return (%s)
"""


def start():
    # Load every operation here, where it is kept: a query loading modules would redo it on every call.
    ops()
    if Realm["1"] is None:
        realm = Realm(id="1", principal_id=chain.self_id())
        realm.calendar, realm.treasury = Calendar(id="1"), Treasury(id="1")
        Department(id="Administration", is_root=True, description="The realm's root department")
    for name, allowed in PROFILES.items():
        if UserProfile[name] is None:
            UserProfile(id=name, allowed_to=allowed)
    tasks.rearm()


@op(None, read=True, public=True)
def status(c, args):
    """The realm's ``version``, ``status``, ``name``, ``canister`` id and number of ``operations``."""
    realm = Realm["1"]
    return {"version": VERSION, "status": realm.status if realm else "", "name": realm.name if realm else "",
            "canister": chain.self_id(), "operations": len(ops())}


@op(None, read=True, public=True)
def version(c, args):
    """The ``version`` of the realm's code."""
    return {"version": VERSION}


@op(None, read=True, public=True)
def realm_info(c, args):
    """The realm's public profile: name, manifesto, welcome message, status, branding, accounting currency,
    whether registration is open and whether it is a capital."""
    realm = Realm["1"]
    return {"realm": view(realm, "name", "manifesto", "welcome_message", "status", "logo_url",
                          "background_image_url", "primary_color", "accounting_currency",
                          "open_registration", "is_capital") if realm else {}}


def launched(args):
    """Configuration is governed once the realm has launched; during setup the founder acts directly (I-ADM-2)."""
    return None if Realm["1"].status == "setup" else root()


def draft():
    return Realm["1"].manifest_data.get("setup", {"done": []})


def remember(key, value):
    realm = Realm["1"]
    realm.manifest_data = {**realm.manifest_data, key: value}


def configure(fields, values):
    realm, color = Realm["1"], values.get("primary_color")
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise Fail(f"cannot configure {', '.join(unknown)} here")
    if color and not (len(color) == 7 and color[0] == "#" and all(ch in HEX for ch in color[1:].lower())):
        raise Fail(f"primary_color must look like #1E6B52, not {color}")
    if (values.get("test_mode") or any((values.get("test_flags") or {}).values())) \
            and realm.network == "ic" and not realm.can_test_mode:
        raise Fail("test flags need a non-production network or can_test_mode set at install")
    for k, v in values.items():
        if k == "baton_canister_id":
            remember("casals", {**realm.manifest_data.get("casals", {}), k: v})
        else:
            setattr(realm, k, v)
    return {"configured": sorted(values)}


def install(values):
    """The installer's facts, set once: network, can_test_mode and the founder."""
    realm, unknown = Realm["1"], sorted(set(values) - set(INSTALL))
    if unknown:
        raise Fail(f"cannot set {', '.join(unknown)} at install")
    realm.network, realm.can_test_mode = values.get("network", realm.network), bool(values.get("can_test_mode"))
    founder = values.get("founder")
    if founder:
        user = User[founder] or User(id=founder)
        held = [*user.profiles, UserProfile["member"], UserProfile["founder"]]
        user.profiles = list({p.id: p for p in held}.values())
        Member[founder] or Member(id=founder, joined_at=chain.now())
    return founder or ""


@op("realm.configure")
def setup_state(c, args):
    """Setup progress: the realm's ``status``, its ``founder``, the steps ``done`` and ``todo``, and its settings."""
    realm, d = Realm["1"], draft()
    fields = [f for group in FIELDS.values() for f in group if f != "baton_canister_id"]
    return {"status": realm.status, "founder": d.get("founder", ""), "done": d["done"],
            "todo": [s for s in ("install", *FIELDS) if s not in d["done"]],
            "realm": view(realm, "network", "can_test_mode", *fields)}


@op("realm.configure")
def setup_step(c, args):
    """One setup step: ``step`` (install, realm, branding, tokens, infrastructure, trust_policy) and its values.
    The values are the fields of the matching realm_configure operation; install takes network, can_test_mode
    and founder."""
    step, values, d = args["step"], {k: v for k, v in args.items() if k != "step"}, draft()
    if Realm["1"].status != "setup":
        raise Fail("the realm has launched: configuration is governed now")
    if step == "install" and "install" in d["done"]:
        raise Fail("the install facts are set once, by the installer")
    if step != "install" and step not in FIELDS:
        raise Fail(f"unknown setup step '{step}'")
    if step == "install":
        d["founder"] = install(values)
    else:
        configure(FIELDS[step], values)
    d["done"] = sorted(set(d["done"]) | {step})
    remember("setup", d)
    return {"done": d["done"]}


@op("realm.configure")
def setup_launch(c, args):
    """Open the realm (status ``alpha``). The founder's setup rights end; configuration is governed from now on."""
    realm, d = Realm["1"], draft()
    if realm.status != "setup":
        raise Fail("the realm has already launched")
    if not realm.name:
        raise Fail("name the realm first")
    if d.get("founder"):
        founder = User[d["founder"]]
        founder.profiles = [p for p in founder.profiles if p.id != "founder"]
    realm.status = "alpha"
    remember("setup", {**d, "launched_at": chain.now()})
    yield from codex.hook("on_stage_change", {"stage": "alpha"})
    return {"status": realm.status}


@op("realm.configure", governed=launched)
def realm_configure(c, args):
    """Set any of ``name``, ``manifesto``, ``welcome_message``, ``open_registration``, ``ai_assistant_enabled``,
    ``quarter_join_mode``, ``auto_scale_enabled``, ``test_mode`` and ``test_flags``. Returns ``configured``.
    Test mode and flags need a non-production network or ``can_test_mode`` set at install."""
    return configure(FIELDS["realm"], args)


@op("realm.configure.branding", governed=launched)
def realm_configure_branding(c, args):
    """Set any of ``logo_url``, ``background_image_url`` and ``primary_color`` (like ``#1E6B52``)."""
    return configure(FIELDS["branding"], args)


@op("realm.configure.tokens", governed=launched)
def realm_configure_tokens(c, args):
    """Set any of ``accounting_currency``, ``accounting_currency_decimals``, ``token_canister_id``,
    ``nft_canister_id`` and ``shared_tokens_json``."""
    return configure(FIELDS["tokens"], args)


@op("realm.configure.infrastructure", governed=launched)
def realm_configure_infrastructure(c, args):
    """Set the canisters the realm uses, any of ``frontend_canister_id``, ``file_registry_canister_id``,
    ``marketplace_canister_id``, ``installer_canister_id`` and ``baton_canister_id`` (the Casals baton)."""
    return configure(FIELDS["infrastructure"], args)


@op("realm.configure.trust_policy", governed=launched)
def realm_configure_trust_policy(c, args):
    """Which code the realm accepts: ``require_marketplace_approval``, ``trusted_approvers``, ``trusted_principals``."""
    return configure(FIELDS["trust_policy"], args)


def text(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@op("realm.register", governed=launched)
def realm_register(c, args):
    """List the realm in a realm registry: ``registry`` (canister id), optional ``url`` and ``backend_url``."""
    realm = Realm["1"]
    ids = "|".join((realm.frontend_canister_id, realm.token_canister_id, realm.nft_canister_id))
    fields = (realm.name, args.get("url", ""), "", args.get("backend_url", ""), ids)
    reply = yield from chain.call(args["registry"], "register_realm", "(" + ", ".join(map(text, fields)) + ")")
    if "Err" in reply:
        raise Fail(f"the registry refused: {reply}")
    Registry[args["registry"]] or Registry(id=args["registry"], principal_id=args["registry"], description=realm.name)
    return {"registry": args["registry"]}


@op("realm.upgrade", governed=launched)
def realm_upgrade(c, args):
    """Accept release ``version`` with wasm ``module_hash`` (hex, optionally prefixed ``sha256:``) for Casals to
    install, if the trust policy accepts that exact module (I-ADM-3): its hash is pinned, or it is the release's
    backend wasm in the file registry (``wasm/realm-backend/<version>/realm_backend.wasm.gz``) and a trusted approver
    approved that namespace."""
    digest = args["module_hash"].removeprefix("sha256:")
    approver = yield from trust(digest, f"wasm/realm-backend/{args['version']}", RELEASE_WASM)
    release = {"version": args["version"], "module_hash": args["module_hash"], "approved_by": approver,
               "accepted_at": chain.now()}
    remember("upgrade", release)
    return release


@op("orchestration.approve")
def orchestration_decide(c, args):
    """Approve or reject (``decision``) the Casals baton action ``action_id`` for this realm."""
    method = {"approve": "submit_approval", "reject": "reject_action"}.get(args["decision"])
    baton = Realm["1"].manifest_data.get("casals", {}).get("baton_canister_id")
    if method is None:
        raise Fail("decision is approve or reject")
    if not baton:
        raise Fail("no Casals baton is configured")
    reply = json.loads((yield from chain.call_text(baton, method, str(args["action_id"]))))
    if reply.get("ok") is False:
        raise Fail(f"the baton refused: {reply.get('error')}")
    return {"action_id": str(args["action_id"]), "decision": args["decision"], "reply": reply}


@op("realm.monitor", read=True)
def realm_monitor(c, args):
    """Health figures: status, running and installed versions, counts of operations, events and rows per entity,
    and the last accepted ``upgrade``."""
    realm = Realm["1"]
    return {"status": realm.status, "version": VERSION, "installed_version": realm.installed_version,
            "operations": len(ops()), "events": Event.max_id(), "rows": {n: getattr(ggg, n).count() for n in COUNTED},
            "upgrade": realm.manifest_data.get("upgrade", {})}


@op("db.export", read=True)
def realm_export(c, args):
    """One page of an entity's rows (``entity``, ``from``, ``count``); ``next`` continues it."""
    cls = getattr(ggg, args["entity"], None)
    if not (isinstance(cls, type) and issubclass(cls, Entity)):
        raise Fail(f"unknown entity '{args['entity']}'")
    rows, nxt = page(cls, args)
    return {"entity": args["entity"], "rows": [view(r) for r in rows], "next": nxt}


@op("shell.execute")
def shell(c, args):
    """Evaluate one expression ``expr`` in the sandbox; ``ops.<name>(args)`` calls an operation as you, through Cedar.
    Example: ``{"expr": "ops.proposal_list({'status': 'voting'})"}``."""
    def as_caller(_, kw):
        return call(kw["name"], kw["args"], user=c.user, origin="shell")

    return {"result": sandbox.execute(SHELL % args["expr"], "main", {}, as_caller, ("op",))}
