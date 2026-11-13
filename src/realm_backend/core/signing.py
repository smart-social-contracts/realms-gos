"""Signatures by the realm's canister on its documents (docs/codices/journal-signature.md). The official journal is the
first kind of document (core.journal_signing).

A document has a ``kind`` and a ``subject`` (a journal's is its day). Its owner builds the ``body`` from stored facts and
asks for the document to be made: written as canonical JSON, hashed with SHA-256 and kept, as it is, with a short
verification code. A chained kind links each document to the one before it by digest, so that one altered or dropped later
shows; making a document costs SHA-256 and nothing else. A signature is asked for later, for one document or a batch of
them: their digests are the leaves of a Merkle tree, and the realm has the IC's threshold Schnorr key for this canister
and the kind (Ed25519, the management canister's ``sign_with_schnorr``) sign the tree's root. Only code running as this
canister can ask for a signature under that key, and the path ``realms:<kind>`` keeps one kind's key from signing another's
documents. A batch of one has its digest for root. The signature is added to the document and never replaced: a second
signature of the same root need not be the same, and a document is signed once.

The text is fixed when the document is made, and what the signature attests is that text as it was made, at ``made_at``,
whenever the signature is asked for. The documents made before a signature could wait were signed as they were made, and
their text says ``signed_at`` where a new one says ``made_at``: they are unchanged.
"""

import hashlib
import json

from core import chain, ic_keys
from core.candid import Reader, idl_hash
from core.db import Conflict, Fail, NotFound, Unavailable, first
from ggg import Realm, SignedDocument

MANAGEMENT = "aaaaa-aa"
ALGORITHM = "ed25519"
VERSION = 1  # of the canonical form
# What a signature costs, by key: the fee of the subnet that holds it, 10,000,000,000 cycles for test_key_1 (a 13-node
# subnet) and 26,153,846,153 for key_1 (34 nodes), which a local key is charged as too. The realm attaches the fee and
# half as much again, and what is not spent comes back, so that a subnet that grows still signs. A call with less
# attached is refused with the fee in the error.
FEES = {"test_key_1": 10_000_000_000}
DEAREST_FEE = 26_153_846_153
KIND_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"  # ASCII only: a kind is a path of the key and a part of an id
BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # RFC 4648: the canister's base64 has no b32encode
CODE_BYTES = 10  # of the digest that the verification code keeps: 80 bits, 16 digits in base32
STATE = "signing"  # in Realm.manifest_data: {kind: {"head": {"subject", "digest"}, "failure": {...} or None}}
# The documents being signed now, ``{(kind, subject): when the call began}``: in the heap, since a call out of the canister
# does not outlive the heap, and a document being signed is not signed again meanwhile. A trap after the call is back would
# leave an entry no one removes, so one older than STALE seconds is no longer counted.
IN_FLIGHT = {}
STALE = 600


# ---- The key


def attached(name):
    """The cycles a signature with key ``name`` is sent with."""
    return FEES.get(name, DEAREST_FEE) * 3 // 2


def path(kind):
    """The derivation path of ``kind``'s key: what keeps one kind's key from signing another kind's documents."""
    if not kind or any(ch not in KIND_CHARS for ch in kind):
        raise Fail("a kind of document is lower-case letters, digits and dashes")
    return "realms:" + kind


def blob(data):
    """``data`` (bytes) as a Candid blob literal."""
    return 'blob "' + "".join("\\%02x" % byte for byte in data) + '"'


def key_args(kind, name):
    """The fields of a management call that name the key ``name``: the derivation path and the key id."""
    key_id = f'record {{ algorithm = variant {{ {ALGORITHM} }}; name = "{name}" }}'
    return f"derivation_path = vec {{ {blob(path(kind).encode())} }}; key_id = {key_id}"


