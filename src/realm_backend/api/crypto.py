from core import chain
from core.db import Fail, each, find, next_id, view
from ggg import Department, Document, KeyEnvelope, Realm
from core.op import op
from api.proposals import holders, root

MGMT = "aaaaa-aa"
CONTEXT = (bytes([len(b"realms")]) + b"realms").hex()  # one root context; recipients differ by vetKD input


def blob(hex_text):
    return "".join("\\" + hex_text[i:i + 2] for i in range(0, len(hex_text), 2))


def blob_hex(decoded):
    """The first blob in a decoded candid reply, as hex."""
    body, out, i = decoded[decoded.index('blob "') + 6:], bytearray(), 0
    while body[i] != '"':
        if body[i] != "\\":
            out += body[i].encode()
            i += 1
        elif body[i + 1] in '\\"':
            out += body[i + 1].encode()
            i += 2
        else:
            out.append(int(body[i + 1:i + 3], 16))
            i += 3
    return out.hex()


def key_id():
    name = "dfx_test_key" if Realm["1"].network == "local" else "test_key_1"
    return f'record {{ curve = variant {{ bls12_381_g2 = null }}; name = "{name}" }}'


def managers(scope):
    """Who may grant and revoke keys at ``scope``: the owner of a ``user:`` scope, the staff of a ``dept:``
    scope's department, the root department's staff for ``realm:``."""
    kind, _, rest = scope.partition(":")
    if kind == "user":
        return {rest.split(":")[0]}
    if kind not in ("dept", "realm"):
        raise Fail(f"unknown scope kind '{kind}'")
    d = find(Department, rest.split(":")[0]) if kind == "dept" else root()
    return holders(d) | ({d.head} - {""})


def manager(scope, principal):
    if principal not in managers(scope):
        raise Fail(f"you do not manage keys at {scope}")


def grant(scope, keys, by):
    for principal, wrapped in keys.items():
        e = KeyEnvelope[f"{scope}|{principal}"] or KeyEnvelope(id=f"{scope}|{principal}", scope=scope,
                                                               principal=principal)
        e.wrapped_dek, e.granted_by, e.at = wrapped, by, chain.now()
    return sorted(keys)


def revoke(scope, principals):
    for p in principals:
        e = KeyEnvelope[f"{scope}|{p}"]
        if e:
            e.delete()
    return sorted(principals)


@op("self.data_manage")
def crypto_public_key(c, args):
    """The realm's vetKD public key: wrap a scope's key for any principal with it (IBE, identity = principal)."""
    reply = yield from chain.call(MGMT, "vetkd_public_key", f'(record {{ canister_id = null; context = blob '
                                  f'"{blob(CONTEXT)}"; key_id = {key_id()} }})', 26_000_000_000)
    return {"public_key": blob_hex(reply)}


@op("self.data_manage")
def crypto_derive_key(c, args):
    """Your vetKey, encrypted to ``transport_public_key`` (hex). It opens only what was wrapped for you."""
    reply = yield from chain.call(MGMT, "vetkd_derive_key", f'(record {{ input = blob "{blob(c.user.encode().hex())}"; '
                                  f'context = blob "{blob(CONTEXT)}"; key_id = {key_id()}; transport_public_key = '
                                  f'blob "{blob(args["transport_public_key"])}" }})', 54_000_000_000)
    return {"encrypted_key": blob_hex(reply)}


@op("self.data_manage")
def crypto_grant(c, args):
    """Store wrapped keys at ``scope``: ``keys`` maps principal to wrapped key. Replaces earlier wraps."""
    manager(args["scope"], c.user)
    return {"granted": grant(args["scope"], args["keys"], c.user)}


@op("self.data_manage")
def crypto_revoke(c, args):
    """Delete the wrapped keys of ``principals`` at ``scope`` (managers only). Returns ``revoked``."""
    manager(args["scope"], c.user)
    return {"revoked": revoke(args["scope"], args["principals"])}


@op("self.data_manage", read=True)
def crypto_envelope(c, args):
    """Your wrapped key at ``scope``: nobody can read another principal's envelope."""
    e = find(KeyEnvelope, f"{args['scope']}|{c.user}")
    return {"wrapped_dek": e.wrapped_dek, "granted_by": e.granted_by, "at": e.at}


@op("self.data_manage", read=True)
def crypto_scopes(c, args):
    """The scopes where you hold a wrapped key."""
    return {"scopes": sorted(e.scope for e in each(KeyEnvelope, "principal", c.user))}


@op("self.data_manage", read=True)
def crypto_access(c, args):
    """Who holds a key at ``scope`` (managers only)."""
    manager(args["scope"], c.user)
    return {"principals": sorted(e.principal for e in each(KeyEnvelope, "scope", args["scope"]))}


def scope_of(doc):
    return f"dept:{doc.department}:doc:{doc.id}"


def staff(department, principal):
    if principal not in managers(f"dept:{department}"):
        raise Fail(f"only {department}'s staff manage its documents")


@op("document.manage")
def document_store(c, args):
    """Store a ``department`` document (or replace ``document``): ``title``, ``ref`` and ``hash`` of its ciphertext,
    and optional ``keys`` (principal to wrapped key). Returns ``document`` and its key ``scope``."""
    fields = {"title": args["title"], "ref": args["ref"], "hash": args["hash"], "at": chain.now()}
    staff(args["department"], c.user)
    doc = find(Document, args["document"]) if args.get("document") else None
    if doc and doc.department != args["department"]:
        raise Fail(f"{doc.id} belongs to {doc.department}")
    doc = doc or Document(id=next_id(Document, "DOC"), department=args["department"], author=c.user)
    for k, v in fields.items():
        setattr(doc, k, v)
    grant(scope_of(doc), args.get("keys") or {}, c.user)
    return {"document": doc.id, "scope": scope_of(doc)}


@op("document.manage")
def document_share(c, args):
    """Share ``document``: ``keys`` maps principal to wrapped key. Returns ``granted``."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    return {"granted": grant(scope_of(doc), args["keys"], c.user)}


@op("document.manage")
def document_revoke(c, args):
    """Delete the wrapped keys of ``principals`` for ``document``. Returns ``revoked``."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    return {"revoked": revoke(scope_of(doc), args["principals"])}


@op("document.manage")
def document_delete(c, args):
    """Delete ``document`` and every wrapped key for it."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    revoke(scope_of(doc), [e.principal for e in each(KeyEnvelope, "scope", scope_of(doc))])
    doc.delete()


@op("document.manage", read=True)
def document_list(c, args):
    """The documents of ``department`` (id, title, hash, author, time), for its staff."""
    staff(args["department"], c.user)
    rows = each(Document, "department", args["department"])
    return {"documents": [view(d, "id", "title", "hash", "author", "at") for d in rows]}


@op("self.data_view", read=True)
def document_get(c, args):
    """The ciphertext reference of ``document`` and your wrapped key: for its department's staff and key holders."""
    doc = find(Document, args["document"])
    mine = KeyEnvelope[f"{scope_of(doc)}|{c.user}"]
    if mine is None and c.user not in managers(f"dept:{doc.department}"):
        raise Fail(f"{doc.id} was not shared with you")
    return {"document": view(doc), "scope": scope_of(doc), "wrapped_dek": mine.wrapped_dek if mine else ""}
