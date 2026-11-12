# Signed documents, and the signed official journal

Design of the signature by a realm's canister on its documents, with the official journal
([layered-codex.md §9.6](layered-codex.md#96-the-official-journal-extension-journal)) as the first user. The user's
requirement: *there should be a digital signature that proves that the journal was produced by the canister of such a
realm*, and other documents (certificates, decisions) will need the same.

Status: design for review, November 2026. The journal's part is built with it; no other kind yet. Changed on 12 Nov 2026
(user decision): a day is **made** when it closes and **signed when someone asks**, not by default (§5).

## 1. What it gives a reader

A signed document carries a **proof** that anyone can check, on screen, from a printed or PDF copy, and offline, with one
standalone page ([§7](#7-verifying)). It proves that **the code running in this realm's canister** produced that exact
content, and, for a chained kind such as the journal, that no document was altered or dropped later without leaving
evidence. A document not yet signed is shown as *provisional* (today's issue) or *not signed yet* (a closed day nobody has
asked to sign). A signature on one document also covers every earlier one of its kind: each names the digest of the one
before it, signed or not.

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
| Making and signing | A document is **made** (text, digest, code) as soon as it exists, for SHA-256 and nothing else; its **signature** is a later step, on request. The text is fixed when it is made and never changes, so the digest the next document names is the digest that is signed | A signature costs 26B cycles on `key_1`; making a document costs about nothing, and the chain of digests lets one signature stand for all before it |
| Canonical form | JSON, keys sorted, no spaces, integers only, every character above `~` written as `\uXXXX` (a pair above U+FFFF), as CPython's `ensure_ascii` does. The realm does the escaping itself: its own `json.dumps` writes non-ASCII as it is. Stored and served as it is | A verifier never re-serializes, so no language's JSON quirks matter; the text is the same under CPython and in the canister, which a test holds |
| When it is made | A sweep every hour makes what has closed into documents, oldest first, in batches of a month. A failure (the public key could not be had) is retried at the next sweep | A sweep also covers downtime and upgrades |
| When it is signed | **When anyone asks** (`journal_sign`, anonymous callers too), at most once for a document, ever; or, in a realm set to `daily`, by the sweep that makes it | The user's decision: no signature by default. A signature needs an update (a query cannot call the management canister) |
| Stored | One `SignedDocument` row per document: canonical text, digest and code when it is made; root, Merkle path and signature added when it is signed, once. Never recomputed | Threshold Ed25519 on mainnet is not promised to be deterministic (PocketIC's is): a second signature may differ |
| CLI | None. Verification is one standalone HTML page | The canister makes the signature; a reader needs only to check it |

## 3. The document and its proof

```
{"v":1,"kind":"journal","realm":"<canister id>","subject":"2026-11-10",
 "body":{"number":12,"timezone":"Europe/Madrid","entries":[{"id":"E31","at":1794000000,"kind":"codex_installed","details":{...}}]},
 "prev":"<hex digest of the previous document of the kind, or null>","made_at":1794009601,
 "key":{"algorithm":"ed25519","name":"key_1","path":"realms:journal","public_key":"<hex>"}}
```

`kind` and `subject` name the document (a journal's subject is its day, a certificate's its id); `body` is the kind's own
content, as stored facts, never rendered text. `prev` links a **chained** kind to the one before it, **whether or not that
one is signed**; other kinds leave it null. The realm's name is not signed: the canister id is the identity, the name a label. The `timezone` of a journal issue is the
realm's zone when it was made, not necessarily the one on its day.

The **proof** is what the realm serves: `canonical` (the text above), `digest` (SHA-256 of it), `root`, `path`, `signature`
and `code`. The kind, subject, `made_at`, key and `prev` are *inside* the signed text and are not repeated beside it: a
field outside the text is one a verifier could be lied about. `made_at` is the time the realm made the document, not the
day it is of: a day made late (after downtime, or an upgrade) says so. **The signature attests the text as it was made, at
`made_at`**, whenever it is asked for: the time of a signature requested later is in no text and is not served. The documents
the release before this one signed as it made them say `signed_at` where a new one says `made_at`: the same moment then, and
they are unchanged. The verify page reads either and labels it "Signed at" or "Made at".

**Verification code**: the first 80 bits of the digest in base32, `XXXX-XXXX-XXXX-XXXX`, printed on every document like a
Spanish CSV. It finds the document; the signature proves it.

## 4. Batches

A batch is the documents of one kind signed together. Their digests are the leaves of a Merkle tree: a node is
`SHA-256(0x01 ‖ left ‖ right)`, an odd node moves up unchanged, and a leaf is the digest itself (a document's text starts
with `{`, a node's input with `0x01`, so a leaf cannot pass for a node). One signature covers the root, and each document
keeps its **path**: the sibling digests from its leaf up, each marked left or right. Checking a document is: hash the
text, climb the path to the root, check the signature of the root. A batch of one has an empty path and root = digest.

A signature on request is a batch of one. A realm set to sign daily signs the days that are made and come after the last
day signed as batches of at most 31 days (a batch is made in one message), so that 40 days stopped, or a backlog of a year,
is two signatures, or twelve. Two requests that land together are not batched, and none waits for another: a request signs
at once.

## 5. The journal, the first kind

- **Subject** a day (`YYYY-MM-DD`), **body** `number`, `timezone` and `entries`, **chained**.
- **Making.** A day that has closed on the capital's clock is made into a document by the next sweep, **every day, with
  entries or not**: the chain is consecutive, so a gap in the numbers is a dropped issue. Its `prev` is the digest of the
  day before, signed or not. It costs SHA-256 and a call for the public key that costs no cycles (a realm that cannot make a
  day keeps the failure in `signing_failures` and tries again at the next sweep). A realm upgraded onto this release makes
  its closed days at once, oldest first.
- **Signing, on request.** `journal_sign {day}` (public update, anyone, an anonymous caller too) signs the day now and
  returns its proof (`day`, `status` `signed`, `proof`). It refuses a day not closed yet (`conflict`), one before the
  journal's first day (`not_found`), a date that is no date (`invalid_argument`) and a realm that issues no journal (a
  quarter: `conflict`). A day **already signed** returns its proof at once and signs nothing; a day **being signed** by
  another call is `pending` (an in-flight mark, in the heap, so no second `sign_with_schnorr` for it; a mark older than
  ten minutes, left by a call that trapped, no longer counts). A day the sweep has not made yet (it closed within the
  hour) is made first, with the days before it. A signature that fails is kept in `signing_failures` (the reason, with the
  realm's balance, for the IT admin team); the caller gets `unavailable` and only "could not be made now: ask again
  later", and the day stays as it was: it may be asked again. A day made for **another key** than the realm's now (the
  network or the key rule changed since) cannot be signed while the realm's key is another: a request for it is a
  `conflict` that names both keys, and one asks for a later day instead, whose signature covers it through the chain.
  **Cost bound: at most one signature is *stored* per closed day, ever, so the most a realm can spend on signatures is
  what signing every day would cost.** The fee is paid for a signature the realm asked for, not for one it stored: an
  upgrade while `sign_with_schnorr` is out traps the callback and stores nothing, and the next request pays the fee again.
- **No call that changes nothing writes.** An idempotent request, a `pending` one and a refusal save no row, not even the
  realm's: the cursor and the failure record are written only when a signature is made or fails, or a daily sweep passes over
  days it cannot sign.
- **Audit and legislation.** `journal_sign` is registered with `only_reads`, which is the flag that makes the operation run no
  legislation: no chapter's guard, forbid or hook runs for it, so a codex can neither refuse nor hook it (it is protocol, not
  policy). It would also leave no audit event, so the operation writes one itself, only when it makes a signature (at most
  one a day): idempotent replies, `pending` and refusals write none, and anonymous calls cannot fill the log's ring.
- **The setting** `Realm.journal_signing`: `on_request` (the default, also for a realm that was upgraded from the release
  that signed daily) or `daily`. It is set like the realm's other settings (`setup_step` while the realm is in setup,
  `realm_configure` by a vote after the launch; `realm_info` shows it) and changes nothing already made or signed. In `daily`
  the sweep signs **every day that is made and comes after the last day signed**, oldest first, in batches of 31 (one
  signature each), until none is left or a batch fails. It works this out from the documents and from the last signed day
  (`signing` state `signed`, moved forward by every signature, a request's too), never from the record of failures, which is
  for the IT admin team to read: a batch that fails (the realm is short of cycles, a call is refused) leaves its days where
  they were, and the next sweep finds them again. A day before the last signed one is covered by it through the chain, is not
  signed by the sweep, and may still be asked for. A day the sweep cannot sign (made for another key than the realm's now, or
  its document gone) is left out of its batch and passed over, so it never holds back the days after it; a batch left empty
  only because a request is signing one of its days is not passed over, so that the next sweep finds that day again if the
  request fails. **Switching from `on_request` to `daily` therefore signs everything made after the last signed day, once: a
  year without a signature is about 12 signatures (about 0.3 T cycles), and every day then has a proof of its own**; from
  there it is one signature a day. A realm upgraded from the release that signed every day starts
  from its last signed day (its old head); a new realm starts from none.
- `journal_issue {day}` (public read, as now) gives `status`: `provisional` (the open day), `signed` or `unsigned` (closed, with
  no signature), and, when signed, `proof`. `journal_issue {code}` finds an issue by its code, signed or not.
- A quarter issues, makes and signs nothing; its capital does.
- **A zone change west after a day is made** would send the next entry into a day already made. An entry is stamped
  `max(local day, last made day + 1)`, and `journal_issue` treats that day as the open one; the journal's first day does
  not move once a day is made.
- **From the release that signed every day.** Its documents (`signed_at`, each linked to the previous *signed* document) are
  kept as they are, text, digest and proof byte for byte, and keep verifying: nothing writes to them. Its state's `head`, the
  last document, is now read as the last document *made*, which is the last signed one: so the first new day, the one after
  it, has `prev` = that document's digest, and from there each day names the one before it. The chain of old digests and the
  chain of new ones are one chain. The realm is `on_request` after the upgrade, since the setting has no stored value.

## 6. Reading

`signature_proof {kind, subject}` or `{code}` (public read) returns any kind's document: the one call the verify page needs.
**A document that is made and not signed is served too**, with an empty `root`, `path` and `signature`: its text and digest
are what the next document's `prev` names, so a chain can be checked back through days nobody asked to sign. A document that
is not made is not found. `journal_issue` adds the issue's entries as displayed, so a verifier can check them against the
signed ones. The proof is stored, so a second read returns the same signature.

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
3. `prev` is the digest of the previous document, fetched by its code (the code of `prev`). That document is **often only
   made, not signed** (a realm signs a day when asked): the realm serves it all the same, and the page checks that its text
   hashes to `prev` and says it is not signed. A realm that does not serve it has dropped it or hides it, and the page says so.
   "Check the whole chain" walks back to the first document the same way: a signed one is verified in full, one only made by
   its link (same realm, same kind, its text hashing to the digest named). A signature therefore also covers every earlier
   document, and the page says so when `prev` is present.
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

**Where it is published.** Not from a realm's own frontend: the signer must not serve its own verifier. On GitHub Pages of
the organization, at `https://smart-social-contracts.github.io/realms-gos/verify/`, deployed by
`.github/workflows/verify-pages.yml` from `main` with the page alone in the site; the same file can be attached to each
release so it runs from disk. A sheet's QR code points to it with `?canister=…&code=…`, so the address must not change.

## 8. Costs

| | Fee per signature | A realm that signs every day, one year |
|---|---|---|
| `key_1` (34-node subnet), docs | 26,153,846,153 cycles (~$0.0357) | ~9.5T cycles, ~$13 |
| `test_key_1` (13-node subnet), docs | 10,000,000,000 cycles (~$0.0137) | ~3.7T cycles, ~$5 |
| PocketIC, measured | 26.1B cycles for all three key names: the 26.15B fee and about 30M of the realm's own work | |

**On request** a realm pays that fee only for the days someone asks to sign, **for at most one stored signature a day, ever**
(plus a second fee for a signature lost to an upgrade while it was out): the most it can spend is what signing every day
costs (above), and a realm nobody asks anything of spends nothing on signatures. What it
does every day regardless is make the day's document: SHA-256 of the text, a row, and one call for the public key, which costs
no cycles (instructions and cycles measured on PocketIC are in the pull request and in `tests/deployed/test_journal_signing.py`).
The hourly sweep, when no day has closed, is the closed-days check alone.

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

- **Upgrade**: stable memory and documents stay, signed or not; the sweep starts again. A realm upgraded from the release
  that signed every day keeps its signed documents byte for byte and the first new day links to the last of them
  ([§5](#5-the-journal-the-first-kind)).
- **Reinstall** wipes the documents, not the key (it depends on the canister id and path only). Copies already out still
  verify. The journal restarts at issue 1, a second validly signed issue 1 for that canister: the signature cannot tell. That is
  a controller's act, visible in the canister's change history.
- **Subnet key resharing** keeps the public key. A key that is retired makes signing fail: documents stay `unsigned`, the
  failure is kept for the IT admin team (`codex_status`), signatures already made stay valid.
- **A change of key name** (the network setting) makes new documents under the other key, each carrying its own. A document
  made under one key is not signed with another: the realm refuses, since the signature would not verify.
- **A new canister id** is a new identity.

## 10. Threat model

**Proves:** the content is exactly what the canister made at `made_at` and signed (whenever it was asked to); it was signed by code running as this canister;
with two adjacent documents of a chained kind in hand, an alteration or a drop is caught (a fork shows as two documents with
one `prev`).

**Does not prove:** that the content is true or complete beyond what the realm recorded; that the canister ran its published
release (a controller can upgrade it to code that signs anything: compare the module hash and controllers on the IC); that a
later document does not exist (an old copy stays valid, and the newest can be withheld); when the signature was made (the
text says when the document was made, not when it was signed); that a day nobody signed is unaltered unless a later signed
day names it (the newest days stay unsigned until someone asks); that a printed text matches the signed one unless it is
checked; the realm's name; that a provisional document is final; that the key is the canister's, until the
reader checks it ([§7](#7-verifying)).

**Certified data and certified queries** add nothing to the authenticity of a document, which its signature carries. They would
authenticate the live reply (freshness, the head of the chain) against a node that withholds. Basilisk exposes
`set_certified_data`, so it can come later; not in this version.

## 11. Adding a kind

Pick a name (lowercase letters, digits, dashes). Make the documents with `signing.make(kind, chained, subjects_of, body_of)`
(`subjects_of()` names the subjects to make, in order, and `body_of(subject)` builds each body from stored facts), from a
sweep or when the document is issued. Sign them when it is due with `signing.sign_batch(kind, subjects)`, and for a signature
anyone may ask for `signing.request(kind, subject)`, which signs one made document at once, once, and says `pending` while
another call is signing it. Read the proof with `signing.proof(kind, subject)`. Nothing else: the key, the path, the code,
the tree and `signature_proof` are the facility's. Only the journal's operation, `journal_sign`, is exposed.
