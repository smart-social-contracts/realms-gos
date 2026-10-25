
import candid
import chain
from db import Fail, deref, each, find, next_id, page, view
from ggg import Appointment, Codex, Land, License, NFTToken, Organization, Realm, User, Verdict, Zone
from licenses import active, principals
from op import op
from proposals import realm_wide

LAND = "LAND"
PARCEL = ("id", "x", "y", "width", "height", "kind", "owner", "status", "token_id")


# --- ledger (ICRC-7, smart-social-contracts/ic-tokens), spoken in candid text ---

def quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def account(principal):
    return f"record {{ owner = principal {quote(principal)}; subaccount = null }}"


def value(v):
    if isinstance(v, int):
        return f"variant {{ Nat = {v} : nat }}" if v >= 0 else f"variant {{ Int = {v} : int }}"
    return f"variant {{ Text = {quote(v)} }}"


def ledger(collection, method, arg):
    reply = yield from chain.call(collection.canister, method, arg)
    found = candid.number(reply, "Ok") if "Ok" in reply else None
    if found is None:
        raise Fail(f"the {collection.id} ledger refused {method}: {reply}")
    return found


def mint(collection, owner, meta):
    if collection.supply_cap and collection.total_supply >= collection.supply_cap:
        raise Fail(f"{collection.id} has reached its supply cap")
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


def owners(args):
    return principals(find(Land, args["land"]).owner)


def transfer_parties(args):
    return owners(args) | principals(owner_ref(args["to"]))


@op("nft.mint", governed=realm_wide)
def land_register(c, args):
    x, y, w, h = (int(args.get(k, d)) for k, d in (("x", 0), ("y", 0), ("width", 1), ("height", 1)))
    if w < 1 or h < 1:
        raise Fail("a parcel is at least one cell wide and high")
    owner = owner_ref(args["owner"])
    taken = next((p for p in each(Land) if p.status != "revoked" and overlaps(p, x, y, w, h)), None)
    if taken:
        raise Fail(f"the parcel overlaps {taken.id}")
    land = Land(id=next_id(Land, "L"), x=x, y=y, width=w, height=h, kind=args.get("kind", "unassigned"),
                owner=owner, registered_by=c.user, metadata=args.get("metadata", {}))
    return {"land": land.id}


@op("nft.force_transfer", signing=True, governed=realm_wide, parties=transfer_parties)
def land_transfer(c, args):
    land, to, order = find(Land, args["land"]), owner_ref(args["to"]), deref(args["order"])
    if not isinstance(order, Verdict) or order.status not in ("issued", "final"):
        raise Fail("moving a parcel needs an issued court verdict")
    if land.status not in ("active", "disputed") or to == land.owner:
        raise Fail(f"{land.id} cannot be moved to {to}")
    if land.token_id:
        arg = (f"(record {{ token_id = {land.token_id} : nat; to = {account(wallet(to))}; "
               f"memo = opt {quote(args['order'])} }})")
        yield from ledger(find(NFTToken, LAND), "force_transfer", arg)
    land.owner = to
    return {"land": land.id, "owner": to}


@op("realm.data_view", read=True)
def land_list(c, args):
    owner = owner_ref(args["owner"]) if args.get("owner") else None
    rows, more = page(Land, args, lambda p: owner in (None, p.owner))
    return {"lands": [view(p, *PARCEL) for p in rows], "next": more}


@op("realm.data_view", read=True)
def land_map(c, args):
    x, y = int(args.get("min_x", 0)), int(args.get("min_y", 0))
    w, h = int(args.get("max_x", 100)) - x, int(args.get("max_y", 100)) - y
    return {"parcels": [view(p, *PARCEL) for p in each(Land) if p.status != "revoked" and overlaps(p, x, y, w, h)],
            "zones": [view(z, "id", "name", "kind") for z in each(Zone) if z.land is None]}


@op("nft.mint")
def land_mint_nft(c, args):
    land, collection = find(Land, args["land"]), find(NFTToken, LAND)
    if land.token_id or land.status != "active":
        raise Fail(f"{land.id} is {land.status}{' and already minted' if land.token_id else ''}")
    meta = {**header(c, "ggg.land/1", f"Land:{land.id}", f"Department:{realm_wide(args).id}",
                     prepared_by=args.get("prepared_by", "")),
            "x": land.x, "y": land.y, "width": land.width, "height": land.height, "kind": land.kind}
    token_id = yield from mint(collection, wallet(land.owner), meta)
    land.token_id = str(token_id)
    return {"land": land.id, "token_id": token_id}


