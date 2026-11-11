import json

from core import auth, build_variant, chain, journal, language, legislation, localtime, zones
import constants
import ggg
from core import sandbox
from api import tasks
from core.db import CONVERT_IDS, PAGE_MAX, Conflict, Fail, Forbidden, Unavailable, convert_legacy_indexes, page, view
from api.extensions import trust
from ggg import Calendar, Department, Event, Member, Realm, Registry, Treasury, User, UserProfile
from ic_python_db import Entity
from api import codex, installs
from core.op import call, op, ops
from core.pending import quoted, without_secrets
from api.proposals import root

VERSION = "VERSION_PLACEHOLDER"
RELEASE_WASM = "realm_backend.wasm.gz"  # as `realms files publish-release` stores it

PROFILES = {
    "admin": ["all"],
    "member": [
        "self.join", "self.update_public_profile", "self.update_private_data", "self.change_quarter",
        "self.invoice_refresh", "self.data_view", "self.data_manage", "self.delegate", "realm.data_view",
        "proposal.create", "proposal.vote", "federal_vote.propose", "dispute.create", "dispute.view",
        "identity.submit", "attribute.submit", "license.apply", "request.file", "tax.declare", "bid.submit",
        "extension.call",
    ],
    # Held by the founder only while the realm is in setup (I-ADM-2).
    "founder": [
        "realm.configure", "realm.configure.branding", "realm.configure.tokens", "realm.configure.infrastructure",
        "realm.configure.trust_policy", "realm.register", "codex.install", "extension.install", "governance.update",
        "it_team.set",
    ],
}
INSTALL = ("network", "can_test_mode", "founder")
FIELDS = {
    "realm": ("name", "manifesto", "terms", "welcome_message", "open_registration", "ai_assistant_enabled",
              "quarter_join_mode", "auto_scale_enabled", "timezone", "languages", "test_mode", "test_flags"),
    "branding": ("logo_url", "background_image_url", "primary_color"),
    "tokens": ("accounting_currency", "accounting_currency_decimals", "token_canister_id", "nft_canister_id",
               "shared_tokens_json"),
    "infrastructure": ("frontend_canister_id", "file_registry_canister_id", "marketplace_canister_id",
                       "installer_canister_id", "baton_canister_id", "conductor_canister_id", "casals_stand"),
    "trust_policy": ("require_marketplace_approval", "trusted_approvers", "trusted_principals"),
}
# The branding images are kept in the Realm row as data URLs, and setup_step answers with the whole setup state, both
# images included: an update's reply may not pass 2 MiB, so two images and the rest of the state must fit in it. The
# frontend compresses an image to 700 KiB (imageDataUrl.ts), which is 955,759 characters in base64 with the
# "data:image/jpeg;base64," prefix; IMAGE_MAX leaves a little room over that.
IMAGES = ("logo_url", "background_image_url")
IMAGE_MAX = 960_000
# The realm's texts a quarter keeps in sync travel whole with every settings push, whose record (a FederationMessage)
# holds at most 65,536 characters; and the Realm row is read on every message.
TEXTS = ("manifesto", "terms")
TEXT_MAX = 20_000
# Kept in manifest_data["casals"], not on Realm: the stand Casals built the realm on, its baton and its conductor.
CASALS = ("baton_canister_id", "conductor_canister_id", "casals_stand")
# Kept in manifest_data too: how far db_convert_indexes has got, {"after": key, "refused": [key, ...]} while it runs and
# {"done": True, "refused": [...]} at the end (a new realm: {"done": True}). "refused" names the lists it skipped as
# too long to convert.
INDEX_CONVERSION = "index_conversion"
LEG_INDEX = "leg_index"  # FederalVoteLeg.proposal_id indexed for the legs that predate the index (#488)
HEX = "0123456789abcdef"
COUNTED = ("User", "Member", "Department", "Organization", "Proposal", "Extension", "Task", "Notification")
SHELL = """class _Ops:
    def __getattr__(self, name):
        return lambda args=None, **kw: rpc("op", name=name, args={**(args or {}), **kw})


ops = _Ops()


def main():
    return (%s)
"""


