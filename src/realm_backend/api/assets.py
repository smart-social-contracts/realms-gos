
from core import candid
from core import chain, sync_log
from core.db import Conflict, Fail, Forbidden, NotFound, deref, each, find, next_id, page, put, view
from ggg import Appointment, Dispute, Land, License, NFTToken, Organization, Realm, User, Verdict, Zone
from api import codex
from api.licenses import active, principals
from core.op import op
from core.pending import quoted, readable, you_sign
from api.proposals import day_of, launched, realm_wide, whole

LAND = "LAND"
PARCEL = ("id", "x", "y", "width", "height", "kind", "owner", "status", "token_id", "assessed_value", "area",
          "assessed_at", "assessment_ref", "lies_in")
ZONES_MAX = 8  # the zones a parcel lies in (ggg I-AST-6)
CELL = 16  # the side of a spatial index cell, and the most a parcel spans
VIEW = 32  # the most a map viewport spans
# The census of the zones members live in: ``held`` is each zone with its population (``populated``), read from the rows
# once and kept by ``move_home`` and ``leave_home`` after that, so that a quarter does not read every zone to report them;
# ``generation`` counts the changes, and ``taken`` is the generation the capital last took (api.quarters.sync): a quarter
# reports the census while they differ, and the capital's take is acknowledged only when it has been made. None is stored:
# they start afresh after an upgrade, which is when the next report must carry the whole census.
census = {"held": None, "generation": 1, "taken": 0}


# --- ledger (ICRC-7, smart-social-contracts/ic-tokens), spoken in candid text ---

def quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def account(principal):
    return f"record {{ owner = principal {quote(principal)}; subaccount = null }}"


def value(v):
    if isinstance(v, int):
        return f"variant {{ Nat = {v} : nat }}" if v >= 0 else f"variant {{ Int = {v} : int }}"
    return f"variant {{ Text = {quote(v)} }}"


def literal(text, i):
    """The candid text literal opening at ``text[i]``, and the index after it."""
    j = i + 1
    while text[j] != '"':
        j += 2 if text[j] == "\\" else 1
    return candid.unquote(text[i:j + 1]), j + 1


def token_meta(reply):
    """The text entries of an ``icrc7_token_metadata`` reply (variant labels as names or hashes); {} for no token."""
    meta, i = {}, reply.find('"')
    while i >= 0:
        key, i = literal(reply, i)
        i = reply.index("=", reply.index("variant", i)) + 1
        while reply[i] == " ":
            i += 1
        if reply[i] == '"':
            meta[key], i = literal(reply, i)
        i = reply.find('"', i)
    return meta


def ledger(collection, method, arg):
    reply = yield from chain.call(collection.canister, method, arg)
    found = candid.number(reply, "Ok") if "Ok" in reply else None
    if found is None:
        raise Fail(f"the {collection.id} ledger refused {method}: {reply}")
    return found


def mint(collection, owner, meta):
    if collection.supply_cap and collection.total_supply >= collection.supply_cap:
        raise Conflict(f"{collection.id} has reached its supply cap")
    pairs = "; ".join(f"record {{ {quote(k)}; {value(v)} }}" for k, v in meta.items())
    arg = f"(record {{ token_id = null; owner = {account(owner)}; metadata = opt vec {{ {pairs} }} }})"
    token_id = yield from ledger(collection, "mint", arg)
    collection.total_supply += 1
    return token_id


def header(c, schema, subject, issuer, **more):
    """The `ggg:` header every minted token carries. Only references and hashes: no personal data (I-AST-4)."""
    realm = Realm["1"]
    h = {"v": 1, "realm": realm.federation_realm_id or chain.self_id(), "quarter": chain.self_id(),
         "network": (realm.manifest_data or {}).get("network", "ic"), "schema": schema, "subject": subject,
         "issuer": issuer, "license": "", "signer": c.by, "prepared_by": "", "issued_at": chain.now(),
         "expires_at": 0, "payload_hash": "", "payload_ref": ""}
    h.update(more)
    return {f"ggg:{k}": v for k, v in h.items()}


# --- land ---

def owner_ref(text):
    ref = text if ":" in text else f"User:{text}"
    if not isinstance(deref(ref), (User, Organization)):
        raise Fail(f"{ref} cannot own land")
    return ref


