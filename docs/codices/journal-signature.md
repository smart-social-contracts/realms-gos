# Signed documents, and the signed official journal

Design of the signature by a realm's canister on its documents, with the official journal
([layered-codex.md §9.6](layered-codex.md#96-the-official-journal-extension-journal)) as the first user. The user's
requirement: *there should be a digital signature that proves that the journal was produced by the canister of such a
realm*, and other documents (certificates, decisions) will need the same.

Status: design for review, November 2026. The journal's part is built with it; no other kind yet.

## 1. What it gives a reader

A signed document carries a **proof** that anyone can check, on screen, from a printed or PDF copy, and offline, with one
standalone page ([§7](#7-verifying)). It proves that **the code running in this realm's canister** produced that exact
content, and, for a chained kind such as the journal, that no document was altered or dropped later without leaving
evidence. A document not yet signed is shown as *provisional*.

## 2. Decisions

| | Decision | Why |
|---|---|---|
| Facility | `core/signing.py`: one key per **kind**, one canonical form, one proof shape, optional chain, batches. `core/journal_signing.py` is the journal's use of it | The next kinds add a body and a trigger, not a signer |
| Signer | The IC's **threshold Schnorr**: management canister `sign_with_schnorr`, **Ed25519**, key derived for the realm's own principal | The IC binds the key to the canister id, not we: only the canister can ask for a signature under it. No key is stored |
| Call | `chain.call("aaaaa-aa", ...)` with Candid text, as `api/crypto.py` does for vetKD | No Basilisk binding. Spiked on PocketIC: the 64-byte reply verifies as plain RFC 8032 Ed25519 |
| Why not ECDSA | Same price, but secp256k1 needs a library to verify, and no browser supports it | WebCrypto verifies Ed25519 in current browsers |
| Key name | `dfx_test_key` when `Realm.network` is `local`; `test_key_1` on `test`, `staging`, `demo`; **`key_1`** otherwise | `test_key_1` lives on a 13-node subnet and is for testing. A document records its key, so a realm that changes network keeps old ones valid |
| Derivation path | `["realms:<kind>"]`, e.g. `realms:journal`, `realms:certificate`, fixed for ever | **Domain separation**: the journal's key cannot sign a certificate. A new kind is a new key, with no setup |
| What is signed | A 32-byte **root**: the Merkle root of the documents of one batch, which for a batch of one is the document's own digest | One verification rule for both; the signature costs the same for one document or a thousand |
| Canonical form | JSON, keys sorted, no spaces, integers only, every character above `~` written as `\uXXXX` (a pair above U+FFFF), as CPython's `ensure_ascii` does. The realm does the escaping itself: its own `json.dumps` writes non-ASCII as it is. Stored and served as it is | A verifier never re-serializes, so no language's JSON quirks matter; the text is the same under CPython and in the canister, which a test holds |
| When | A sweep every hour signs what has closed, oldest first, in as few batches as it can. A failure is retried at the next sweep | A signature needs an update (a query cannot call the management canister); a sweep also covers downtime and upgrades |
| Stored | One `SignedDocument` row per document: canonical text, digest, root, Merkle path, signature, code. Never recomputed | Threshold Ed25519 on mainnet is not promised to be deterministic (PocketIC's is): a second signature may differ |
| CLI | None. Verification is one standalone HTML page | The canister makes the signature; a reader needs only to check it |

## 3. The document and its proof

```
{"v":1,"kind":"journal","realm":"<canister id>","subject":"2026-11-10",
 "body":{"number":12,"timezone":"Europe/Madrid","entries":[{"id":"E31","at":1794000000,"kind":"codex_installed","details":{...}}]},
 "prev":"<hex digest of the previous document of the kind, or null>","signed_at":1794009601,
 "key":{"algorithm":"ed25519","name":"key_1","path":"realms:journal","public_key":"<hex>"}}
```

`kind` and `subject` name the document (a journal's subject is its day, a certificate's its id); `body` is the kind's own
content, as stored facts, never rendered text. `prev` links a **chained** kind to the one before it; other kinds leave it
null. The realm's name is not signed: the canister id is the identity, the name a label. The `timezone` of a journal issue is the
realm's zone when it was signed, not necessarily the one on its day.

The **proof** is what the realm serves: `canonical` (the text above), `digest` (SHA-256 of it), `root`, `path`, `signature`
and `code`. The kind, subject, `signed_at`, key and `prev` are *inside* the signed text and are not repeated beside it: a
field outside the text is one a verifier could be lied about. `signed_at` is the time of the signature, not of the day: a
document signed late says so.

**Verification code**: the first 80 bits of the digest in base32, `XXXX-XXXX-XXXX-XXXX`, printed on every document like a
Spanish CSV. It finds the document; the signature proves it.

## 4. Batches

A batch is the documents of one kind signed together. Their digests are the leaves of a Merkle tree: a node is
`SHA-256(0x01 ‖ left ‖ right)`, an odd node moves up unchanged, and a leaf is the digest itself (a document's text starts
with `{`, a node's input with `0x01`, so a leaf cannot pass for a node). One signature covers the root, and each document
keeps its **path**: the sibling digests from its leaf up, each marked left or right. Checking a document is: hash the
text, climb the path to the root, check the signature of the root. A batch of one has an empty path and root = digest.

The journal signs one document a day, so its batches are of one, except after downtime: the days that closed meanwhile
are one batch, one signature, at most 31 days to a batch (a batch is made in one message), so that 40 days stopped is two
signatures. A kind with documents on demand (certificates) queues them and signs the queue in the same
sweep: its cost per document goes to near zero, and a document waits at most an hour.

## 5. The journal, the first kind

- **Subject** a day (`YYYY-MM-DD`), **body** `number`, `timezone` and `entries`, **chained**.
- A day that has closed on the capital's clock is signed by the next sweep, **every day, with entries or not**: the chain is
  consecutive, so a gap in the numbers is a dropped issue. Late signing is allowed (a realm upgraded onto this release signs
  its closed days at once, oldest first).
- `journal_issue {day}` (public read, as now) gains `status`: `provisional` (the open day), `signed` or `unsigned` (closed,
  awaiting its signature, or failed), and, when signed, `proof`. `journal_issue {code}` finds an issue by its code.
- A quarter issues and signs nothing; its capital does.
- **A zone change west after a day is signed** would send the next entry into the signed day. An entry is stamped
  `max(local day, last signed day + 1)`, and `journal_issue` treats that day as the open one; the journal's first day does
  not move once a day is signed.

## 6. Reading

`signature_proof {kind, subject}` or `{code}` (public read) returns any kind's proof: the one call the verify page needs.
`journal_issue` adds the issue's entries as displayed, so a verifier can check them against the signed ones. The proof is
stored, so a second read returns the same signature.

## 7. Verifying

One page, `docs/verify/index.html`: a single file, no build, no framework, no external script. It takes the proof as pasted
JSON, an uploaded file, or fetched from a realm by canister id and code (or kind and subject), and shows **valid** or
**invalid** with the reason, the canister id, the kind, subject and number, and the chain link. It checks:

1. `SHA-256(canonical)` is `digest`; the path climbs from the digest to `root`; the signature of `root` verifies under the
   public key in the text (WebCrypto Ed25519, in current Chrome, Firefox and Safari, found out once with a known key).
   Every value of the proof must have the form it has when it is right (hex, a canister id, the key's path being
   `realms:<kind>`): none that has not is shown in a command.
2. The proof is **the one asked for** (its code, or its kind and subject, is the requested one: an answer to a query is not
   certified, and could be another genuine document), and the text names the canister it was fetched from. The page shows
   what was signed, to compare with a printed copy; the realm's own sheet (realms-extensions, journal) checks that the entries
   it shows are the signed ones.
3. `prev` is the digest of the previous document, fetched by its code (the code of `prev`).
4. **The key is the canister's.** See below.

**How a reader knows the key belongs to the canister.** The key is derived by the IC from its master key, the canister id and
the path; nobody else can ask for a signature under it. Establishing that the key in the text *is* that derived key needs the
master key, which a page cannot get from a canister: a query reply is not certified, and checking a certificate needs BLS
verification, a library of its own. So the page:
- fetches the proof with a **tiny inline query** (an anonymous `query` request over the IC HTTP API: about 150 lines of CBOR
  and Candid text, no agent-js). The reply needs no trust: the signature binds the content;
- shows the key, its name and path, and says plainly that it is **the key the realm reports** until checked;
- takes an **expected key** and compares. The reader types it: **a link cannot fill it in, nor choose the replica asked**
  (`?key=` and `?host=` are ignored), since a link from an attacker would otherwise bring its own key and its own replica. A
  replica other than the Internet Computer's public one is named wherever it was asked. The reader derives the key
  independently, offline, with DFINITY's
  [`@dfinity/ic-pub-key`](https://docs.internetcomputer.org/guides/chain-fusion/offline-key-derivation/)
  (`PublicKeyWithChainCode.forMainnetKey("key_1")`, `DerivationPath.withCanisterPrefix(canister, [path])`); the page prints
  the snippet filled in. Or by asking any canister for `schnorr_public_key` with `canister_id` set to the realm's (spiked: a
  canister may ask for another's key). A local replica's key (`dfx_test_key`) has no published master key: the page says so.

Deriving the key inside the page (HKDF and Edwards point addition on BigInt, with the master keys embedded) would make it fully
offline; it is not in this version, since the master keys must be checked against the registry first.

If the browser has no WebCrypto Ed25519 the page says so and prints the commands to check the signature with OpenSSL or
Python's `cryptography`; it carries no curve code of its own.

**Where to publish.** Not from a realm's own frontend: the signer must not serve its own verifier. A GitHub Pages site of the
organization, deployed by a workflow that publishes `docs/verify/` alone, plus the same file attached to each release so it
runs from disk. A sheet's QR code points to it with `?canister=…&code=…`.

## 8. Costs

| | Fee per signature | A realm, one a day, one year |
|---|---|---|
| `key_1` (34-node subnet), docs | 26,153,846,153 cycles (~$0.0357) | ~9.5T cycles, ~$13 |
| `test_key_1` (13-node subnet), docs | 10,000,000,000 cycles (~$0.0137) | ~3.7T cycles, ~$5 |
| PocketIC, measured | 26.1B cycles for all three key names: the 26.15B fee and about 30M of the realm's own work | |

1 XDR = 1 trillion cycles = $1.366 (docs, 22 May 2026). `schnorr_public_key` is free. A call with too few cycles attached is
rejected and refunded, and the error names the fee. The realm checks its balance first, and a realm short of cycles says what
it lacks in `signing_failures` and asks for nothing. It attaches its key's fee and half as much again (15B for `test_key_1`,
39.2B for the others) and gets the unused part back. The check is of the balance, not of what is spendable above the
freezing reserve: a call that would take the realm below the reserve still traps, and nothing is recorded of it. Batching makes the
signature cost the same for one document or many: on PocketIC a day's signature cost 26.1B cycles, and 40 days stopped cost
two. An idle realm there burns about 0.7B cycles a day, so the signature is the larger part of a realm's daily cost.
In instructions (PocketIC): reading a signed issue 10.5M, writing one issue's text and digest 19M, the tree of a month of
issues 480M, finding a proof by its code 2.4M, so a batch of 31 is about 1.1B, well under a message's limit. Storage: a few
KB a document. A federation signs only at its capital.

## 9. Key management

- **Upgrade**: stable memory and documents stay; the sweep starts again.
- **Reinstall** wipes the documents, not the key (it depends on the canister id and path only). Copies already out still
  verify. The journal restarts at issue 1, a second validly signed issue 1 for that canister: the signature cannot tell. That is
  a controller's act, visible in the canister's change history.
- **Subnet key resharing** keeps the public key. A key that is retired makes signing fail: documents stay `unsigned`, the
  failure is kept for the IT admin team (`codex_status`), signatures already made stay valid.
- **A change of key name** (the network setting) signs under the other key; each document carries its own.
- **A new canister id** is a new identity.

## 10. Threat model

**Proves:** the content is exactly what the canister signed at `signed_at`; it was signed by code running as this canister;
with two adjacent documents of a chained kind in hand, an alteration or a drop is caught (a fork shows as two documents with
one `prev`).

**Does not prove:** that the content is true or complete beyond what the realm recorded; that the canister ran its published
release (a controller can upgrade it to code that signs anything: compare the module hash and controllers on the IC); that a
later document does not exist (an old copy stays valid, and the newest can be withheld); that a printed text matches the signed
one unless it is checked; the realm's name; that a provisional document is final; that the key is the canister's, until the
reader checks it ([§7](#7-verifying)).

**Certified data and certified queries** add nothing to the authenticity of a document, which its signature carries. They would
authenticate the live reply (freshness, the head of the chain) against a node that withholds. Basilisk exposes
`set_certified_data`, so it can come later; not in this version.

## 11. Adding a kind

Pick a name (lowercase letters, digits, dashes). Build the body from stored facts and call `signing.sign_batch(kind,
documents, chained)` from a sweep: `documents` are `(subject, body)` in order. Read the proof with `signing.proof(kind,
subject)`. Nothing else: the key, the path, the code, the tree and `signature_proof` are the facility's.