def start(installer=""):
    """Boot the realm. ``installer``, the init argument, is the GaaS installer canister: it keeps admin rights
    after the stand hands off its controllers, so it can finish setup. Recorded once."""
    # Load every operation here, where it is kept: a query loading modules would redo it on every call.
    ops()
    if Realm["1"] is None:
        realm = Realm(id="1", principal_id=chain.self_id())
        realm.calendar, realm.treasury = Calendar(id="1"), Treasury(id="1")
        # a new database holds no list saved the old way, and no leg that predates its index
        realm.manifest_data = {INDEX_CONVERSION: {"done": True}, LEG_INDEX: True}
        Department(id="Administration", is_root=True, description="The realm's root department")
    installer = (installer or "").strip()
    if installer and not Realm["1"].installer_canister_id:
        Realm["1"].installer_canister_id = installer
    for name, allowed in PROFILES.items():
        if UserProfile[name] is None:
            UserProfile(id=name, allowed_to=allowed)
    # The parsed zone is gone after an upgrade, and a query could not keep one it parsed: read it here. A zone the
    # release lacks traps the upgrade on purpose: tz names only grow, and the realm must not turn UTC without a word.
    if Realm["1"].timezone != "UTC":  # a UTC realm needs no zone file, nor the reader
        localtime.use(Realm["1"].timezone)
    journal.fix_first_day()  # on the realm's clock
    if not Realm["1"].manifest_data.get(LEG_INDEX):
        from api import federation

        federation.index_legs()
        remember(LEG_INDEX, True)
    legislation.warm()  # the active layered codex's sandbox is gone after an upgrade, and a query could not keep one
    installs.pause_after_upgrade()
    tasks.rearm()
    from api import links, proposals, quarters

    links.resume()
    proposals.resume()
    quarters.resume()


@op(None, read=True, public=True)
def status(c, args):
    """The realm's ``version``, ``build_variant`` (production or test), ``status``, ``name``, ``canister`` id and
    number of ``operations``."""
    realm = Realm["1"]
    return {"version": VERSION, "build_variant": build_variant.BUILD_VARIANT, "status": realm.status if realm else "",
            "name": realm.name if realm else "", "canister": chain.self_id(), "operations": len(ops())}


@op(None, read=True, public=True)
def version(c, args):
    """The ``version`` of the realm's code."""
    return {"version": VERSION}


@op(None, read=True, public=True)
def realm_info(c, args):
    """The realm's public profile: name, manifesto, terms, welcome message, status, branding, accounting currency and
    its decimals, whether registration is open and whether it is a capital, its network, time zone and languages, and
    its test mode and test flags, and its ``sync_sources``: the realms it takes legislation from, each with the status
    of its link."""
    from api import links

    realm = Realm["1"]
    return {"realm": view(realm, "name", "manifesto", "terms", "welcome_message", "status", "logo_url",
                          "background_image_url", "primary_color", "accounting_currency",
                          "accounting_currency_decimals", "open_registration", "is_capital", "network", "timezone",
                          "languages", "test_mode", "test_flags") if realm else {},
            "sync_sources": [links.source_view(s) for s in links.sources()]}


@op(None, read=True, public=True)
def realm_image(c, args):
    """One of the realm's branding images, ``image`` (``logo_url`` or ``background_image_url``), as ``value``. A quarter
    reads its capital's this way when one changes (api.quarters), one image a call: two images and the rest of
    ``realm_info`` may not fit in the 2 MiB a reply to another canister may be."""
    if args["image"] not in IMAGES:
        raise Fail(f"image is one of {', '.join(IMAGES)}")
    return {"image": args["image"], "value": getattr(Realm["1"], args["image"])}


