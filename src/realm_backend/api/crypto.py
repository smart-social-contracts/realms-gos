from core import auth, chain, language
from core.db import Conflict, Fail, Forbidden, deref, find, next_id, page_by, view
from ggg import Department, Dispatch, Document, External, KeyEnvelope, Realm
from core.op import op
from core.pending import named, principals, quoted
from api.external import queue
from api.proposals import appointed, holders, root

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
        raise Forbidden(f"you do not manage keys at {scope}")


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


@op("self.data_manage", returns="public_key")
def crypto_public_key(c, args):
    """The realm's vetKD public key: wrap a scope's key for any principal with it (IBE, identity = principal)."""
    reply = yield from chain.call(MGMT, "vetkd_public_key", f'(record {{ canister_id = null; context = blob '
                                  f'"{blob(CONTEXT)}"; key_id = {key_id()} }})', 26_000_000_000)
    return {"public_key": blob_hex(reply)}


def deriving(user, args, grant):
    """The key goes to whoever holds the transport key, for good: the one sentence the member must read."""
    return [{"label": "Warning about your key",
             "value": f"This gives {named(grant)} a permanent copy of your key. "
                      "Revoking power of attorney does not take it back."},
            {"label": "What changes",
             "value": "Your key is sent encrypted to the transport key above, and whoever holds the matching private key "
                      "opens everything that was ever wrapped for you"}]


@op("self.data_manage", returns="encrypted_key", secret=("encrypted_key",), delivers=("encrypted_key",),
    summary=deriving, title="Hand over a copy of your key")
def crypto_derive_key(c, args):
    """Your vetKey, encrypted to ``transport_public_key`` (hex). It opens only what was wrapped for you."""
    reply = yield from chain.call(MGMT, "vetkd_derive_key", f'(record {{ input = blob "{blob(c.user.encode().hex())}"; '
                                  f'context = blob "{blob(CONTEXT)}"; key_id = {key_id()}; transport_public_key = '
                                  f'blob "{blob(args["transport_public_key"])}" }})', 54_000_000_000)
    return {"encrypted_key": blob_hex(reply)}


def granting(user, args, grant):
    """Who gets a key to the scope. When it is the delegate alone, the scope is all of yours that it reads."""
    manager(args["scope"], user)
    names, scope = principals(args["keys"], "keys"), quoted(args["scope"])
    if set(args["keys"]) == {grant.delegate}:
        effect = f"{named(grant)} will be able to read {scope}, and nothing else of yours"
    else:
        effect = f"{names} will be able to read {scope}"
    return [{"label": "Keys go to", "value": names}, {"label": "What changes", "value": effect}]


@op("self.data_manage", returns="granted", secret=("keys",), summary=granting, title="Give someone a key")
def crypto_grant(c, args):
    """Store wrapped keys at ``scope``: ``keys`` maps principal to wrapped key. Replaces earlier wraps."""
    manager(args["scope"], c.user)
    return {"granted": grant(args["scope"], args["keys"], c.user)}


def revoking(user, args, grant):
    """Who loses the key to the scope; you may be among them."""
    manager(args["scope"], user)
    names = principals(args["principals"], "principals")
    you = ", and so do you" if user in args["principals"] else ""
    return [{"label": "Loses access", "value": names},
            {"label": "What changes", "value": f"{names} lose their key to {quoted(args['scope'])}{you}"}]


@op("self.data_manage", returns="revoked", summary=revoking, title="Take keys away")
def crypto_revoke(c, args):
    """Delete the wrapped keys of ``principals`` at ``scope`` (managers only). Returns ``revoked``."""
    manager(args["scope"], c.user)
    return {"revoked": revoke(args["scope"], args["principals"])}


def own_envelope(args):
    """Reading your own envelope takes no permission, so that a principal that is no member, such as an AI connection given
    one scope's key, reads the key it was given: an envelope is useless without the key it is wrapped for."""
    return "self.join"


@op("self.data_manage", read=True, returns="envelope", perm_for=own_envelope)
def crypto_envelope(c, args):
    """Your wrapped key at ``scope``: nobody can read another principal's envelope."""
    e = find(KeyEnvelope, f"{args['scope']}|{c.user}")
    return {"envelope": e.id, "wrapped_dek": e.wrapped_dek, "granted_by": e.granted_by, "at": e.at}


@op("self.data_manage", read=True, returns="scopes")
def crypto_scopes(c, args):
    """The scopes where you hold a wrapped key, ``count`` at a time (default 100, at most 500) from row ``from``
    (default 1). Returns ``scopes`` and ``next`` (null at the end)."""
    rows, nxt = page_by(KeyEnvelope, "principal", c.user, args)
    return {"scopes": [e.scope for e in rows], "next": nxt}


@op("self.data_manage", read=True, returns="principals")
def crypto_access(c, args):
    """Who holds a key at ``scope`` (managers only), ``count`` at a time (default 100, at most 500) from row
    ``from`` (default 1). Returns ``principals`` and ``next`` (null at the end)."""
    manager(args["scope"], c.user)
    rows, nxt = page_by(KeyEnvelope, "scope", args["scope"], args)
    return {"principals": [e.principal for e in rows], "next": nxt}


