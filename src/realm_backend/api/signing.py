"""The operations of the documents the realm's canister signs. The rules are in core.signing; the official journal's own
are in api.journal."""

from core import signing
from core.db import Fail, NotFound
from core.op import op


@op(None, read=True, public=True)
def signature_proof(c, args):
    """The proof that this realm's canister signed a document (docs/codices/journal-signature.md), for the ``kind`` and
    ``subject`` of the document (a journal issue's subject is its day) or for its verification ``code``. The proof is
    the ``canonical`` text that was signed, its ``digest``, the ``root`` of the batch it was signed in, its ``path`` to
    the root, the ``signature`` of the root and the ``code``; the key, the realm's canister id and the previous
    document's digest are in the text. Returns ``kind``, ``subject`` and ``proof``. A document made and not signed is
    served too, with an empty ``root``, ``path`` and ``signature``: its text and digest are what the next document's
    ``prev`` names, so that a chain can be checked back through days nobody asked to sign. A document not made is not
    found."""
    if args.get("code"):
        kind, subject = signing.find(args["code"])
    else:
        kind, subject = args.get("kind"), args.get("subject")
        if not isinstance(kind, str) or not isinstance(subject, str):
            raise Fail("give the kind and subject of the document, or its verification code")
        signing.path(kind)  # a kind that is no name is refused, and is not echoed
    proof = signing.proof(kind, subject)
    if proof is None:
        raise NotFound(f"no document {kind[:40]}:{subject[:40]}")
    return {"kind": kind, "subject": subject, "proof": proof}
