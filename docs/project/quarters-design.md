# Quarters: Federation Through a Shared Codex

## Overview

A **realm is a set of canisters that sync the same stack to a capital canister.** The stack is WASM + codex + manifest + extensions; the capital publishes it (it does not run day-to-day governance). The codex is the realm's constitution — it encodes every policy: admission, fees, governance, taxation, justice, assignment. Quarters are sovereign demoi (each home to up to a few thousand users) that belong to a realm because their users collectively choose to adopt its codex.

The whole architecture reduces to one primitive:

> **A realm is a set of canisters that sync the same stack to a capital canister.** (Technical.) A realm is a decentralized network of computers that sync the same digital system of governance to a capital. (Plain.)

Everything else — capital, fees, taxation, reputation — is policy *inside* the codex, not load-bearing infrastructure.

---

## Terminology

| Term | Definition |
|------|-----------|
| **Quarter** | A full, sovereign realm backend canister, home to up to a few thousand users. Holds its own users, governance data, and extensions. Acts as a collective entity via its codex (decisions made by its users' votes). |
| **Realm** | A set of canisters that sync the same stack to a capital canister. Membership *is* codex-adherence. |
| **Codex** | The shared constitution of a realm. Encodes all policy. Distributed from the capital on admission and on amendment. |
| **Capital** | The quarter that is the codex's origin and pusher, and the gate for admission. It is still an ordinary quarter (holds users, runs governance). Its only realm-wide powers are **admission** and **codex propagation** — both disciplined by every quarter's freedom to secede. |
| **Secession** | A quarter dropping the codex and ceasing to sync. Frictionless: all users and data stay with the quarter. |
| **Fork** | A contentious amendment splits the realm: quarters on the new codex and quarters on the old codex are, by definition, now two realms. Dissent is automatic and frictionless. |

---

## Design Principles

1. **A quarter IS a realm backend.** Same WASM, same entities, same API surface. A quarter is distinguished only by flags (`is_quarter`, `is_capital`) and the codex it runs.

2. **The codex defines the realm.** Two quarters are in the same realm iff they run the same codex. No shared codex, no shared realm.

3. **Quarters are sovereign.** No quarter depends on another to function. There is no global user registry and no cross-quarter "guest" presence — to participate in another quarter, you register there. **First join** is system-assigned (codex policy, default least-populated) so open registration cannot overload a single quarter; **additional** quarter memberships are deliberate and separate.

4. **The capital is a pusher, not a ruler.** Its two central functions (admission, codex propagation) are real but bounded: any quarter can secede at any time, so the capital's power is continuously re-tested by the exit option.

5. **Centralization is allowed where users tolerate it.** If a realm's codex encodes a strong capital — even a monarchy — and users keep choosing it, that is a legitimate outcome. The system stays neutral; the codex carries the values.

6. **Opting out is frictionless.** Secession = drop the codex and stop syncing. All users, data, governance, and extensions remain intact.

---

## Topology

```
                    ┌─────────────────────┐
                    │   1 FRONTEND        │
                    │   (asset canister)  │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
     ┌────────▼───┐  ┌───────▼──────┐  ┌─────▼──────┐
     │ Capital    │  │  Quarter B   │  │ Quarter C  │  ... × N
     │ (codex      │→ │  (runs codex)│  │(runs codex)│
     │  origin)    │  │              │  │            │
     │ ≤ few k     │  │  ≤ few k     │  │ ≤ few k    │
     └────────────┘  └──────────────┘  └────────────┘
        admission +        sovereign        sovereign
        codex push
```

- The **capital** originates and pushes the codex, and gates admission.
- Each **quarter** is otherwise sovereign: its own users, governance, fees.
- The **frontend** is one static SvelteKit asset canister that dynamically switches which backend quarter it talks to. It bootstraps from the capital's canister ID (baked in at build time).

---

## The Codex

The codex is the only thing quarters of a realm share. It encodes, at minimum:

- **Admission policy** — who may join the realm, and on what terms.
- **Fee policy** — registration and maintenance fees per quarter, ideally set by supply/demand and technical constraints.
- **Assignment policy** — how new members are placed on first join (`assign_quarter`). Default: least-populated joinable quarter with capacity. Free pick at join time is not the product default (overload / brigading risk).
- **Governance** — how proposals are made, who votes, how amendments are ratified.
- **Capital policy** — whether and how capital-hood can move (typically via a federal vote).
- **Taxation/tributes (optional)** — the codex may require quarters to pay tributes to the capital. This is allowed but not built-in; absence of tribute policy means no tribute.
- **Justice, duplicates, sanctions** — how cross-quarter misbehaviour (e.g. duplicate registrations) is detected and punished. Detection is ex-post and codex-driven, analogous to how analog societies handle it — not prevented by central infrastructure.

There is no quantized, system-level reputation. Reputation is whatever a subject infers about another from available records; the codex (and users) decide what to do with it.

---

## Users

### First join (assigned)

1. User signs in on the shared frontend.
2. Capital / federation policy assigns a **joinable** quarter (default: least-populated with capacity). Once sub-quarters exist, the capital is coordinator-only and not a join target.
3. Frontend registers the user on that quarter and caches `home_quarter` in `localStorage`.
4. UI shows the assignment; it does **not** offer a free picker across all quarters.
5. Invite / deep links (`?quarter=<id>`) may override the target (issuer-assigned, not user browsing).

### Returning users (discovery ladder)

There is **no central principal→quarter index**. Location is recovered from:

1. Client-carried `localStorage.home_quarter` (fast path)
2. Federated broadcast: probe each known quarter with `get_my_user_status()`
3. Manual "Find my quarter" on `/join` (same broadcast)
4. Optional: user-entered quarter catalog `index` (capital = 0)

Before any **new** registration on `/join`, the app must run the federated probe so a returning member is never accidentally registered on a second quarter.

### Multi-quarter membership

- A principal **may** register on more than one quarter (admins, strategic presence, fork prep).
- That is a **separate deliberate flow**, not the default join wizard.
- A user is present only on quarters where they have registered. There is **no GuestUser**.
- Duplicate identities across quarters are not prevented by infrastructure; they are detected after the fact and sanctioned per the codex if disallowed.

---

## Admission

1. A prospective quarter requests admission from the capital.
2. The capital applies the realm's codex admission policy.
3. On acceptance, the quarter **receives the codex** and begins running it. It is now part of the realm.

Admission is the capital's gate. Whether a previously-seceded quarter re-enters with its prior history or cold is up to the codex.

---

## Governance: Amendment, Fork, Secession

- **Amendment.** The capital pushes codex amendments. Ratification follows the codex's own rules — often a **federal vote** (see below). A fast sync mechanism propagates the new codex; transient version skew during rollout is handled by syncing, not by special-casing.

- **Federal vote.** Realm-wide decisions use a two-tier GOS mechanism (`propose_federal_vote`):
  1. Each quarter runs a local **leg** ballot (voting extension).
  2. The capital aggregates leg outcomes and broadcasts the result.
  3. If adopted, **every quarter executes** the frozen action — including quarters that did not vote.

  The aggregation rule comes from the codex hook `get_federal_governance_params`, frozen at open time (default: `per_quarter` — one quarter, one vote). The capital is a **relay and aggregator**, not the electorate; there is no global user registry. A `vote_hash` binds `action + rule + deadline`; quarters execute only after hash verification.

- **Fork (dissent).** A quarter that rejects an adopted federal action (or amendment) is running a different codex — a different realm. Dissent is frictionless.

- **Secession.** A quarter drops the codex and stops syncing. All its users and data remain intact. Former peers remove it from the realm. This is the only hard check in the system, and it disciplines the capital's admission and propagation powers.

---

## Data Model

### Quarter Entity
```python
class Quarter(Entity, TimestampedMixin):
    name = String()
    canister_id = String()         # backend canister principal
    federation = ManyToOne("Realm", "quarter_ids")
    population = Integer(default=0)
    status = String(default="active")  # active/suspended/splitting/merging
```

### Realm Flags
```python
is_quarter = Boolean(default=False)
is_capital = Boolean(default=False)   # codex origin + admission gate
federation_realm_id = String(max_length=64)
codex_version = String(max_length=64) # current codex version this quarter runs
```

No `home_quarter` on users, no `GuestUser` entity, no central tax aggregator. Those are removed from the model; any equivalent behaviour, if desired, lives in the codex.

---

## Frontend

1. **Bootstrap** from the capital's canister ID (baked in at build time).
2. **First join** — resolve assignment via `get_join_targets()` / codex policy; register on the assigned quarter; show an assignment banner (not a free picker).
3. **Returning login** — restore from `localStorage`, else federated membership probe; switch the actor to that quarter's backend.
4. **Multi-quarter session** — if several memberships exist, an activation picker may choose which quarter to work in (session routing), distinct from registration.

---

## What Changed From the Earlier Design

This document supersedes an earlier, heavier design. Removed or reframed:

- **GuestUser / cross-quarter guest presence** — removed. Participation requires registration on the target quarter.
- **Central tax aggregation** — removed as a built-in; optional tributes can be encoded in the codex.
- **`home_quarter` as a global registry** — there is still no central index; the client may cache a pointer, and each quarter holds its own `User` rows. Optional `User.home_quarter` on a quarter is a local hint, not a federation-wide map.
- **Free pick at first join** — ruled out as the product default (overload risk). Assignment is codex-driven; default strategy is least-populated among joinable quarters.
- **Capital as governance coordinator** — reframed: the capital is the codex origin + admission gate + pusher, nothing more.
- **Quantized reputation** — removed; reputation is interpretive and codex/user-driven.

See also GitHub [#156](https://github.com/smart-social-contracts/realms-gos/issues/156) (join-assignment addendum).
