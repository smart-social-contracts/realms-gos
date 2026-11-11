# The signed official journal

Design of the signature on every closed issue of a realm's official journal
([layered-codex.md §9.6](layered-codex.md#96-the-official-journal-extension-journal)). The user's requirement:
*there should be a digital signature that proves that the journal was produced by the canister of such a realm.*

Status: design for review, November 2026. Nothing here is built yet.

## 1. What it gives a reader

A closed issue carries a **signature** that anyone can check, on screen, from a printed or PDF copy, and offline. It
proves that **the code running in this realm's canister** produced that exact content, and, through a **hash chain**,
that no issue was altered or dropped later without leaving evidence. Today's issue is shown as *provisional, not yet
signed*.

## 2. Decisions

| | Decision | Why |
|---|---|---|
| Signer | The IC's **threshold Schnorr**: management canister `sign_with_schnorr`, **Ed25519**, key derived for the realm's own principal | The key is bound to the canister id by the IC, not by us: only the canister can ask for a signature under it. No key is stored |
| Call | `chain.call("aaaaa-aa", ...)` with Candid text, as `api/crypto.py` does for vetKD | No Basilisk binding needed. Spiked on PocketIC: the 64-byte reply verifies as plain RFC 8032 Ed25519 (`cryptography`, `openssl`, libsodium, WebCrypto) |
| Why not ECDSA | Same price and API, but secp256k1 verifies only through a library, needs a pre-hash, and no browser supports it | Ed25519 is the one a printed copy's verifier can check with the tool at hand |
| Key name | `dfx_test_key` when `Realm.network` is `local`; `test_key_1` on the other test networks (`quarters.TEST_NETWORKS`: test, staging, demo); **`key_1` on production** | `test_key_1` lives on a 13-node subnet and is for testing. Each issue records its key, so a realm that changes network keeps its old issues valid |
| Derivation path | `["realms:journal"]`, fixed for ever | Domain separation: this key signs journal digests and nothing else. The path is in each issue |
| What is signed | `SHA-256(canonical)`, 32 bytes | A fixed-size message: no size limit to meet (the IC docs state none), and the same digest is the chain link and the source of the code |
| Canonical form | UTF-8 JSON, keys sorted, no spaces, ASCII escapes, integers only | Stored with the issue and served as it is: a verifier never re-serializes, so no language's JSON quirks matter |
| When | A timer at the end of the realm's local day signs every closed, unsigned day in order. Retried hourly on failure; re-set in `post_upgrade` like `sync_checks.resume` | An update is needed (a query cannot call the management canister), so "lazily on first read" is out |
| Stored | One row per closed issue: canonical text, digest, signature, code, time. Never recomputed | Threshold Ed25519 on mainnet is not promised to be deterministic (the docs are silent; PocketIC's is): a second signature may differ |

### Open decision: every day, or only days with entries

Signing **every** closed day (recommended) keeps the chain trivial: issue *n* links to issue *n-1*, a gap in the numbers is
a dropped issue. It costs one signature a day (§6). Signing **only days with entries** makes an empty day cost nothing:
the next signed issue says which issue precedes it, so the days between are empty by its signature. It is cheaper for
a realm with few news, a little harder to explain.

## 3. What is signed

```
{"v":1,"realm":"<canister id>","number":12,"day":"2026-11-10","timezone":"Europe/Madrid",
 "entries":[{"id":"E31","at":1794000000,"kind":"codex_installed","details":{...}}, ...],
 "previous":"<hex SHA-256 of issue 11's canonical text, null for issue 1>",
 "signed_at":1794009601,
 "key":{"algorithm":"ed25519","name":"key_1","path":"realms:journal","public_key":"<hex>"}}
```

- The entries are the stored facts (`id`, `at`, `kind`, `details`), never rendered text.
- The realm's **name is not signed**: the canister id is the identity, the name a label the realm may change.
- `signed_at` is the time of the signature, not of the day. An issue signed late (a realm upgraded onto this release,
  or a failed call retried) says so.

## 4. How it is read

- `journal_issue {day}` (public read, as now) gains `status` (`provisional` for today, `signed`, or `unsigned` for a
  closed day awaiting its signature) and, when signed, `proof`: `canonical` (the signed text), `digest`, `signature`,
  `public_key`, `key_id`, `algorithm`, `derivation_path`, `previous`, `code`, `signed_at`. The existing fields stay.
- `journal_issue {code}` finds the issue by its verification code.
- **Verification code**: the first 80 bits of the digest in base32, as `XXXX-XXXX-XXXX-XXXX`, printed on every issue
  like a Spanish CSV. It is a lookup key, not the proof: the page shows the issue the canister holds under it, next to
  what is printed.

## 5. Verifying

1. **Intact.** `SHA-256(canonical)` is `digest`; the signature verifies under `public_key`; `canonical` names this canister,
   this day and number; the entries shown equal the signed ones; `previous` equals the digest of the previous issue
   (`--chain` walks back to issue 1).
2. **From this canister.** The key is the one the IC derives for that canister, which a verifier establishes without
   trusting the realm: offline, with DFINITY's `@dfinity/ic-pub-key` (`forMainnetKey("key_1")` and the path
   `canister id + "realms:journal"`), or by asking any canister to call `schnorr_public_key` with `canister_id` set to the
   realm (spiked: a canister may ask for another's key). `realms journal verify` prints the exact command and takes
   `--expect-key <hex>` to check it. Without it the CLI says only *the key is what the realm reports*.
3. **On screen, print and offline.** The verify page (QR on the sheet, in realms-extensions) runs step 1 in the browser with
   WebCrypto. A sheet carries the code, canister id, digest, signature and key, and says which of its text is signed.
   For offline checking the page offers the **signed copy** (the `proof` as a `.json` file): `realms journal verify --file`
   needs no network.

CLI: `realms journal verify <canister> <day>` (`--file`, `--chain`, `--expect-key`). New dependency: `cryptography`, for
Ed25519 verification. A verifier that decides whether a signature is genuine should not rest on hand-written curve code.

## 6. Costs

| | Fee per signature | Per realm per year, one a day |
|---|---|---|
| `key_1` (34-node subnet), docs | 26,153,846,153 cycles (~$0.0357) | ~9.5T cycles, ~$13 |
| `test_key_1` (13-node subnet), docs | 10,000,000,000 cycles (~$0.0137) | ~3.7T cycles, ~$5 |
| PocketIC, measured | 26.18B cycles for all three key names (26.15B fee + ~30M of the realm's own work) | |

1 XDR = 1 trillion cycles = $1.366 (docs, 22 May 2026). `schnorr_public_key` is free. A call with too few cycles attached is
rejected and refunded, and the error names the fee. The realm attaches 60B and gets the unused part back, so a larger subnet
does not break it. Storage is one row of a few KB a day. A federation signs only at its capital.

## 7. Key management

- **Upgrade**: stable memory and issues stay; the timer is set again.
- **Reinstall** wipes the issues, not the key (it depends on the canister id and path only). Copies already out still verify.
  The new journal restarts at issue 1, a second validly signed issue 1 for that canister: the signature cannot tell. That is
  a controller's act, visible in the canister's change history, and governed by whoever holds the controllers.
- **Subnet key resharing** keeps the public key. A key that is retired makes signing fail: issues stay `unsigned`, the failure
  is kept for the IT admin team, and signatures already made stay valid.
- **A change of key name** (network setting) starts signing under the other key; each issue carries its own.
- **A new canister id** is a new identity.

## 8. Threat model

**Proves:** the content is exactly what the canister signed at `signed_at`; it was signed by code running as this canister;
with two adjacent issues in hand, an altered or dropped issue is caught (a fork shows as two issues with one `previous`).

**Does not prove:** that the content is true or complete beyond what the realm recorded; that the canister ran its published
release (a controller can upgrade it to code that signs anything: compare the module hash and controllers on the IC); that
a later issue does not exist (an old copy stays valid, and the newest issues can be withheld); that a printed text matches
the signed one unless it is checked; the realm's name; that a provisional issue is final.

**Certified data and certified queries** add nothing to the authenticity of an issue, which its signature already carries.
They would only authenticate the live reply (freshness, the head of the chain) against a node that withholds. Basilisk
exposes `set_certified_data`, so it can come later; not in this version.

## 9. Edge cases

- **A zone change west after a day is signed** would send the next entry into the signed day. An entry is stamped
  `max(local day, last signed day + 1)`, and `journal_issue` treats that day as today.
- **A failed signature** leaves the day `unsigned`, retried hourly; nothing else in the realm waits for it.
- **Concurrent closes** keep the first signature stored; a second reply is dropped.
- **A quarter** issues nothing and signs nothing; its capital does.