@op(None, read=True, public=True)
def token_catalog(c, args):
    """The tokens a realm may adopt as its treasury token: ``symbol``, ``name``, ``description``, ``ledger``,
    ``indexer``, ``decimals`` and whether it is ``monetary`` (real money). Any other ICRC-1 ledger is added by hand."""
    return {"tokens": [dict(t) for t in constants.TOKENS]}


def launched(args):
    """Configuration is governed once the realm has launched; during setup the founder acts directly (I-ADM-2)."""
    return None if Realm["1"].status == "setup" else root()


def draft():
    return Realm["1"].manifest_data.get("setup", {"done": []})


def remember(key, value):
    realm = Realm["1"]
    realm.manifest_data = {**realm.manifest_data, key: value}


def configurable(values):
    """The check of ``realm_configure`` and its branding and tokens variants, so that a call that cannot succeed is
    refused before a voting policy turns it into a proposal: on a quarter, a setting the capital keeps in sync; and the
    values ``valid_values`` refuses."""
    from api import quarters  # it imports this module

    quarters.refuse_synced(values)
    valid_values(values)


def valid_values(values):
    """Refuse a ``timezone`` this release has no zone file for, ``languages`` that are no BCP 47 tags, a branding image
    above ``IMAGE_MAX`` and a manifesto or terms above ``TEXT_MAX``."""
    if "timezone" in values and not zones.known(values["timezone"]):
        raise Fail(f"timezone must be an IANA name such as Europe/Madrid, or UTC; this release has no zone "
                   f"'{values['timezone']}'")
    if "languages" in values:
        language.require_all(values["languages"])
    for name in IMAGES:
        if len(values.get(name) or "") > IMAGE_MAX:
            raise Fail(f"{name} is above {IMAGE_MAX:,} characters (an image of about 700 KiB): the realm sends its "
                       f"images with every page it shows")
    for name in TEXTS:
        if len(values.get(name) or "") > TEXT_MAX:
            raise Fail(f"{name} is above {TEXT_MAX:,} characters")


def configure(fields, values):
    """Set ``values``, any of ``fields``. On a capital, a change of a synced setting is pushed to its quarters; a
    quarter refuses one (api.quarters.SYNCED)."""
    from api import quarters  # it imports this module

    realm, color = Realm["1"], values.get("primary_color")
    unknown = sorted(set(values) - set(fields))
    if unknown:
        raise Fail(f"cannot configure {', '.join(unknown)} here")
    quarters.refuse_synced(values)
    valid_values(values)
    if color and not (len(color) == 7 and color[0] == "#" and all(ch in HEX for ch in color[1:].lower())):
        raise Fail(f"primary_color must look like #1E6B52, not {color}")
    if (values.get("test_mode") or any((values.get("test_flags") or {}).values())) \
            and not build_variant.test_flags_allowed(realm.network, realm.can_test_mode):
        raise Fail("test flags need a test build, on a non-production network or with can_test_mode set at install")
    for k, v in values.items():
        if k in CASALS:
            remember("casals", {**realm.manifest_data.get("casals", {}), k: v})
        else:
            setattr(realm, k, v)
    if "timezone" in values:
        localtime.use(realm.timezone)  # from now on; nothing stored is rewritten
        journal.fix_first_day()  # a zone west of the old one can put today before the journal's first day
    quarters.forget_digests(values)
    if set(values) & set(quarters.SYNCED):
        quarters.push_settings()
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
    fields = [f for group in FIELDS.values() for f in group if f not in CASALS]
    return {"status": realm.status, "founder": d.get("founder", ""), "done": d["done"],
            "todo": [s for s in ("install", *FIELDS) if s not in d["done"]],
            "realm": view(realm, "network", "can_test_mode", *fields)}


@op("realm.configure")
def setup_step(c, args):
    """One setup step: ``step`` (install, realm, branding, tokens, infrastructure, trust_policy) and its values.
    The values are the fields of the matching realm_configure operation; install takes network, can_test_mode
    and founder. Returns the setup progress, as setup_state."""
    apply_step(args["step"], {k: v for k, v in args.items() if k != "step"})
    return setup_state(c, {})