def scope_of(doc):
    return f"dept:{doc.department}:doc:{doc.id}"


def staff(department, principal):
    if principal not in managers(f"dept:{department}"):
        raise Forbidden(f"only {department}'s staff manage its documents")


def stored_languages(args):
    if "languages" not in args:
        raise Fail("languages is required: the BCP 47 tags of the document's languages, such as [en]")
    language.require_all(args["languages"])


def document_line(doc):
    return {"label": "Document", "value": f"{doc.id} {quoted(doc.title)} of {quoted(doc.department)}"}


def storing(user, args, grant):
    """A document stored with ``keys`` gives those principals its key: that is what asks the member."""
    staff(args["department"], user)
    doc = find(Document, args["document"]) if args.get("document") else None
    if doc and doc.department != args["department"]:
        raise Fail(f"{doc.id} belongs to {doc.department}")
    names = principals(args.get("keys") or {}, "keys")
    what = f"Replaces {doc.id}" if doc else "Stores a new document"
    return [{"label": "Keys go to", "value": names},
            {"label": "What changes", "value": f"{what}, and {names} will be able to read it"}]


@op("document.manage", check=stored_languages, private=("keys",), secret=("keys",), summary=storing,
    title="Store a document and give out its key")
def document_store(c, args):
    """Store a ``department`` document (or replace ``document``): ``title``, ``languages`` (BCP 47 tags), ``ref`` and
    ``hash`` of its ciphertext, optional ``kind`` (minutes, an ordinance), ``facts`` (what legislation reads about
    it) and ``keys`` (principal to wrapped key). A replacement that leaves out ``kind`` or ``facts`` keeps them.
    Returns ``document`` and its key ``scope``."""
    fields = {"title": args["title"], "languages": args["languages"], "ref": args["ref"], "hash": args["hash"],
              "at": chain.now()}
    staff(args["department"], c.user)
    doc = find(Document, args["document"]) if args.get("document") else None
    if doc and doc.department != args["department"]:
        raise Fail(f"{doc.id} belongs to {doc.department}")
    doc = doc or Document(id=next_id(Document, "DOC"), department=args["department"], author=c.user)
    for k, v in fields.items():
        setattr(doc, k, v)
    for k in ("kind", "facts"):  # left out, they stay as they are: a screen that predates them re-stores a document
        if args.get(k) is not None:
            setattr(doc, k, args[k])
    grant(scope_of(doc), args.get("keys") or {}, c.user)
    return {"document": doc.id, "scope": scope_of(doc)}


RECIPIENTS = ("User", "Organization", "External")


def recipient_of(args):
    """The ``recipient`` of a share, which must exist, and the ``basis`` it states, if any, which must say its legal
    basis and purpose."""
    recipient, basis = args["recipient"], args.get("basis")
    if not isinstance(recipient, str) or recipient.partition(":")[0] not in RECIPIENTS:
        raise Fail(f"a document is shared with a User, an Organization or an External, not {recipient}")
    deref(recipient)
    if basis is not None and not (isinstance(basis, dict) and basis.get("legal_basis") and basis.get("purpose")):
        raise Fail("a basis states its legal_basis and its purpose")


def sharing(user, args, grant):
    doc = find(Document, args["document"])
    staff(doc.department, user)
    recipient_of(args)
    kind, _, who = args["recipient"].partition(":")
    recipient = f"{kind} {principals([who], 'recipients')}" if kind == "User" else f"{kind} {quoted(who)}"
    names = principals(args["keys"], "keys")
    return [document_line(doc), {"label": "Shared with", "value": recipient},
            {"label": "Keys go to", "value": names},
            {"label": "What changes", "value": f"{names} will be able to read {doc.id}"}]


@op("document.manage", private=("keys",), secret=("keys",), returns="granted", summary=sharing,
    title="Share a document")
def document_share(c, args):
    """Share ``document`` with ``recipient`` (``User:id``, ``Organization:id`` or ``External:id``), who must exist:
    ``keys`` maps principal to wrapped key (for an External, the principal that delivers to it). State the ``basis``
    (``legal_basis``, ``purpose``, optional ``legislation`` and ``special_category``) and any ``facts`` legislation
    reads; the audit log keeps them with the recipient. Returns ``granted``."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    recipient_of(args)
    return {"granted": grant(scope_of(doc), args["keys"], c.user)}


def sent_to(doc, journal):
    """Whether a dispatch of ``doc`` to ``journal`` is queued or confirmed. A failed one may be sent again."""
    return any(p["journal"] == journal.id and Dispatch[p["dispatch"]].status != "failed" for p in doc.publications)


def publishing(user, args, grant):
    doc = find(Document, args["document"])
    staff(doc.department, user)
    journal = f", and sent to the journal {quoted(args['to'])}" if args.get("to") else ""
    return [document_line(doc),
            {"label": "What changes",
             "value": f"The content at {quoted(args['ref'])} becomes public: anyone, signed in or not, reads it, and it "
                      f"cannot be taken back{journal}"}]


@op("document.manage", returns="document", summary=publishing, title="Publish a document")
def document_publish(c, args):
    """Make ``document`` public: the content in clear is at ``ref``, with its ``hash``, and anyone reads the record
    with document_get, signed in or not. Publish only what everyone may read: a published copy holds no personal
    data (I-PRV-1). Publishing the same content again is not an error; other content is a conflict. With a journal
    outside the realm in ``to`` (an outside body of kind journal) a dispatch to it is queued, once unless it failed:
    its confirmation gives the document's publication the journal's date and reference."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    journal = find(External, args["to"]) if args.get("to") else None
    if journal is not None and journal.kind != "journal":
        raise Fail(f"{journal.id} is not a journal")
    published = {"ref": args["ref"], "hash": args["hash"]}
    if doc.public not in (None, published):
        raise Conflict(f"{doc.id} is already published with other content")
    doc.public = published
    if journal is not None and not sent_to(doc, journal):
        sent = queue(journal, "publication", doc.title, published, document=doc.id)
        doc.publications = doc.publications + [{"journal": journal.id, "dispatch": sent.id, "published_at": None,
                                                "ref": None}]
    return {"document": doc.id}


