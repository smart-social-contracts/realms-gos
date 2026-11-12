# Verify a signed document

`index.html` is one self-contained page: no build, no framework, no script from elsewhere. It checks that a document was
signed by a realm's canister ([the design](../codices/journal-signature.md)): the official journal's issues, and any other
kind the canister signs.

Give it the proof in one of three ways: fetch it from a realm by canister id and verification code (or kind and subject),
paste the proof as JSON, or open a file. It shows valid or invalid with the reason, the canister id, the kind, subject and
signing time, what was signed, and the link to the previous document. It also prints, for the key in the document, the
snippet that derives the canister's key offline and the commands that check the signature with Python or OpenSSL.

A link can carry the request, and only that: `?canister=<id>&code=<code>` (or `&kind=journal&subject=2026-11-09`). It cannot
carry a key to trust or a replica to ask: the reader types the expected key, and may choose a replica under "Network", which
the page names whenever it is not `https://icp-api.io`. A sheet's QR code is such a link.

## What it checks

1. The signed text hashes to its digest, the verification code is the digest's, and the digest climbs its Merkle path to the
   root that was signed.
2. The root's signature verifies under the key in the text, with the browser's own WebCrypto Ed25519 (current Chrome, Firefox
   and Safari). The page has no curve code of its own: a browser without it says so and shows the commands for other tools.
3. The proof is the one asked for (its code, or its kind and subject, is the requested one), and the text names the canister
   it was fetched from.
4. `prev` is the document before it, fetched by its code; the whole chain back to the first can be walked. A realm that
   refuses the previous document has dropped it or hides it, and the page says so.
5. When an expected key is given, the key in the text is that key. **That is what ties a key to a canister**: derive the key
   yourself, offline, as the page shows, and give it. Without it the page says the key is only the one the document reports.

## Where it runs from

Not from a realm's own frontend: the signer must not serve its own verifier. The page is published on GitHub Pages at

> **https://smart-social-contracts.github.io/realms-gos/verify/**

which is the address on every journal sheet's QR code, so it must not change. `.github/workflows/verify-pages.yml` assembles a
site with this folder's `index.html` as `verify/index.html` and nothing else, and deploys it. It runs on pushes to `main` that
touch the page, and by hand (`workflow_dispatch`); a deployment to Pages comes from `main`, so the page goes out with the
release that merges `feat/layered-codex` into `main`. To turn it on, in the repository's **Settings → Pages → Build and
deployment → Source**, choose **GitHub Actions**. The same file can also be attached to each release, so that it runs from disk.
It asks a replica only to read a proof (an anonymous query).

## Tests

`tests/deployed/test_verify_page.py` runs the page's script under Node against proofs made with a real Ed25519 key and the
design's own rules, and checks its request to a realm against the IC's HTTP interface and Candid. The canister's own proofs
go through the page in `tests/deployed/test_journal_signing.py`.