def apply_step(step, values, **noted):
    """Run one setup step and record it as done, with anything ``noted`` alongside."""
    d = draft()
    if Realm["1"].status != "setup":
        raise Conflict("the realm has launched: configuration is governed now")
    if step == "install" and "install" in d["done"]:
        raise Conflict("the install facts are set once, by the installer")
    if step != "install" and step not in FIELDS:
        raise Fail(f"unknown setup step '{step}'")
    if step == "install":
        d["founder"] = install(values)
    else:
        configure(FIELDS[step], values)
    d.update(noted)
    d["done"] = sorted(set(d["done"]) | {step})
    remember("setup", d)


def hosted(c):
    """Only the realm's host: a controller, or the installer it was created with; ``all`` is not enough."""
    if not (chain.is_controller(c.by) or c.by == Realm["1"].installer_canister_id):
        raise Forbidden("only the realm's host does this, during setup")


# GGG's names for the canisters Realms GOS keeps as infrastructure fields.
HOSTED = {"frontend": "frontend_canister_id", "file_registry": "file_registry_canister_id",
          "realms_gos:marketplace": "marketplace_canister_id", "realms_gos:installer": "installer_canister_id",
          "realms_gos:baton": "baton_canister_id", "realms_gos:conductor": "conductor_canister_id"}


@op("realm.host")
def host_install(c, args):
    """The host's install, once and before anything else: the ``founder``, the ``network``, the ``registry`` that
    lists the realm (kept for realm_register) and whether ``test_mode`` may be turned on. Returns the setup
    progress, as setup_state."""
    hosted(c)
    values = {"founder": args["founder"], "network": args["network"], "can_test_mode": bool(args.get("test_mode"))}
    apply_step("install", values, **({"registry": args["registry"]} if args.get("registry") else {}))
    return setup_state(c, {})


@op("realm.host")
def host_infrastructure(c, args):
    """The canisters the realm works with, from its host during setup: ``frontend``, ``file_registry`` and
    ``extra`` pairs named ``realms_gos:<role>`` (marketplace, installer, baton). Returns the setup progress."""
    hosted(c)
    named = {"frontend": args.get("frontend"), "file_registry": args.get("file_registry"), **dict(args.get("extra") or [])}
    unknown = sorted(set(named) - set(HOSTED))
    if unknown:
        raise Fail(f"this realm keeps no {', '.join(unknown)}")
    apply_step("infrastructure", {HOSTED[k]: v for k, v in named.items() if v})
    return setup_state(c, {})


@op("realm.configure", returns="realm")
def setup_launch(c, args):
    """Open the realm (status ``alpha``; a quarter takes its capital's stage). The founder's setup rights end;
    configuration is governed from now on. Returns ``realm`` and its ``status``."""
    from api import quarters  # it imports this module

    realm, d = Realm["1"], draft()
    if realm.status != "setup":
        raise Conflict("the realm has already launched")
    if not realm.name:
        raise Conflict("name the realm first")
    stage = launch_stage()
    if d.get("founder"):
        founder = User[d["founder"]]
        founder.profiles = [p for p in founder.profiles if p.id != "founder"]
    realm.status = stage
    remember("setup", {**d, "launched_at": chain.now()})
    quarters.push_settings()
    yield from codex.hook("on_stage_change", {"stage": stage})
    return {"realm": realm.id, "status": realm.status}


def launch_stage():
    """The stage a launch takes: ``alpha``, or on a quarter its capital's, which the quarter learns when it syncs. A
    quarter that has not synced yet, or whose capital is still in setup, cannot launch."""
    from api import quarters  # it imports this module

    if not quarters.federated():
        return "alpha"
    stage = (Realm["1"].sync_state or {}).get("stage")
    if stage is None:
        raise Conflict("this quarter does not know its capital's stage yet: run quarter_sync first")
    if stage == "setup":
        raise Conflict(f"the capital {quarters.capital()} is still in setup: a quarter launches after it")
    return stage


