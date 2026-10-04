# Federation portal — single login origin, sandboxed realm frontends

**Status:** Accepted (consolidated implementation decisions)  
**Related:** [#232](https://github.com/smart-social-contracts/realms-gos/issues/232) (tenant URLs), [#233](https://github.com/smart-social-contracts/realms-gos/issues/233) (cross-realm identity, interim staging only)  
**See also:** [FEDERATION_PORTAL_REALM_BRIDGE.md](./FEDERATION_PORTAL_REALM_BRIDGE.md) (iframe `postMessage` contract)

### Domain (v1 — no new purchase required)

Federation URLs use **existing `realmsgos.org` env hostnames** already wired for
registry / IC custom domains. **`gos.world` is deferred** — same architecture,
hostname swap only when/if the Foundation registers it later.

## Problem

Internet Identity assigns **one principal per browser origin**. Realms deploy **separate frontend asset canisters** (separate origins). Requirements:

- **One human → one principal** across registry and every realm (non‑negotiable).
- **Unlimited realms** (thousands), each with its own frontend canister.
- **No trusted off-chain router** — slug → canister resolution must not be a hackable CDN/nginx step.
- **No scaling cliff** — `/.well-known/ii-alternative-origins` allows at most **10 explicit URLs**, no wildcards ([II spec](https://github.com/dfinity/internet-identity/blob/main/docs/ii-spec.mdx)).
- **Session isolation** — a realm's frontend JS must not be able to read the portal session or act as the user against arbitrary canisters.

The #233 approach (registry as `derivationOrigin` + grow the alternative-origins list) works for a handful of staging canisters but **does not scale** and encourages manual per-realm patches. It is **legacy/staging-only** until the portal ships.

---

## 1. Portal & login architecture

- **One portal canister per environment** is the **only** Internet Identity login origin.
- Realm frontends are **not** independent login surfaces.
- Because there is only ever one login origin, `derivationOrigin` and
  `/.well-known/ii-alternative-origins` are **not needed** — this is not a
  workaround for the II 10-origin cap; it makes the cap **inapplicable**.

| Environment | Portal origin (v1) | Future (optional) |
|---|---|---|
| Production | `https://realmsgos.org` | `https://gos.world` |
| Staging | `https://staging.realmsgos.org` | `https://staging.gos.world` |
| Demo | `https://demo.realmsgos.org` | `https://demo.gos.world` |
| Test | `https://test.realmsgos.org` | `https://test.gos.world` |

Each environment is a **separate II login origin** → separate principal per environment (expected: staging ≠ production).

Within one environment, realms are reached by **path**, not subdomain:

```text
https://{env-host}/r/{slug}/…
# e.g. https://staging.realmsgos.org/r/agora
```

Each realm **keeps its own frontend asset canister** (branding, extensions, deploy lifecycle). That canister is a **UI backend**, not an auth origin.

---

## 2. Slug resolution — tamper-proof (`@update`)

On session entry the portal resolves `{slug → frontend_canister_id, backend_canister_id, …}` by calling the **registry canister**.

**Do not use a plain query call for this.** A query is answered by a single replica; a malicious or misbehaving boundary node could lie about the mapping and the “on-chain, verifiable” story breaks at the step that matters.

**Use an `@update` method** (consensus-backed) for v1 — once per session, latency acceptable. Certified query + client-side certificate verification is a valid **future** optimization if resolution latency becomes a measured problem; not needed now.

| Mechanism | Tamper resistance | Latency | Recommendation |
|---|---|---|---|
| Plain query | ✗ single replica | Low | **Reject** for slug resolution |
| Update call | ✓ consensus | ~1 round | **Default** — once per session |
| Certified query | ✓ if verified | Low | Future optimization only |

Suggested API:

```text
registry.resolve_slug(slug: text) -> update -> text
// JSON: { frontend_id, backend_id, gos_implementation, loader_profile, … }
```

Portal caches the result in memory for the session. User-visible **canister IDs** remain on screen; the portal rejects loads if resolved IDs do not match certified assets.

---

## 3. Join / onboarding flow

```text
1. Entry:    https://{env-host}/r/{slug}/join
2. Redirect: https://{env-host}/join?returnTo=/r/{slug}/join
             → user clicks "Sign in with Internet Identity"
3. Return:   https://{env-host}/r/{slug}/join
             → already authenticated, continue stepper
             (Quarter → Terms → Profile → Welcome)
4. If already signed in: skip step 2, go straight into the stepper.
```

Pass the return path explicitly (`?returnTo=...`) so post-login redirect lands on the correct realm, not a generic home page.

---

## 4. Pretty URLs — async, redirect-only

Pretty hostnames (e.g. `agora.staging.realmsgos.org`) are **302 redirects at DNS/CDN**, never a second app origin and never a login surface.

- **Canonical URL** (`https://staging.realmsgos.org/r/{slug}`) is live **immediately** on `claim_slug` — no DNS wait.
- Pretty URL becomes active asynchronously once the off-chain worker provisions the redirect.

### Off-chain worker (`domain-bridge`)

Extend `realms-management-service`, or add a dedicated `domain-bridge` service (isolate Cloudflare credentials either way). The worker is a **dumb renderer**:

1. Poll the registry for `pretty_hostname_status = pending`
2. Call the Cloudflare API to create the redirect rule:
   ```text
   agora.staging.realmsgos.org/* → 302 → https://staging.realmsgos.org/r/agora${uri}${query_string}
   ```
3. Mark status `live` (or `failed` on error)

The worker has **no authority** to invent mappings. The registry is the source of truth for the redirect *target*; the worker only executes DNS/CDN side effects for slugs that already exist on-chain.

### Registry fields

- `pretty_hostname` — e.g. `agora.staging.realmsgos.org`
- `pretty_hostname_status` — `pending` | `live` | `failed`

### UI

- While `pending`: show the canonical `{env-host}/r/{slug}` URL as the working share link.
- Show the pretty URL only once `live`.

### Never

- Point a pretty hostname at the realm frontend canister directly.
- Use a pretty hostname as `derivationOrigin` or add it to `ii-alternative-origins`.

---

## 5. Multi-implementation support (GGG interoperability)

The registry and portal are **GOS-neutral** — Realms GOS is one conforming implementation, not the only one permitted under the federation namespace (`realmsgos.org` today; `gos.world` optionally later). A third-party “ABC GOS” (different language/stack) can coexist under the same registry, URL namespace, and II session, provided it speaks **GGG** at the interoperability boundary.

**Not yet in the codebase.** Needs:

| Registry metadata | Purpose |
|---|---|
| `gos_implementation` | e.g. `realms-gos`, `abc-gos` |
| `gos_version` | implementation release |
| `ggg_conformance` | GGG spec version claimed |
| `loader_profile` | portal embedding strategy |

Portal-side **loader dispatch**: pick the embedding/bootstrap strategy by `loader_profile` rather than assuming a Realms-specific asset loader.

**GGG conformance is checked behaviorally**, not by source audit: call the realm's public Candid interface and verify it returns the shapes GGG defines. No access to source or WASM required. This can gate an informational **“GGG-verified”** badge in the registry. It must **not** gate slug registration (see §7).

---

## 6. Frontend isolation — sandbox every realm

**Problem:** loading realm frontend JS into the portal's own origin (same-origin shell) means any realm's code — buggy, malicious, or third-party — can read the portal's session storage and act as the user against *any* canister, not just its own realm.

**Decision: sandbox every realm frontend, including Realms-native ones.** No trusted/untrusted two-tier system — one path, no exceptions. Enforcement is entirely on the portal side (a realm frontend never controls its own embedding).

### Implementation

- Each realm frontend renders in an **`<iframe>`** pointed at its existing IC origin (`https://{canister-id}.icp0.io/…` — no new DNS/TLS needed).
- `sandbox="allow-scripts allow-forms allow-popups"` — **no** `allow-same-origin`.
- Portal and realm communicate only via **`postMessage`**, against the shared contract in [FEDERATION_PORTAL_REALM_BRIDGE.md](./FEDERATION_PORTAL_REALM_BRIDGE.md) (auth handoff, navigation, resize, backend-call passthrough). This is part of the GGG conformance surface.
- **Auth handoff, not shared session:** realm frontends must **not** call `AuthClient.login()` directly. The portal mints an **II delegation scoped to that realm's backend canister** (II delegations support a `targets` field) with a short TTL, and passes only that down via `postMessage`. The realm never sees the general-purpose portal session.
- **Origin-check caveat:** a sandboxed iframe without `allow-same-origin` gets an opaque `null` origin, so `event.origin` cannot be checked against a domain string. Use a one-time handshake via **`MessageChannel`** (transfer a port once at load; trust the channel thereafter) instead of origin-string checks.

### Explicitly not required (and why that's fine)

| Item | Rationale |
|---|---|
| **`frame-ancestors` CSP** on realm canisters | Self-protective for direct visitors against rogue embedders — unrelated to portal security. The sandbox already protects portal users. Publish as guidance for realm builders; **not** a registration requirement. |
| **WASM audits / reproducible builds** | Registry is open to third parties whose source you cannot audit. Sandbox design makes session isolation hold regardless. Build-hash / source-link metadata may exist as **optional self-declared trust signals** (“verified build” badge), never a gate. |
| **Worst case (malicious realm)** | Can misuse its own scoped delegation against its own backend, or serve bad content to direct visitors. **Cannot** touch the user's session elsewhere. Slug squatting and abusive content are governance/dispute problems, not session-security problems. |

---

## 7. Registry stays open, not permissioned

No gate on slug registration for WASM trust reasons.

What the registry **can** reasonably gate or badge without requiring trust:

- **GGG conformance badge** — behavioral check against the public API (§5).
- **Optional self-declared build/source metadata** (§6).

Everything else (who may claim which names, dispute resolution) is **governance policy**, orthogonal to the technical architecture in this document.

---

## 8. No off-chain resolution trust

A Cloudflare Worker or nginx must **not** be the authority for “which canister is `myrealm`”. DNS/TLS may terminate at the edge for **redirects only** (§4), but **slug authority lives in the registry canister**; the portal **calls the registry over IC** via `@update`.

Compromise of the portal **canister** WASM is the same class of risk as a compromised registry — mitigated by open builds, upgrade governance, and visible canister IDs.

---

## Architecture (high level)

```text
User browser  ──►  portal origin  /r/{slug}
                         │
                         ├─► registry.resolve_slug(slug)   [@update, consensus]
                         │
                         ├─► II login (portal only)  /join?returnTo=…
                         │
                         └─► sandboxed iframe  →  {frontend-id}.icp0.io
                                  ▲
                                  │  MessageChannel + postMessage
                                  │  (scoped delegation, nav, backend passthrough)
                                  ▼
                             portal shell
```

Realm installer continues to provision **per-realm frontend + backend canisters**. Public URL registered on-chain becomes `https://{env-host}/r/{slug}` (e.g. `https://staging.realmsgos.org/r/{slug}`), not `{canister-id}.icp0.io` as the primary user-facing link.

---

## Consequences

**Pros**

- One II principal for all realms without alternative-origins lists.
- Unlimited realms; II origin limits become irrelevant.
- Slug → canister mapping is consensus-backed.
- Session isolation: realm JS cannot exfiltrate portal credentials.
- GOS-neutral federation: Realms GOS and third-party implementations can coexist under GGG.

**Cons / trade-offs**

- Portal shell + iframe bridge add integration complexity vs. same-origin embedding.
- Realm frontends must adopt delegation handoff (no direct `AuthClient.login()` in portal context).
- One extra consensus round per session for slug resolution (acceptable vs. security).
- Pretty URLs lag canonical URLs by DNS provisioning time.

---

## Migration

1. Add `resolve_slug` **update** endpoint (+ multi-implementation metadata fields) to `realm_registry_backend`.
2. Extend portal frontend with `/r/{slug}`, `/join`, and sandboxed iframe shell.
3. Implement realm bridge SDK ([FEDERATION_PORTAL_REALM_BRIDGE.md](./FEDERATION_PORTAL_REALM_BRIDGE.md)); update Realms `realm_frontend` for delegation handoff.
4. Installer registers `url = https://{env-host}/r/{slug}`; stop appending realm `icp0.io` URLs to alternative-origins.
5. Deploy `domain-bridge` worker for pretty URL redirects (§4).
6. Deprecate cross-origin `derivationOrigin` wiring in `realm_frontend` for end-user login.

---

## Open items (decide during implementation)

- `domain-bridge` as new service vs. module in `realms-management-service`.
- Exact shape of the realm-frontend `postMessage` SDK/contract — see [FEDERATION_PORTAL_REALM_BRIDGE.md](./FEDERATION_PORTAL_REALM_BRIDGE.md).
- Whether/when to add certified-query resolution as a latency optimization over the `@update` default.
- Merge registry wizard UI into the portal origin (`/deploy`) vs. keep registry on a second fixed origin (would reintroduce **one** alternative origin entry — still fine).
- How quarter/secession frontends interact if a quarter ever needs a **separate** login origin (see `QUARTERS.md`).

---

## Verification checklist

- [ ] `resolve_slug` implemented as `@update` (documented in candid; portal uses update, never query).
- [ ] Portal login works without `derivationOrigin` and without `ii-alternative-origins`.
- [ ] Same II principal joins registry and two different slug paths (`/r/a`, `/r/b`).
- [ ] Malformed slug / wrong canister ID surfaces a hard error, not a silent wrong realm.
- [ ] Join flow: `/r/{slug}/join` → `/join?returnTo=…` → back to stepper when unauthenticated; skip when already signed in.
- [ ] Realm UI loads in sandboxed iframe (`allow-same-origin` absent); portal session not readable from iframe.
- [ ] Delegation handoff: realm receives backend-scoped delegation only; cannot call arbitrary canisters with portal session.
- [ ] MessageChannel handshake used (not origin-string checks on `null` iframe origin).
- [ ] Pretty URL worker creates 302 only for on-chain slugs; canonical URL works before `live`.
- [ ] Registry open registration: GGG badge is informational, not a slug gate.
