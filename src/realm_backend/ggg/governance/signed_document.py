"""The storage of the documents the realm's canister has signed (docs/codices/journal-signature.md). Data only: the
rules are in core.signing."""

from ic_python_db import String

from core.db import Json, Row


class SignedDocument(Row):
    """A document of any kind (the official journal's issues, so far) signed by the realm's canister. Stored once and
    never changed. ``canonical`` is the document as it was signed: its kind, subject, body, the previous document's
    digest and the key are in it, and nothing else about it is kept beside it."""

    __alias__ = "id"
    id = String()  # ``<kind>:<subject>``, such as ``journal:2026-11-09``
    code = String(default="", indexed=True)  # the verification code printed on the document
    canonical = String(default="")
    digest = String(default="")  # SHA-256 of canonical, hex: a leaf of the batch's tree; the next document links to it
    root = String(default="")  # the root of the batch's tree, hex: what the signature covers
    path = Json([])  # from the digest up to the root: [side, sibling digest] steps (core.signing.merkle)
    signature = String(default="")  # hex
