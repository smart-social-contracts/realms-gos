"""The management canister's threshold Schnorr key, played by a peer that signs deterministically, and a verifier's walk
up a Merkle path: what the tests of core.signing and of the documents it signs share."""

import hashlib

import fake_chain
from core import signing
from core.candid import Reader


def fake_key(name, path=b"realms:journal"):
    """The public key the peer gives for key ``name`` and derivation ``path``."""
    return hashlib.sha256(b"fake key " + name.encode() + path).digest()


def fake_signature(message):
    return hashlib.sha512(b"fake signature " + message).digest()


def management(**replies):
    """The management canister as a peer: the public key and the signature depend on what the realm sends, as they
    would. ``replies`` replace its methods, to make a call fail."""
    def public_key(arg):
        asked = Reader(arg).args()[0]
        key = fake_key(asked["key_id"]["name"], *asked["derivation_path"])
        return f"(record {{ 3_110_171_755 = {signing.blob(bytes(32))}; 881_350_601 = {signing.blob(key)} }})"

    def sign(arg):
        message = Reader(arg).args()[0]["message"]
        return f"(record {{ 359_375_608 = {signing.blob(fake_signature(message))} }})"

    return fake_chain.peer("aaaaa-aa", schnorr_public_key=public_key, sign_with_schnorr=sign, **replies)


def climb(digest, path):
    """The root a document's path leads to, as a verifier works it out."""
    node = bytes.fromhex(digest)
    for side, sibling in path:
        sibling = bytes.fromhex(sibling)
        node = hashlib.sha256(b"\x01" + (node + sibling if side == "r" else sibling + node)).digest()
    return node.hex()