def freeze(args, on):
    land = find(Land, args["land"])
    if land.status != ("active" if on else "disputed"):
        raise Fail(f"{land.id} is {land.status}")
    if land.token_id:
        arg = (f"(record {{ token_id = {land.token_id} : nat; reason = opt {quote(args.get('reason', ''))} }})" if on
               else f"({land.token_id} : nat)")
        yield from ledger(find(NFTToken, LAND), "freeze_token" if on else "unfreeze_token", arg)
    land.status = "disputed" if on else "active"
    return {"land": land.id, "status": land.status}


@op("nft.freeze", signing=True, parties=owners)
def land_freeze(c, args):
    return (yield from freeze(args, True))


@op("nft.freeze", signing=True, parties=owners)
def land_unfreeze(c, args):
    return (yield from freeze(args, False))


@op("self.data_manage", delegable=True)
def zone_set(c, args):
    zone = Zone[args["zone"]]
    if zone and zone.user not in ("", c.user):
        raise Fail(f"zone {zone.id} is someone else's home")
    for old in [z for z in each(Zone, "user", c.user) if z.id != args["zone"]]:
        old.user = ""
    zone = zone or Zone(id=args["zone"])
    zone.user, zone.name, zone.kind = c.user, args.get("name", zone.name), args.get("kind", zone.kind)
    return {"zone": zone.id}


@op("self.data_manage", delegable=True)
def zone_clear(c, args):
    zone = find(Zone, args["zone"])
    if zone.user != c.user:
        raise Fail(f"zone {zone.id} is not your home")
    zone.user = ""


# --- credentials: NFTs whose schemas the active codex declares ---

def schema_of(name):
    """A codex manifest's `credentials` block: {schema: {license, positions, collection, public, after_license}}."""
    for codex in each(Codex):
        rule = (codex.manifest or {}).get("credentials", {}).get(name)
        if codex.status == "active" and rule:
            return rule
    raise Fail(f"no active codex declares the schema '{name}'")


def seats(user, org):
    return [a for a in each(Appointment, "user", user)
            if a.status == "active" and a.position.organization and a.position.organization.id == org.id]


def signer(c, args):
    """The schema, organization and collection, once the caller holds a seat that signs it (I-AST-1)."""
    rule, org = schema_of(args["schema"]), find(Organization, args["organization"])
    if org.status != "active":
        raise Fail(f"{org.id} is {org.status}")
    accepted = set(rule.get("positions", []))
    if not any(not a.agent and {a.position.id, a.position.title} & accepted for a in seats(c.by, org)):
        raise Fail(f"you hold no seat at {org.id} that signs {args['schema']}")
    return rule, org, find(NFTToken, rule["collection"])


@op("credential.sign", signing=True)
def credential_issue(c, args):
    rule, org, collection = signer(c, args)
    issuer = f"Organization:{org.id}"
    for kind in filter(None, (rule["license"], collection.required_license)):
        if not active(issuer, kind):
            raise Fail(f"{org.id} holds no active {kind} licence")
    if collection.transferable and not rule.get("transferable"):
        raise Fail(f"{args['schema']} needs a non-transferable collection, not {collection.id}")
    fields, prepared_by = args.get("fields", {}), args.get("prepared_by", "")
    private = set(fields) - set(rule.get("public", []))
    if private or not str(args.get("payload_hash") or "sha256:").startswith("sha256:"):
        raise Fail(f"only hashes, references and public fields go on a token (not {sorted(private) or 'payload_hash'})")
    if prepared_by and not seats(prepared_by, org):
        raise Fail(f"{prepared_by} does not represent {org.id}")
    meta = {**header(c, args["schema"], args.get("subject", ""), issuer, license=active(issuer, rule["license"]).id,
                     prepared_by=prepared_by, expires_at=int(args.get("expires_at", 0)),
                     payload_hash=args.get("payload_hash", ""), payload_ref=args.get("payload_ref", "")), **fields}
    token_id = yield from mint(collection, args["holder"], meta)
    return {"collection": collection.id, "token_id": token_id, "header": meta}


@op("credential.sign", signing=True)
def credential_revoke(c, args):
    # The ledger has no burn: a revoked credential is frozen for good, with the reason on the ledger.
    rule, org, collection = signer(c, args)
    reason = quote(f"ggg:revoked by Organization:{org.id}: {args.get('reason', '')}")
    arg = f"(record {{ token_id = {int(args['token_id'])} : nat; reason = opt {reason} }})"
    yield from ledger(collection, "freeze_token", arg)
    return {"collection": collection.id, "token_id": int(args["token_id"]), "status": "revoked"}


@op(None, read=True, public=True)
def credential_verify(c, args):
    """Check a token's `ggg:` header (as read from the ledger, with its frozen flag) against this realm."""
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