def revoking_document(user, args, grant):
    doc = find(Document, args["document"])
    staff(doc.department, user)
    names = principals(args["principals"], "principals")
    return [document_line(doc), {"label": "Loses access", "value": names},
            {"label": "What changes", "value": f"{names} can no longer open {doc.id}"}]


@op("document.manage", returns="revoked", summary=revoking_document, title="Take a document's keys away")
def document_revoke(c, args):
    """Delete the wrapped keys of ``principals`` for ``document``. Returns ``revoked``."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    return {"revoked": revoke(scope_of(doc), args["principals"])}


def deleting(user, args, grant):
    doc = find(Document, args["document"])
    staff(doc.department, user)
    return [document_line(doc),
            {"label": "What changes",
             "value": "The document and every key to it are deleted for good. This cannot be undone. A document with "
                      "many keys is deleted over several calls, and each one asks you"}]


@op("document.manage", summary=deleting, title="Delete a document")
def document_delete(c, args):
    """Delete ``document``'s wrapped keys, ``count`` per call (default 100, at most 500), then the document itself:
    call again until ``deleted`` is true. Returns ``revoked`` (this call's principals) and ``deleted``."""
    doc = find(Document, args["document"])
    staff(doc.department, c.user)
    rows, more = page_by(KeyEnvelope, "scope", scope_of(doc), {"count": args.get("count")})
    for e in rows:
        e.delete()
    if more is None:
        doc.delete()
    return {"revoked": sorted(e.principal for e in rows), "deleted": more is None}


@op("document.manage", read=True, returns="documents")
def document_list(c, args):
    """The documents of ``department``, for its staff, all or only those of ``kind`` and ``language``, ``count`` at a
    time (default 100, at most 500) from row ``from`` (default 1). Returns ``documents`` and ``next`` (null at the
    end); a page can hold fewer than ``count`` when a filter is given: follow ``next``."""
    staff(args["department"], c.user)
    if args.get("language"):
        language.require(args["language"])
    rows, nxt = page_by(Document, "department", args["department"], args)
    wanted = [d for d in rows if args.get("kind") in (None, d.kind)
              and (not args.get("language") or language.listed(d.languages, args["language"]))]
    return {"documents": [view(d) for d in wanted], "next": nxt}


# What a reader without the key sees in place of a published document's ciphertext reference, hash and facts (I-PRV-4).
CONCEALED = {"ref": "", "hash": "", "facts": {}}


def on_staff(department, principal):
    """Whether ``principal`` is among ``managers("dept:<department>")``: its head or the holder of a seat in it. Found from
    their own seats, not by listing the department's: a reply that holds hundreds of documents asks this of each."""
    d = find(Department, department)
    return principal == d.head or any(a.position.department == d for a in appointed(principal))


def holds_key(doc, principal):
    return KeyEnvelope[f"{scope_of(doc)}|{principal}"] is not None


def reads_ciphertext(doc, principal):
    """Whether ``principal`` reads the ciphertext reference, hash and facts of ``doc``: its department's staff and the
    holders of its key. Anyone else reads only the public copy of a published document."""
    return holds_key(doc, principal) or on_staff(doc.department, principal)


@op("self.data_view", read=True, public=True)
def document_get(c, args):
    """``document`` with its key scope and your wrapped key, for its department's staff and key holders. Anyone, signed
    in or not, reads a published document: its public copy, title, kind and languages, without the ciphertext."""
    doc = find(Document, args["document"])
    if not reads_ciphertext(doc, c.user):
        if not doc.public:
            if c.user == chain.ANONYMOUS:
                raise auth.SignIn("sign in first")
            raise Forbidden(f"{doc.id} was not shared with you")
        return {"document": {**view(doc), **CONCEALED}, "scope": scope_of(doc), "wrapped_dek": "", "envelope": None}
    mine = KeyEnvelope[f"{scope_of(doc)}|{c.user}"]
    return {"document": view(doc), "scope": scope_of(doc), "wrapped_dek": mine.wrapped_dek if mine else "",
            "envelope": mine.id if mine else None}