@op("realm.configure", governed=launched, check=configurable)
def realm_configure(c, args):
    """Set any of ``name``, ``manifesto`` and ``terms`` (at most 20,000 characters each), ``welcome_message``,
    ``open_registration``, ``ai_assistant_enabled``, ``quarter_join_mode``, ``auto_scale_enabled``, ``timezone``,
    ``languages`` (BCP 47 tags, the first the default), ``test_mode`` and ``test_flags``. Returns ``configured``. The
    ``timezone`` is an IANA name such as ``Europe/Madrid`` that this release bundles, or ``UTC``,
    which a realm has until it sets one; the new zone applies from the moment it is set, and nothing stored changes.
    Test mode and flags need a test build, on a non-production network or with ``can_test_mode`` set at install;
    a production build refuses them. On a quarter, the settings its capital keeps in sync (api.quarters.SYNCED: the
    name, the manifesto, the terms and the languages here) are refused with Forbidden."""
    return configure(FIELDS["realm"], args)


@op("realm.configure.branding", governed=launched, check=configurable)
def realm_configure_branding(c, args):
    """Set any of ``logo_url``, ``background_image_url`` and ``primary_color`` (like ``#1E6B52``). An image is a URL or
    a data URL of at most 960,000 characters, about 700 KiB. A quarter refuses them: its capital keeps them in sync."""
    return configure(FIELDS["branding"], args)


@op("realm.configure.tokens", governed=launched, check=configurable)
def realm_configure_tokens(c, args):
    """Set any of ``accounting_currency``, ``accounting_currency_decimals``, ``token_canister_id``,
    ``nft_canister_id`` and ``shared_tokens_json``. A quarter refuses the accounting currency and its decimals: its
    capital keeps them in sync."""
    return configure(FIELDS["tokens"], args)


@op("realm.configure.infrastructure", governed=launched)
def realm_configure_infrastructure(c, args):
    """Set the canisters the realm uses, any of ``frontend_canister_id``, ``file_registry_canister_id``,
    ``marketplace_canister_id``, ``installer_canister_id``, ``baton_canister_id`` (the Casals baton),
    ``conductor_canister_id`` and ``casals_stand`` (the Casals conductor and the stand it built the realm on)."""
    return configure(FIELDS["infrastructure"], args)


@op("realm.configure.trust_policy", governed=launched)
def realm_configure_trust_policy(c, args):
    """Which code the realm accepts: ``require_marketplace_approval``, ``trusted_approvers``, ``trusted_principals``."""
    return configure(FIELDS["trust_policy"], args)