def wallet(ref):
    # Organizations have no principal: their tokens stay in the realm's custody.
    kind, _, key = ref.partition(":")
    return key if kind == "User" else chain.self_id()


def overlaps(p, x, y, w, h):
    return p.x < x + w and x < p.x + p.width and p.y < y + h and y < p.y + p.height


def cell(x, y):
    return f"{x // CELL}:{y // CELL}"


def parcels_in(x, y, w, h):
    """The parcels overlapping the box, read from the cells that can hold their corner: a parcel spans at most CELL,
    so its corner lies at most CELL - 1 before the box."""
    return [p for cx in range((x - CELL + 1) // CELL, (x + w - 1) // CELL + 1)
            for cy in range((y - CELL + 1) // CELL, (y + h - 1) // CELL + 1)
            for p in each(Land, "cell", f"{cx}:{cy}") if p.status != "revoked" and overlaps(p, x, y, w, h)]


def owners(args):
    return principals(find(Land, args["land"]).owner)


def transfer_parties(args):
    return owners(args) | principals(owner_ref(args["to"]))


@op("nft.mint", governed=realm_wide, returns="land")
def land_register(c, args):
    """Register a parcel of ``width`` × ``height`` cells (default 1, at most 16) at ``x``, ``y`` (default 0) for
    ``owner`` (user or organization), optional ``kind`` (default unassigned) and ``metadata``; no overlaps.
    Returns ``land``."""
    x, y, w, h = (int(args.get(k, d)) for k, d in (("x", 0), ("y", 0), ("width", 1), ("height", 1)))
    if not (1 <= w <= CELL and 1 <= h <= CELL):
        raise Fail(f"a parcel is 1 to {CELL} cells wide and high")
    owner = owner_ref(args["owner"])
    taken = parcels_in(x, y, w, h)
    if taken:
        raise Conflict(f"the parcel overlaps {taken[0].id}")
    land = Land(id=next_id(Land, "L"), x=x, y=y, width=w, height=h, cell=cell(x, y),
                kind=args.get("kind", "unassigned"), owner=owner, registered_by=c.user,
                metadata=args.get("metadata", {}))
    return {"land": land.id}


@op("realm.configure", governed=launched, returns="land")
def land_zone_set(c, args):
    """Replace the zones parcel ``land`` lies in, such as a planning area, by ``zones``: at most 8, each a zone the realm
    defines (``zone_define``) and named once. Returns ``land``."""
    land, zones = find(Land, args["land"]), args["zones"]
    if len(zones) > ZONES_MAX or len(set(zones)) != len(zones):
        raise Fail(f"a parcel lies in at most {ZONES_MAX} zones, each named once")
    for zone in zones:
        if not (Zone[zone] and Zone[zone].defined):
            raise NotFound(f"{zone} is not a zone of the realm")
    land.lies_in = zones
    return {"land": land.id}


def of_the_parcel(land):
    return f"parcel {land.id} ({quoted(land.kind)}, {land.width} by {land.height} cells at {land.x}, {land.y})"


def moving_land(user, args, grant):
    land, to, order = find(Land, args["land"]), owner_ref(args["to"]), deref(args["order"])
    if not isinstance(order, Verdict):
        raise Fail("moving a parcel needs an issued court verdict")
    return you_sign(f"Moves {of_the_parcel(land)}{' and its NFT' if land.token_id else ''} from {quoted(land.owner)} "
                    f"to {quoted(to)} under verdict {order.id} of case {order.case.id}",
                    ("Court order", quoted(order.decision)))


@op("nft.force_transfer", signing=True, governed=realm_wide, parties=transfer_parties, summary=moving_land,
    title="Move a parcel by court order")
def land_transfer(c, args):
    """Move parcel ``land`` to ``to`` (user or organization) under court ``order`` (``Verdict:id``, issued or final,
    in a case whose dispute is about ``Land:<id>`` and in which ``to`` is a party), and its NFT with it if minted."""
    land, to, order = find(Land, args["land"]), owner_ref(args["to"]), deref(args["order"])
    if not isinstance(order, Verdict) or order.status not in ("issued", "final"):
        raise Fail("moving a parcel needs an issued court verdict")
    case = order.case
    if find(Dispute, case.dispute).subject != f"Land:{land.id}" or to not in (case.plaintiff, case.defendant):
        raise Fail(f"{order.id} does not settle {land.id} with {to} as a party")
    if land.status not in ("active", "disputed") or to == land.owner:
        raise Fail(f"{land.id} cannot be moved to {to}")
    if land.token_id:
        arg = (f"(record {{ token_id = {land.token_id} : nat; to = {account(wallet(to))}; "
               f"memo = opt {quote(args['order'])} }})")
        yield from ledger(find(NFTToken, LAND), "force_transfer", arg)
    land.owner = to
    return {"land": land.id, "owner": to}


@op("realm.data_view", read=True, returns="lands")
def land_list(c, args):
    """One page of parcels (``from``, ``count``), optionally only those of ``owner``; ``next`` continues it.
    A page can hold fewer matches than ``count``: follow ``next``."""
    owner = owner_ref(args["owner"]) if args.get("owner") else None
    rows, more = page(Land, args, lambda p: owner in (None, p.owner))
    return {"lands": [view(p, *PARCEL) for p in rows], "next": more}


@op("realm.data_view", read=True)
def land_map(c, args):
    """The ``parcels`` overlapping the viewport of ``w`` × ``h`` cells (default and at most 32) from ``x``, ``y``
    (default 0), and one page (``from``, ``count``) of the ``zones`` not tied to a parcel; ``next`` continues the
    zones. A page can hold fewer zones than ``count``: follow ``next``."""
    x, y, w, h = (int(args.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", VIEW), ("h", VIEW)))
    if not (1 <= w <= VIEW and 1 <= h <= VIEW):
        raise Fail(f"a map viewport is 1 to {VIEW} cells wide and high")
    zones, more = page(Zone, args, lambda z: z.land is None)
    return {"parcels": [view(p, *PARCEL) for p in parcels_in(x, y, w, h)],
            "zones": [view(z, "id", "name", "kind", "population") for z in zones], "next": more}


@op("nft.mint", returns="token_id")
def land_mint_nft(c, args):
    """Mint the LAND NFT for the active parcel ``land`` to its owner (an organization's stays in the realm's custody),
    optional ``prepared_by``. Returns ``token_id``."""
    land, collection = find(Land, args["land"]), find(NFTToken, LAND)
    if land.token_id or land.status != "active":
        raise Conflict(f"{land.id} is {land.status}{' and already minted' if land.token_id else ''}")
    meta = {**header(c, "ggg.land/1", f"Land:{land.id}", f"Department:{realm_wide(args).id}",
                     prepared_by=args.get("prepared_by", "")),
            "x": land.x, "y": land.y, "width": land.width, "height": land.height, "kind": land.kind}
    token_id = yield from mint(collection, wallet(land.owner), meta)
    land.token_id = str(token_id)
    return {"land": land.id, "token_id": token_id}


def assessing_land(user, args, grant):
    land = find(Land, args["land"])
    changes = [f"{name} {whole(name, args[name]) if name != 'reference' else quoted(args[name])}"
               for name in ("value", "area", "reference") if args.get(name) is not None]
    lines = [("Legislation followed", quoted(args["legislation"]))] if args.get("legislation") else []
    return you_sign(f"Sets the assessment of {of_the_parcel(land)}: {', '.join(changes) or 'nothing'}", *lines)


@op("land.assess", signing=True, returns="land", summary=assessing_land, title="Assess a parcel")
def land_assess(c, args):
    """Set what the assessor decided for the parcel ``land``, signing for it: its ``value`` in the accounting
    currency, its ``area`` in square metres, and the ``reference`` of the entry in the assessor's register. A field
    left out keeps its value; ``legislation`` (the provision followed) is kept in the audit event. Returns ``land``."""
    land = find(Land, args["land"])
    changes = {k: args[k] for k in ("value", "area", "reference") if args.get(k) is not None}
    if not changes:
        raise Fail("an assessment sets a value, an area or a reference")
    if land.status == "revoked":
        raise Conflict(f"{land.id} is {land.status}")
    if "value" in changes:
        land.assessed_value = int(changes["value"])
    if "area" in changes:
        land.area = int(changes["area"])
    if "reference" in changes:
        land.assessment_ref = changes["reference"]
    land.assessed_at = chain.now()
    return {"land": land.id}


def freeze(args, on):
    land = find(Land, args["land"])
    if land.status != ("active" if on else "disputed"):
        raise Conflict(f"{land.id} is {land.status}")
    if land.token_id:
        arg = (f"(record {{ token_id = {land.token_id} : nat; reason = opt {quote(args.get('reason', ''))} }})" if on
               else f"({land.token_id} : nat)")
        yield from ledger(find(NFTToken, LAND), "freeze_token" if on else "unfreeze_token", arg)
    land.status = "disputed" if on else "active"
    return {"land": land.id, "status": land.status}


def freezing_land(user, args, grant):
    land = find(Land, args["land"])
    return you_sign(f"Marks {of_the_parcel(land)} of {quoted(land.owner)} disputed"
                    f"{' and freezes its NFT' if land.token_id else ''}",
                    ("Freeze reason", quoted(args.get("reason", ""))))


@op("nft.freeze", signing=True, parties=owners, returns="land", summary=freezing_land, title="Freeze a parcel")
def land_freeze(c, args):
    """Mark the active parcel ``land`` disputed and freeze its NFT, with optional ``reason``."""
    return (yield from freeze(args, True))


def unfreezing_land(user, args, grant):
    land = find(Land, args["land"])
    return you_sign(f"Ends the dispute on {of_the_parcel(land)} of {quoted(land.owner)}: it becomes active again"
                    f"{' and its NFT is unfrozen' if land.token_id else ''}")


@op("nft.freeze", signing=True, parties=owners, returns="land", summary=unfreezing_land, title="Unfreeze a parcel")
def land_unfreeze(c, args):
    """End the dispute on parcel ``land``: it becomes active again and its NFT is unfrozen."""
    return (yield from freeze(args, False))


@op("self.data_manage", returns="zone", secret=("zone",))
def zone_set(c, args):
    """Make ``zone`` (an h3 cell) your home, with optional ``name`` and ``kind``; it replaces your previous home. The
    cell is personal data: it reaches neither the legislation nor the audit log (``secret``)."""
    zone = Zone[args["zone"]]
    if zone and zone.defined:
        raise Fail(f"zone {zone.id} is a zone of the realm, not a cell to make your home")
    if zone and zone.user not in ("", c.user):
        raise Forbidden(f"zone {zone.id} is someone else's home")
    for old in [z for z in each(Zone, "user", c.user) if z.id != args["zone"]]:
        old.user = ""
    zone = zone or Zone(id=args["zone"])
    zone.user, zone.name, zone.kind = c.user, args.get("name", zone.name), args.get("kind", zone.kind)
    return {"zone": zone.id}


@op("realm.configure", governed=launched, returns="zone")
def zone_define(c, args):
    """Define ``zone``, such as a district, that members name as their home zone: its ``name`` and ``kind``. The
    same id again redefines it. Returns ``zone``."""
    return {"zone": define_zone(args["zone"], args["name"], args["kind"]).id}


def define_zone(zone_id, name, kind):
    """The zone of the realm ``zone_id``, created or redefined in place (its population stays). A member's home cell
    is no zone of the realm."""
    zone = Zone[zone_id]
    if zone and not zone.defined:
        raise Conflict(f"zone {zone.id} is a member's home cell")
    created = zone is None
    zone = zone or Zone(id=zone_id, defined=True)
    if put(zone, name=name, kind=kind) or created:
        sync_log.note("zone", zone_id)  # the capital's quarters take it at their next sync
    return zone


def defined_zones():
    """The zones of the realm. A member's home cell has a user and a zone of the realm has none, so the index on
    ``user`` finds them without reading every cell."""
    return [z for z in each(Zone, "user", "") if z.defined]


def populated():
    """The zones of the realm that members live in, each with how many."""
    if census["held"] is None:
        census["held"] = {z.id: z.population for z in defined_zones() if z.population}
    return dict(census["held"])


def counted(zone):
    """The population of ``zone`` changed: the census has it as it is now."""
    census["generation"] += 1
    if census["held"] is not None:
        census["held"].pop(zone.id, None)
        if zone.population and zone.defined:
            census["held"][zone.id] = zone.population


def defined_zone(zone_id):
    """The zone of the realm called ``zone_id``; a member's home cell, or no zone at all, is a bad argument."""
    zone = Zone[zone_id]
    if zone is None or not zone.defined:
        raise Fail(f"'{zone_id}' is no zone of the realm: define it with zone_define")
    return zone


def move_home(user, zone_id):
    """Make the zone of the realm ``zone_id`` the home zone of ``user``, counting them there and no longer where
    they were."""
    zone = defined_zone(zone_id)
    if user.home_zone != zone.id:
        leave_home(user)
        zone.population += 1
        counted(zone)
        user.home_zone = zone.id


def leave_home(user):
    """Take ``user`` out of the population of their home zone, if they have one."""
    home = Zone[user.home_zone] if user.home_zone else None
    if home:
        home.population -= 1
        counted(home)
        user.home_zone = ""


@op("self.data_manage", returns="zone", secret=("zone",))
def zone_clear(c, args):
    """Stop having ``zone`` (your home cell) as your home."""
    zone = find(Zone, args["zone"])
    if zone.user != c.user:
        raise Forbidden(f"zone {zone.id} is not your home")
    zone.user = ""
    return {"zone": zone.id}


# --- credentials: NFTs whose schemas the active codex declares ---

def schema_of(name):
    """A codex manifest's `credentials` block: {schema: {license, positions, collection, public, after_license}}."""
    current = codex.active_legacy()
    rule = current and (current.manifest or {}).get("credentials", {}).get(name)
    if not rule:
        raise Fail(f"no active codex declares the schema '{name}'")
    return rule


def seats(user, org):
    return [a for a in each(Appointment, "user", user)
            if a.status == "active" and a.position.organization and a.position.organization.id == org.id]


def signer(c, args):
    """The schema, organization and collection, once the caller holds a seat that signs it (I-AST-1)."""
    rule, org = schema_of(args["schema"]), find(Organization, args["organization"])
    if org.status != "active":
        raise Conflict(f"{org.id} is {org.status}")
    accepted = set(rule.get("positions", []))
    if not any(not a.agent and {a.position.id, a.position.title} & accepted for a in seats(c.by, org)):
        raise Forbidden(f"you hold no seat at {org.id} that signs {args['schema']}")
    return rule, org, find(NFTToken, rule["collection"])


def licences_held(issuer, kinds):
    """The active licences of ``issuer`` of the ``kinds`` that issuing a credential needs, as the card names them: a
    renewal replaces the licence by another, and the card changes with it."""
    held = [(kind, active(issuer, kind)) for kind in dict.fromkeys(filter(None, kinds))]
    return ", ".join(f"{lic.id if lic else 'none'} ({quoted(kind)})" for kind, lic in held) or "none needed"


def issuing_credential(user, args, grant):
    expires = whole("expires_at", args.get("expires_at") or 0)
    rule = schema_of(args["schema"])
    collection = find(NFTToken, rule["collection"])
    issuer = f"Organization:{find(Organization, args['organization']).id}"
    needed = (rule["license"], collection.required_license)
    return you_sign(f"Issues a {quoted(args['schema'])} credential from organization {quoted(args['organization'])} "
                    f"to {quoted(args['holder'])}",
                    ("Credential claims", readable(args.get("fields", {}))),
                    ("Credential issuer licence", licences_held(issuer, needed)),
                    ("Credential collection", quoted(collection.id)),
                    ("Credential subject", quoted(args.get("subject", "")) if args.get("subject") else "none"),
                    ("Credential payload", f"hash {quoted(args.get('payload_hash', ''))}, "
                                           f"kept at {quoted(args.get('payload_ref', ''))}"),
                    ("Credential expiry", f"expires {day_of(expires)}" if expires else "never expires"),
                    ("Credential prepared by", quoted(args["prepared_by"]) if args.get("prepared_by") else "nobody"))


@op("credential.sign", signing=True, summary=issuing_credential, title="Issue a credential")
def credential_issue(c, args):
    """Mint a ``schema`` credential to principal ``holder`` for ``organization`` (you need a seat there that signs it),
    with optional ``subject``, public ``fields``, ``payload_hash`` (sha256:…), ``payload_ref``, ``prepared_by`` and
    ``expires_at``. Returns ``token_id`` and ``header``."""
    rule, org, collection = signer(c, args)
    issuer = f"Organization:{org.id}"
    for kind in filter(None, (rule["license"], collection.required_license)):
        if not active(issuer, kind):
            raise Forbidden(f"{org.id} holds no active {kind} licence")
    if collection.transferable and not rule.get("transferable"):
        raise Fail(f"{args['schema']} needs a non-transferable collection, not {collection.id}")
    fields, prepared_by = args.get("fields", {}), args.get("prepared_by", "")
    private = set(fields) - set(rule.get("public", []))
    if private or not str(args.get("payload_hash") or "sha256:").startswith("sha256:"):
        raise Fail(f"only hashes, references and public fields go on a token (not {sorted(private) or 'payload_hash'})")
    if prepared_by and not seats(prepared_by, org):
        raise Forbidden(f"{prepared_by} does not represent {org.id}")
    meta = {**header(c, args["schema"], args.get("subject", ""), issuer, license=active(issuer, rule["license"]).id,
                     prepared_by=prepared_by, expires_at=int(args.get("expires_at", 0)),
                     payload_hash=args.get("payload_hash", ""), payload_ref=args.get("payload_ref", "")), **fields}
    token_id = yield from mint(collection, args["holder"], meta)
    return {"collection": collection.id, "token_id": token_id, "header": meta}


def revoking_credential(user, args, grant):
    return you_sign(f"Revokes the {quoted(args['schema'])} credential, token {whole('token_id', args['token_id'])}, "
                    f"issued by organization {quoted(args['organization'])}: the token is frozen for good",
                    ("Revocation reason", quoted(args.get("reason", ""))))


@op("credential.sign", signing=True, returns="token_id", summary=revoking_credential, title="Revoke a credential")
def credential_revoke(c, args):
    """Revoke the ``schema`` credential ``token_id`` that ``organization`` issued (you need a seat there that signs
    it; the ledger's token header must name both): the token is frozen for good, with ``reason``."""
    # The ledger has no burn: a revoked credential is frozen for good, with the reason on the ledger.
    rule, org, collection = signer(c, args)
    token_id = int(args["token_id"])
    meta = token_meta((yield from chain.call(collection.canister, "icrc7_token_metadata", f"({token_id} : nat)")))
    if (meta.get("ggg:issuer"), meta.get("ggg:schema")) != (f"Organization:{org.id}", args["schema"]):
        raise Fail(f"token {token_id} of {collection.id} is not a {args['schema']} credential issued by {org.id}")
    reason = quote(f"ggg:revoked by Organization:{org.id}: {args.get('reason', '')}")
    arg = f"(record {{ token_id = {token_id} : nat; reason = opt {reason} }})"
    yield from ledger(collection, "freeze_token", arg)
    return {"collection": collection.id, "token_id": token_id, "status": "revoked"}


@op(None, read=True, public=True)
def credential_verify(c, args):
    """Check a token's `ggg:` ``header`` (as read from the ledger, with its ``frozen`` flag) against this realm."""
    h, now = args["header"], chain.now()
    rule, used = schema_of(h["ggg:schema"]), License[h.get("ggg:license") or "-"]
    genuine = bool(used and used.holder == h["ggg:issuer"] and used.kind == rule["license"]
                   and used.issued_at and used.issued_at <= int(h["ggg:issued_at"]))
    checks = {"ours": h.get("ggg:quarter") == chain.self_id(), "revoked": bool(args.get("frozen")),
              # I-AST-3: once the licence is gone, the codex decides whether its credentials stand.
              "licensed": genuine and (rule.get("after_license") == "keep" or bool(active(h["ggg:issuer"], used.kind))),
              "expired": bool(h.get("ggg:expires_at")) and now >= int(h["ggg:expires_at"])}
    return {"valid": checks["ours"] and checks["licensed"] and not checks["expired"] and not checks["revoked"],
            "checks": checks}
