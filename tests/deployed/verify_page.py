"""The verify page (docs/verify/index.html) run under Node, and proofs made the way a verifier is told they are made, with
a real Ed25519 key and none of the realm's code: the reference the page and the canister are both held to.

``page(fn, *args)`` runs a function of the page's script; ``proofs(...)`` writes documents and signs them in batches."""

import base64
import hashlib
import json
import pathlib
import shutil
import subprocess

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

HERE = pathlib.Path(__file__).resolve().parent
PAGE = HERE.parents[1] / "docs" / "verify" / "index.html"
RUNNER = HERE / "verify_page.cjs"
NODE = shutil.which("node")


def hexed(value):
    """Bytes in the runner's notation, and any list or dict of them."""
    if isinstance(value, (bytes, bytearray)):
        return {"$hex": bytes(value).hex()}
    if isinstance(value, list):
        return [hexed(item) for item in value]
    if isinstance(value, dict):
        return {key: hexed(item) for key, item in value.items()}
    return value


def unhexed(value):
    if isinstance(value, list):
        return [unhexed(item) for item in value]
    if isinstance(value, dict):
        return bytes.fromhex(value["$hex"]) if set(value) == {"$hex"} else {k: unhexed(v) for k, v in value.items()}
    return value


def page(fn, *args, fetch=None):
    """What ``fn`` of the page returns for ``args``, or raises its error; with ``fetch`` (``{"status", "body": bytes}``)
    its requests are answered by that reply, and ``(result, request it made)`` is returned."""
    request = {"fn": fn, "args": hexed(list(args))}
    if fetch:
        request["fetch"] = {"status": fetch["status"], "body": fetch["body"].hex()}
    out = subprocess.run([NODE, str(RUNNER), str(PAGE), json.dumps(request)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    reply = json.loads(out.stdout)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return (unhexed(reply["result"]), reply["request"]) if fetch else unhexed(reply["result"])


# ---- Proofs, made from the text of the design (docs/codices/journal-signature.md)


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def node(left, right):
    return hashlib.sha256(b"\x01" + left + right).digest()


def tree(leaves):
    """The root and each leaf's path, worked out leaf by leaf: at each level a node's sibling is its neighbour, if it has
    one, and the level above holds the hashes of the pairs, an odd one out moving up."""
    paths = []
    for position in range(len(leaves)):
        nodes, at, path = list(leaves), position, []
        while len(nodes) > 1:
            if at % 2:
                path.append(["l", nodes[at - 1].hex()])
            elif at + 1 < len(nodes):
                path.append(["r", nodes[at + 1].hex()])
            nodes = [node(nodes[i], nodes[i + 1]) for i in range(0, len(nodes) - 1, 2)] + (nodes[-1:] if len(nodes) % 2 else [])
            at //= 2
        paths.append(path)
    nodes = list(leaves)
    while len(nodes) > 1:
        nodes = [node(nodes[i], nodes[i + 1]) for i in range(0, len(nodes) - 1, 2)] + (nodes[-1:] if len(nodes) % 2 else [])
    return nodes[0], paths


def code(digest):
    text = base64.b32encode(bytes.fromhex(digest)[:10]).decode()
    return "-".join(text[i:i + 4] for i in range(0, 16, 4))


class Signer:
    """A key standing for the canister's, signing batches of one kind as the realm does."""

    def __init__(self, realm="aaaaa-aa", kind="journal", key_name="key_1", private=None):
        self.private = private or Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.realm, self.kind, self.key_name, self.head = realm, kind, key_name, None

    def sign_text(self, text):
        """A proof of one document written as ``text``, whatever it says: for a text a right signer would never write."""
        digest = hashlib.sha256(text.encode()).digest()
        return {"canonical": text, "digest": digest.hex(), "root": digest.hex(), "path": [],
                "signature": self.private.sign(digest).hex(), "code": code(digest.hex())}

    def sign(self, batch, chained=True, signed_at=1_790_000_000):
        """``batch``: ``[(subject, body)]``. The proofs, in order."""
        texts, prev = [], self.head if chained else None
        for subject, body in batch:
            text = canonical({"v": 1, "kind": self.kind, "realm": self.realm, "subject": subject, "body": body, "prev": prev,
                              "signed_at": signed_at, "key": {"algorithm": "ed25519", "name": self.key_name,
                                                              "path": "realms:" + self.kind, "public_key": self.public.hex()}})
            texts.append(text)
            prev = hashlib.sha256(text.encode()).hexdigest() if chained else None
        digests = [hashlib.sha256(text.encode()).digest() for text in texts]
        root, paths = tree(digests)
        signature = self.private.sign(root)
        if chained:
            self.head = digests[-1].hex()
        return [{"canonical": text, "digest": digest.hex(), "root": root.hex(), "path": path, "signature": signature.hex(),
                 "code": code(digest.hex())} for text, digest, path in zip(texts, digests, paths)]


def climb(digest, path):
    """The root a path leads to, as the design tells a verifier to work it out."""
    current = bytes.fromhex(digest)
    for side, sibling in path:
        sibling = bytes.fromhex(sibling)
        current = node(current, sibling) if side == "r" else node(sibling, current)
    return current.hex()


def reference_valid(proof):
    """Whether a proof is right, checked in Python the way the design says: the text hashes to its digest, the path leads
    to the root, and the root's signature verifies under the key in the text."""
    document = json.loads(proof["canonical"])
    if hashlib.sha256(proof["canonical"].encode()).hexdigest() != proof["digest"]:
        return False
    if climb(proof["digest"], proof["path"]) != proof["root"] or code(proof["digest"]) != proof["code"]:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(document["key"]["public_key"])).verify(
            bytes.fromhex(proof["signature"]), bytes.fromhex(proof["root"]))
    except InvalidSignature:
        return False
    return True


def issue_body(number, entries=()):
    return {"number": number, "timezone": "UTC", "entries": list(entries)}
