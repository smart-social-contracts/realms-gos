"""Signatures by the realm's canister on its documents (docs/codices/journal-signature.md). The official journal is the
first kind of document (core.journal_signing).

A document has a ``kind`` and a ``subject`` (a journal's is its day). Its owner builds the ``body`` from stored facts
and asks for a batch to be signed: the documents of one kind, signed together. Each is written as canonical JSON, and
its SHA-256 is a leaf of a Merkle tree. The realm has the IC's threshold Schnorr key for this canister and the kind
(Ed25519, the management canister's ``sign_with_schnorr``) sign the tree's root: only code running as this canister can
ask for a signature under that key, and the path ``realms:<kind>`` keeps one kind's key from signing another's
documents. A batch of one has its digest for root. A document keeps its text, its digest, the root, its path in the
tree, the signature and a short verification code, and a chained kind links each document to the one before it by
digest, so that one altered or dropped later shows. A document is signed once and never again: a second signature of
the same root need not be the same.
"""

import hashlib
import json

from core import chain
from core.candid import Reader, idl_hash
from core.db import Fail, NotFound, first
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
TEST_NETWORKS = ("test", "staging", "demo")  # with local, the networks that use a test key (as api.quarters sees them)
KIND_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"  # ASCII only: a kind is a path of the key and a part of an id
BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # RFC 4648: the canister's base64 has no b32encode
CODE_BYTES = 10  # of the digest that the verification code keeps: 80 bits, 16 digits in base32
STATE = "signing"  # in Realm.manifest_data: {kind: {"head": {"subject", "digest"}, "failure": {...} or None}}


# ---- The key


def key_name():
    """The IC's key this realm signs with: its own on a local replica, a test key on a test network, else ``key_1``."""
    network = Realm["1"].network
    if network == "local":
        return "dfx_test_key"
    return "test_key_1" if network in TEST_NETWORKS else "key_1"


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


# ---- Signing


def state(kind):
    return (Realm["1"].manifest_data.get(STATE) or {}).get(kind) or {}


def keep(kind, **values):
    """Keep ``values`` with the signing state of ``kind``, besides what is there."""
    realm = Realm["1"]
    data = realm.manifest_data
    realm.manifest_data = {**data, STATE: {**data.get(STATE, {}), kind: {**state(kind), **values}}}


def head(kind):
    """The last document of a chained ``kind`` that was signed, ``{"subject", "digest"}``; None while none is."""
    return state(kind).get("head")


def failures():
    """The last batch of each kind that could not be signed, ``{kind: {"subjects", "at", "error"}}``."""
    return {kind: kept["failure"] for kind, kept in (Realm["1"].manifest_data.get(STATE) or {}).items()
            if kept.get("failure")}


def document_id(kind, subject):
    return f"{kind}:{subject}"


def sign_batch(kind, documents, chained, fresh=None):
    """Sign ``documents``, a list of ``(subject, body)`` in order, as one batch of ``kind``, and keep them. A
    ``chained`` kind links each to the one before it, the first to the last signed (``head``). ``fresh``, if given, is
    called when the signature is back and returns the documents as they are by then: a batch whose documents have
    changed meanwhile is not kept. True when the batch is signed or another has signed it meanwhile. A failure (a realm
    short of cycles, a call refused, documents changed while it was signed) is kept for the IT admin team
    (``failures``, shown in ``codex_status``), nothing is kept of the batch, and the result is False: the owner tries
    again at its next sweep. A generator."""
    subjects = [subject for subject, _ in documents]
    try:
        yield from sign(kind, documents, chained, fresh)
    except Exception as e:  # noqa: BLE001  whatever it is, only the signature waits
        keep(kind, failure={"subjects": subjects, "at": chain.now(), "error": f"{type(e).__name__}: {e}"[:500]})
        return False
    keep(kind, failure=None)
    return True


def sign(kind, documents, chained, fresh):
    """What ``sign_batch`` does, raising what goes wrong. The key is named once, so that a change of network while the
    calls are out cannot make the text name one key and the signature another. The texts are made once the public key is
    known, and the batch is dropped if it was overtaken while the calls were out, so that each document has one
    signature. A generator."""
    path(kind)  # a kind that is no name is refused before anything else
    previous, name = head(kind), key_name()
    if any(SignedDocument[document_id(kind, subject)] for subject, _ in documents):
        raise Fail(f"a document of {kind} is signed already")
    if chain.cycles() < attached(name):
        raise Fail(f"the realm has {chain.cycles()} cycles and a signature with {name} needs {attached(name)} attached")
    key = yield from public_key(kind, name)
    texts, link = [], previous["digest"] if previous and chained else None
    for subject, body in documents:
        text = canonical({"v": VERSION, "kind": kind, "realm": chain.self_id(), "subject": subject, "body": body,
                          "prev": link, "signed_at": chain.now(),
                          "key": {"algorithm": ALGORITHM, "name": name, "path": path(kind),
                                  "public_key": key.hex()}})
        texts.append((subject, text, digest_of(text)))
        link = texts[-1][2] if chained else None
    root, paths = merkle([bytes.fromhex(digest) for _, _, digest in texts])
    signature = yield from sign_root(kind, name, root)
    if overtaken(kind, previous, chained, texts):
        return
    if fresh is not None and fresh() != documents:
        raise Fail("the documents changed while they were signed")
    for (subject, text, digest), leaf_path in zip(texts, paths):
        SignedDocument(id=document_id(kind, subject), code=code_of(digest), canonical=text, digest=digest,
                       root=root.hex(), path=leaf_path, signature=signature.hex())
    if chained:
        keep(kind, head={"subject": texts[-1][0], "digest": texts[-1][2]})


def overtaken(kind, previous, chained, texts):
    """Whether another batch has signed since this one began: the chain has moved, or a subject is signed."""
    return (chained and head(kind) != previous) or any(SignedDocument[document_id(kind, s)] for s, _, _ in texts)


# ---- Reading


def proof(kind, subject):
    """The proof of the signed document ``subject`` of ``kind``, or None if it is not signed: its ``canonical`` text,
    which names the realm, kind, subject, body, the previous document's digest and the key; its ``digest``; the ``root``
    of its batch and its ``path`` to it; the ``signature`` of the root; and the verification ``code``. All as stored."""
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