def public_key(kind, name):
    """This canister's public key for ``kind`` under key ``name``, 32 bytes. It costs no cycles. A generator."""
    arg = f"(record {{ canister_id = null; {key_args(kind, name)} }})"
    reply = yield from chain.call(MANAGEMENT, "schnorr_public_key", arg)
    return Reader(reply).args()[0][idl_hash("public_key")]


def sign_root(kind, name, root):
    """The signature, 64 bytes, of ``root`` (32 bytes) under this canister's key ``name`` for ``kind``. A generator."""
    reply = yield from chain.call(MANAGEMENT, "sign_with_schnorr",
                                  f"(record {{ message = {blob(root)}; {key_args(kind, name)}; aux = null }})",
                                  attached(name))
    signature = Reader(reply).args()[0][idl_hash("signature")]
    if len(signature) != 64:
        raise Exception(f"sign_with_schnorr gave {len(signature)} bytes, not an Ed25519 signature")
    return signature


# ---- The documents as they are signed


def escaped(ch):
    """The JSON escape of a character above ``~``, as CPython's ``ensure_ascii`` writes it: ``\\uXXXX``, and a pair of
    them for a character above U+FFFF."""
    code = ord(ch)
    if code > 0xFFFF:
        code -= 0x10000
        return "\\u%04x\\u%04x" % (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
    return "\\u%04x" % code


def canonical(document):
    """The text that is signed: ``document`` as JSON with sorted keys, no spaces and ASCII only, so that it reads the
    same everywhere. The escaping is done here and not left to ``json.dumps``: the canister's writes non-ASCII
    characters as they are (core.fastjson), and the text must be the same there as under CPython. It is kept and served
    as it is, never written again from the facts."""
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return "".join(ch if ch <= "~" else escaped(ch) for ch in text)


def digest_of(text):
    return hashlib.sha256(text.encode()).hexdigest()


def code_of(digest):
    """The verification code of a document: the first 80 bits of its ``digest`` in base32, as ``XXXX-XXXX-XXXX-XXXX``.
    It finds the document; the signature is what proves it."""
    number = int.from_bytes(bytes.fromhex(digest)[:CODE_BYTES], "big")
    return grouped("".join(BASE32[(number >> shift) & 31] for shift in range(CODE_BYTES * 8 - 5, -1, -5)))


def grouped(digits):
    return "-".join(digits[i:i + 4] for i in range(0, len(digits), 4))


def node(left, right):
    """A node of the tree: SHA-256 of 0x01 and its two children. A document's text starts with ``{``, so no leaf can
    pass for a node."""
    return hashlib.sha256(b"\x01" + left + right).digest()


def merkle(leaves):
    """The root over ``leaves`` (digests, 32 bytes each, at least one) and the path of each: the steps from its leaf up,
    ``[side, sibling digest in hex]``, ``side`` being the one the sibling is on (``l`` or ``r``). A node with no sibling
    moves up unchanged. With one leaf the root is the leaf and the path is empty."""
    paths = [[] for _ in leaves]
    level = [(digest, [i]) for i, digest in enumerate(leaves)]  # a node, and the leaves under it
    while len(level) > 1:
        above = []
        for i in range(0, len(level) - 1, 2):
            (left, under_left), (right, under_right) = level[i], level[i + 1]
            for leaf in under_left:
                paths[leaf].append(["r", right.hex()])
            for leaf in under_right:
                paths[leaf].append(["l", left.hex()])
            above.append((node(left, right), under_left + under_right))
        if len(level) % 2:
            above.append(level[-1])
        level = above
    return level[0][0], paths


# ---- State


def state(kind):
    return (Realm["1"].manifest_data.get(STATE) or {}).get(kind) or {}


def keep(kind, **values):
    """Keep ``values`` with the signing state of ``kind``, besides what is there."""
    realm = Realm["1"]
    data = realm.manifest_data
    realm.manifest_data = {**data, STATE: {**data.get(STATE, {}), kind: {**state(kind), **values}}}


def head(kind):
    """The last document of a chained ``kind`` that was made, ``{"subject", "digest"}``; None while none is. A realm that
    signed each document as it was made has the last one signed here."""
    return state(kind).get("head")


def failures():
    """The last batch of each kind that could not be made or signed, ``{kind: {"subjects", "at", "error"}}``: for the IT
    admin team to read. What an owner makes or signs next is worked out from its documents, not from this."""
    return {kind: kept["failure"] for kind, kept in (Realm["1"].manifest_data.get(STATE) or {}).items()
            if kept.get("failure")}


def failed(kind, subjects, error):
    """Keep for the IT admin team that ``subjects`` of ``kind`` could not be made or signed, and why."""
    keep(kind, failure={"subjects": subjects, "at": chain.now(), "error": f"{type(error).__name__}: {error}"[:500]})


def document_id(kind, subject):
    return f"{kind}:{subject}"


# ---- Making the documents


def make(kind, chained, subjects_of, body_of):
    """Make the documents of ``kind`` that ``subjects_of()`` names, oldest first, with the body ``body_of(subject)`` gives
    each, and keep them unsigned. A ``chained`` kind links each to the one before it, the first to the last made
    (``head``). The text names the realm's public key, which is asked for first: the one wait. So the subjects and the
    bodies are taken once it is back, in one message, and what is made is what is there then; a subject made meanwhile by
    another call is left to it. A failure (the key could not be had) is kept for the IT admin team (``failures``, shown in
    ``codex_status``), nothing is made, and the result is None: the owner tries again at its next sweep. The subjects made
    by this call. A generator."""
    try:
        path(kind)  # a kind that is no name is refused before anything else
        name = ic_keys.key_name()
        key = yield from public_key(kind, name)
    except Exception as e:  # noqa: BLE001  whatever it is, only the documents wait
        failed(kind, subjects_of(), e)
        return None
    previous = head(kind)
    link = previous["digest"] if previous and chained else None
    made = []
    for subject in subjects_of():
        existing = SignedDocument[document_id(kind, subject)]
        if existing:  # made by another call: the next document follows it
            link = existing.digest if chained else None
            continue
        text = canonical({"v": VERSION, "kind": kind, "realm": chain.self_id(), "subject": subject,
                          "body": body_of(subject), "prev": link, "made_at": chain.now(),
                          "key": {"algorithm": ALGORITHM, "name": name, "path": path(kind), "public_key": key.hex()}})
        digest = digest_of(text)
        SignedDocument(id=document_id(kind, subject), code=code_of(digest), canonical=text, digest=digest)
        made.append(subject)
        link = digest if chained else None
    if chained and made:
        keep(kind, head={"subject": made[-1], "digest": link})
    return made


# ---- Signing


def made_for(stored):
    """The name of the key the text of the made document ``stored`` names: the one its signature has to be made with."""
    return json.loads(stored.canonical)["key"]["name"]


def being_signed(kind, subject):
    since = IN_FLIGHT.get((kind, subject))
    return since is not None and chain.now() - since < STALE


def sign_batch(kind, subjects):
    """Sign as one batch the made documents of ``subjects`` of ``kind`` that are not signed, are not being signed and were
    made for the key the realm has now, and keep the signature with them; the rest are left out. The subjects signed, []
    when there are none (and then nothing is written). A failure (a realm short of cycles, a call refused) is kept for the IT
    admin team (``failures``: the last, with its time and the subjects it concerned: information, nothing retries from it),
    nothing is kept of the batch, and the result is None: the owner tries again. A generator."""
    try:
        signed = yield from sign(kind, subjects)
    except Exception as e:  # noqa: BLE001  whatever it is, only the signature waits
        failed(kind, subjects, e)
        return None
    if signed and state(kind).get("failure"):  # a call that signs nothing writes nothing
        keep(kind, failure=None)
    return signed


def sign(kind, subjects):
    """What ``sign_batch`` does, raising what goes wrong. The key is named once, and a text that names another key (the
    realm's network or key rule changed since it was made) would not verify with it: that document is left out, and a
    signature of a later one covers it through the chain. A document is marked as being signed while the call is out, so
    that no other call asks for its signature; one signed meanwhile, which a mark lost to a trap allows, keeps its
    signature. A generator."""
    path(kind)
    name = ic_keys.key_name()
    waiting = []
    for subject in subjects:
        stored = SignedDocument[document_id(kind, subject)]
        if (stored is not None and not stored.signature and not being_signed(kind, subject)
                and made_for(stored) == name):
            waiting.append((subject, stored))
    if not waiting:
        return []
    # The balance, not what is spendable above the freezing reserve: a call that would take the realm below the reserve
    # still traps, and nothing is recorded of it (the realm's balance is topped up, as for any other call it makes).
    if chain.cycles() < attached(name):
        raise Fail(f"the realm has {chain.cycles()} cycles and a signature with {name} needs {attached(name)} attached")
    root, paths = merkle([bytes.fromhex(stored.digest) for _, stored in waiting])
    for subject, _ in waiting:
        IN_FLIGHT[(kind, subject)] = chain.now()
    try:
        signature = yield from sign_root(kind, name, root)
    finally:
        for subject, _ in waiting:
            IN_FLIGHT.pop((kind, subject), None)
    signed = []
    for (subject, _), leaf_path in zip(waiting, paths):
        stored = SignedDocument[document_id(kind, subject)]
        if not stored.signature:
            stored.root, stored.path, stored.signature = root.hex(), leaf_path, signature.hex()
            signed.append(subject)
    return signed


def request(kind, subject):
    """The proof of the made document ``subject`` of ``kind``, signing it now if it is not signed (what anyone's
    request for a signature does). ``(proof, signed)``: ``signed`` is whether this call made the signature, and the proof is
    None while another call is signing the document. NotFound when it is not made, Conflict when it was made for another key
    than the realm's now (it can never be signed: the error names both), Unavailable when the signature could not
    be made, whose reason is kept (``failures``) for the IT admin team and not told to the caller (it holds the realm's
    balance and what the management canister said): the document is as it was, and may be asked again. A generator."""
    stored = SignedDocument[document_id(kind, subject)]
    if stored is None:
        raise NotFound(f"no document {kind}:{subject[:40]} is made")
    if not stored.signature and made_for(stored) != ic_keys.key_name():
        raise Conflict(f"the document {subject} of {kind} was made for the key {made_for(stored)}: it cannot be signed "
                       f"while the realm's key is {ic_keys.key_name()}; ask for a later one, whose signature covers it")
    signed = yield from sign_batch(kind, [subject])
    if signed is None:
        raise Unavailable("the signature could not be made now: ask again later")
    found = proof(kind, subject)
    return (found if found["signature"] else None), bool(signed)


# ---- Reading


def proof(kind, subject):
    """The proof of the made document ``subject`` of ``kind``, or None if it is not made: its ``canonical`` text, which
    names the realm, kind, subject, body, the previous document's digest and the key; its ``digest``; the ``root`` of its
    batch and its ``path`` to it; the ``signature`` of the root; and the verification ``code``. All as stored. Until the
    document is signed its ``root``, ``path`` and ``signature`` are empty."""
    stored = SignedDocument[document_id(kind, subject)]
    if stored is None:
        return None
    return {"canonical": stored.canonical, "digest": stored.digest, "root": stored.root, "path": stored.path,
            "signature": stored.signature, "code": stored.code}


def find(code):
    """The ``(kind, subject)`` of the document with the verification ``code``, written with or without its dashes and in
    any case."""
    digits = "".join(ch for ch in str(code).upper() if ch.isalnum())
    if len(digits) != CODE_BYTES * 8 // 5:
        raise Fail("a verification code is 16 letters and digits, such as ABCD-EFGH-JKLM-NPQR")
    stored = first(SignedDocument, "code", grouped(digits))
    if stored is None:
        raise NotFound("no document has that verification code")
    kind, _, subject = stored.id.partition(":")
    return kind, subject