def text(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@op("realm.register", governed=launched, returns="registry")
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


DECISIONS = {"approve": "submit_approval", "reject": "reject_action"}


def deciding(user, args, grant):
    """What deciding a Casals action does. The realm holds the action's id and no more of it: the upgrade it carries is
    in the baton, so the member is told to read it there."""
    if args["decision"] not in DECISIONS:
        raise Fail("decision is approve or reject")
    action = quoted(args["action_id"])
    if args["decision"] == "reject":
        return [{"label": "What changes", "value": f"Rejects Casals action {action}, which then does not run"}]
    return [{"label": "What changes",
             "value": f"Approves Casals action {action}. Once it has the approvals it needs, the upgrade it carries "
                      "takes effect at once. The realm cannot show you the upgrade here: read it in Casals before you approve"}]


@op("orchestration.approve", summary=deciding, title="Decide a Casals action")
def orchestration_decide(c, args):
    """Approve or reject (``decision``) the Casals baton action ``action_id`` for this realm."""
    method = DECISIONS.get(args["decision"])
    baton = Realm["1"].manifest_data.get("casals", {}).get("baton_canister_id")
    if method is None:
        raise Fail("decision is approve or reject")
    if not baton:
        raise Unavailable("no Casals baton is configured")
    reply = json.loads((yield from chain.call_text(baton, method, str(args["action_id"]))))
    if reply.get("ok") is False:
        raise Fail(f"the baton refused: {reply.get('error')}")
    return {"action_id": str(args["action_id"]), "decision": args["decision"], "reply": reply}


@op("realm.monitor", read=True)
def realm_monitor(c, args):
    """Health figures: status, running and installed versions, cycles, stable ``memory`` in bytes, counts of
    operations, events and rows per entity, and the last accepted ``upgrade``."""
    realm = Realm["1"]
    return {"status": realm.status, "version": VERSION, "installed_version": realm.installed_version or VERSION,
            "cycles": chain.cycles(), "memory": chain.memory(), "operations": len(ops()), "events": Event.max_id(),
            "rows": {n: getattr(ggg, n).count() for n in COUNTED}, "upgrade": realm.manifest_data.get("upgrade", {})}


# Entities whose rows hold personal data in clear: exporting them takes their readers' permission too (I-PRV-1).
READERS = {"Attribute": "attribute.review"}


@op("db.export", read=True, returns="rows")
def realm_export(c, args):
    """One page of an entity's rows (``entity``, ``from``, ``count``); ``next`` continues it. An entity whose rows are
    large returns fewer rows than ``count`` asks for: follow ``next``, never compute ``from``. Exporting ``Attribute``
    takes attribute.review as well, since its rows hold personal data in clear."""
    cls = getattr(ggg, args["entity"], None)
    if not (isinstance(cls, type) and issubclass(cls, Entity)):
        raise Fail(f"unknown entity '{args['entity']}'")
    reader = READERS.get(args["entity"])
    if reader and not {"all", reader} & auth.permissions(c.user):
        raise Forbidden(f"exporting {args['entity']} needs {reader}")
    rows, nxt = page(cls, args, most=getattr(cls, "export_most", PAGE_MAX))
    out = [view(r) for r in rows]
    if args["entity"] == "PendingAction":  # whoever exports, the secrets of the calls the actions hold stay out
        out = [without_secrets(r["method"], r) for r in out]
    return {"entity": args["entity"], "rows": out, "next": nxt}


@op("db.convert")
def db_convert_indexes(c, args):
    """After an upgrade from a release before ic-python-db 0.13: convert the index lists saved as one array each, at
    most ``ids`` (default 10,000, at most 140,000) of their ids per call. Call it until ``done``. A realm created by
    this release, or one that is done, answers at once and reads no index entry. Returns the ``lists`` and ``ids``
    converted, whether it is ``done`` and the lists ``refused`` so far: one longer than a call can convert is left as
    it was."""
    progress = Realm["1"].manifest_data.get(INDEX_CONVERSION) or {}
    refused = progress.get("refused", [])
    if progress.get("done"):
        return {"lists": 0, "ids": 0, "done": True, "refused": refused}
    out = convert_legacy_indexes(progress.get("after", ""), max(1, int(args.get("ids") or CONVERT_IDS)))
    refused = refused + out["refused"]
    done = out["next"] is None
    progress = {"done": True} if done else {"after": out["next"]}
    remember(INDEX_CONVERSION, {**progress, "refused": refused})
    return {"lists": out["lists"], "ids": out["ids"], "done": done, "refused": refused}


@op("shell.execute")
def shell(c, args):
    """Evaluate one expression ``expr`` in the sandbox; ``ops.<name>(args)`` calls an operation as you, through Cedar.
    Called by your delegate, it calls them as the delegate for you, so under the grant.
    Example: ``{"expr": "ops.proposal_list({'status': 'voting'})"}``."""
    def as_caller(_, kw):
        return call(kw["name"], kw["args"], user=c.user, origin="shell", by=c.by, root=c.root)

    return {"result": sandbox.execute(SHELL % args["expr"], "main", {}, as_caller, ("op",))}
