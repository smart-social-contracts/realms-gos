# Quarters: Horizontal Scalability Architecture

## Overview

Quarters enable Realms to scale horizontally. Each quarter is a **full, autonomous realm backend** that participates in a **federation** — a group of quarters sharing a common manifest, extensions, and governance framework. Any quarter can **secede** at any time to become an independent realm, taking all its users and data with it.

This design supports the core Realms GOS principle: **opting out must always be frictionless**.

---

## Terminology

| Term | Definition |
|------|-----------|
| **Quarter** | A full realm backend canister that participates in a federation. Holds its own users, governance data, extensions. |
| **Capital** | The codex origin and admission gate. Relays and aggregates federal votes; pushes codex amendments. Still an ordinary quarter — holds users, runs local governance. |
| **Federation** | A group of quarters sharing a manifest, extensions, and governance framework. Communication is peer-to-peer. |
| **Home quarter** | Client-cached pointer to the quarter where a user first registered (not a global registry). |
| **Secession** | A quarter leaving the federation to become an independent realm. Zero data migration required. |

---

## Design Principles

1. **A quarter IS a realm.** Same WASM, same entities, same API surface. The only difference is two flags (`is_quarter`, `is_capital`) and a `federation_realm_id`.

2. **Federation is an opt-in overlay.** Every quarter is born as a full realm that *chooses* to participate. The federation layer is useful but not load-bearing.

3. **No quarter depends on another to function.** If the capital goes down, quarters keep operating locally. Realm-wide governance pauses but local governance continues.

4. **One frontend serves all quarters.** The frontend is a static SvelteKit app served from a single asset canister. It dynamically switches which backend canister it talks to.

5. **Opting out is frictionless.** Secession = flip a flag. All users, data, governance, extensions remain intact.

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
     │ (Quarter A)│←→│              │←→│            │  (peer gossip)
     │ ~1000 users│  │  ~1000 users │  │ ~1000 users│
     └────────────┘  └──────────────┘  └────────────┘
```

- **Capital** is the codex origin, admission gate, and federal-vote relay — logically equal to other quarters.
- **Join-time population push** — after a new member registers on a quarter,
  that quarter immediately reports its live `User.count()` to the capital
  (`report_quarter_population`). The UI reads populations from **`capital.status().quarters`** only.
- **Frontend** bootstraps from the capital's canister ID (baked in at build time), then routes dynamically.

---

## What Quarters Share

- **Manifest** (`manifest.json`) — source of truth lives on the capital; quarters hold copies.
- **Extensions** — same set deployed to all quarters.
- **Federation Codex** — realm-wide governance rules (tax policy, voting rules, quarter assignment strategy).

## What Quarters Own Independently

- **Users** — each quarter holds its own `User` rows. First join is
  system-assigned to one quarter; additional quarter memberships are deliberate.
- **Governance data** — proposals, votes, disputes, etc. local to the quarter.
- **Zones** — potentially same or different geographic zones.

---

## User Model

### First join (assigned)

There is **no free picker** on `/join` for open registration (overload /
brigading risk). The system assigns a joinable quarter:

1. User opens frontend → connects to capital (bootstrap canister ID).
2. `get_join_targets()` / federation codex `assign_quarter` picks a joinable
   quarter (default: **least-populated** with capacity). Once sub-quarters
   exist, the capital is coordinator-only (`joinable=false`).
3. Frontend switches actor to the assigned quarter and registers there.
4. UI shows the assignment banner; invite / `?quarter=` may override (issuer-
   assigned, not user browsing).
5. Client caches `home_quarter` in `localStorage`.

### Multi-quarter membership

A principal **may** register on more than one quarter (admins, strategic
presence). That is a **separate deliberate flow**, not the default join wizard.
There is **no GuestUser** — presence requires full registration on that quarter.
Duplicates are not prevented centrally; the codex may sanction them ex-post.

### Returning User Flow (home-quarter discovery ladder)

There is **no central per-user index**, so a returning user's quarter is
recovered from progressively slower sources:
1. **Client-carried pointer (fast path)** — frontend reads the cached
   `home_quarter` canister ID from `localStorage` and activates it immediately
   (`setActiveQuarter`) before any round-trip.
2. **Federated broadcast** — probe each known quarter with
   `get_my_user_status()` (also runs automatically on login via
   `loadUserProfiles`).
3. **User-entered quarter number** — every quarter has a small stable integer
   `index` in the federation catalog (capital = 0); a user who remembers it can
   route directly.
4. **"Find my quarter" on `/join`** — same broadcast, then redirect into the app.

**Hardening:** before any new registration on `/join`, the app must run the
federated membership probe. If the principal is found on any quarter →
"Welcome back" + route; never offer Terms/Profile join again for that session.

> Note on identity: an Internet Identity principal is **per-frontend-origin**.
> A single realm frontend yields a stable principal across all its backend
> quarters; a *seceded* quarter that serves its own frontend origin would see a
> different principal unless it configures `derivationOrigin` /
> `/.well-known/ii-alternative-origins`.
>
> **Secession requirement (#233):** the federation now pins a canonical
> `derivationOrigin` (the registry) so one human = one principal across all
> realms + the registry. Any quarter/realm that secedes with **its own frontend
> origin** MUST be added to the canonical
> `/.well-known/ii-alternative-origins` list, or its users will get a fresh
> principal and lose access to their existing records. See
> [`IDENTITY_AND_ASSISTANT.md`](./IDENTITY_AND_ASSISTANT.md).

---

## Assignment Strategies

Defined in the federation codex (`assign_quarter` function). Built-in strategies:

1. **least_populated** — **product default**; pick the joinable quarter with fewest residents (capacity-checked).
2. **random** — `hash(principal) % len(active_quarters)` → uniform load.
3. **user_choice** — honour a *preferred* quarter (invite / deep link / deliberate multi-quarter flow) if it has capacity — **not** an open join-page picker.

Custom codexes can implement arbitrary rules (geography, invitation codes, profile attributes).

---

## Auto-Scaling / Sharding

Sharding is **policy-driven, non-blocking, and provisioned directly through
Casals** (issue #156). It is triggered on **every new user registration** and
never blocks the join.

### The decision (codex hook + default)

The federation codex may define a `should_deploy_quarter(populations, network,
realm)` hook. `populations` is the list of per-quarter resident counts
(including the capital). When the codex defines no hook, the built-in default
(`core/autoscale.py`) applies:

- **Scale when the fullest quarter reaches 90% of N** — the 90% headroom means
  the triggering user still lands on an existing quarter.
- **N = 2000** in production; **N = 10** for the `test` / `staging` / `demo`
  networks (so CI exercises sharding without thousands of joins).
- A broken/throwing codex hook safely falls back to the default policy.

### The mechanism (record intent → provision out of band)

1. `user_register` (all join paths) calls `maybe_request_quarter_scale()` after
   the post-hook. If the policy fires and no deploy is already queued, it sets an
   **idempotent guard** `Realm.scale_in_flight = True` and records the time. It
   never performs the deploy itself — joins are never blocked on provisioning.
2. A separate async endpoint `process_quarter_scaling()` (called by a
   controller, timer, or task manager) acts on the flag: when
   `manifest_data.casals.casals_canister_id` is set, the capital asks Casals to
   provision **one backend-only quarter** (`create_canister` under the realm's
   existing stand), drives `bootstrap_as_quarter` on it, optionally hands the
   canister to the stand baton, then **registers** the new canister as a Quarter
   (assigning the next catalog index) and clears the guard so the next threshold
   crossing can re-trigger. Without `casals_canister_id`, provisioning stays
   blocked until an operator wires the direct path.
3. **Users stay put.** Existing users are not migrated; the assignment strategy
   simply starts directing *new* registrations to the freshest quarter once it
   is registered.
4. Backend only — new quarters share the capital's single frontend; the frontend
   switches actors per home quarter (see Frontend Changes).

### Why no central per-user location index

`ic-python-db` caps a canister at ~5000 entities, so we never store a global
user→quarter map anywhere. Gossip and the registry carry only **coarse**
container-level data (quarter list, populations, indices), which stays well
under the cap. A user's location is recovered from their own client-carried
pointer / a small quarter integer / (last resort) a broadcast search — never
from a central index.

---

## Federal Votes

Realm-wide decisions use the **federal vote** GOS mechanism (issue #300):

1. **Propose** — `propose_federal_vote` on any quarter; the capital opens the vote.
2. **Local legs** — each quarter runs its own ballot via the voting extension.
3. **Aggregate** — the capital collects leg outcomes under the frozen aggregation rule.
4. **Execute** — if adopted, every quarter runs the frozen action (including non-voting quarters).

The aggregation rule is set by the codex hook `get_federal_governance_params`, frozen at open time (default: `per_quarter` — one quarter, one vote). The capital is a relay, not the electorate; there is no global user registry. A `vote_hash` binds `action + rule + deadline`; execution happens only after hash verification. Dissent = fork; exit = secession.

---

## Secession

A quarter declares independence:
1. Set `is_quarter = False`, `is_capital = False`, clear `federation_realm_id`.
2. Stop participating in peer gossip.
3. Deploy own frontend asset canister (optional — can reuse existing).
4. All local users, data, governance, extensions remain intact.
5. Former peers remove it from their quarter list.
6. Cross-quarter registrations involving this quarter become ordinary
   out-of-federation memberships (no automatic guest severance — there is no
   GuestUser layer).

**Joining a federation** is the reverse: set `is_quarter = True`, configure `federation_realm_id`, start gossip, register with peers.

---

## Data Model

### Realm Entity
```python
is_quarter = Boolean(default=False)
is_capital = Boolean(default=False)  # codex origin + admission gate
federation_realm_id = String(max_length=64)
auto_scale_enabled = Boolean(default=True)
scale_in_flight = Boolean(default=False)
# ... plus installer / casals wiring fields
```

### User Entity (per quarter canister)
```python
# Optional local hint — not a federation-wide index
home_quarter = String(max_length=64)
```

There is **no GuestUser** entity in the product model. Multi-quarter presence =
full registration on each quarter.

### Quarter Entity
```python
class Quarter(Entity, TimestampedMixin):
    name = String()
    canister_id = String()
    federation = ManyToOne("Realm", "quarter_ids")
    population = Integer(default=0)
    status = String(default="active")
    index = Integer(default=0)     # stable catalog number (capital = 0)
```

---

## API Changes

### Modified Endpoints

| Endpoint | Change |
|----------|--------|
| `join_realm(profile, preferred_quarter)` | Register on the target quarter; may set local `home_quarter` |
| `get_my_user_status()` | Return membership on *this* canister (+ optional `assigned_quarter`) |
| `get_join_targets()` | Public join policy; `default_quarter` = least-populated joinable |
| `change_quarter(new_quarter_canister_id)` | Move / update local home pointer (separate from multi-register) |

### New / federation Endpoints

| Endpoint | Purpose |
|----------|---------|
| `declare_independence()` | Secede from federation |
| `join_federation(capital_canister_id)` | Join an existing federation |
| `sync_quarters()` | Peer gossip: exchange quarter list + populations |
| `report_quarter_population(population)` | Quarter→capital push of live `User.count()` after join |
| `get_scale_status()` | Report auto-scale state |
| `process_quarter_scaling()` | Provision + register a new quarter (direct Casals path) |

The capital must have `manifest_data.casals.casals_canister_id` set so it can
command its Casals stand to mint backend-only quarter canisters.

---

## Frontend Changes

1. **Auto-routing on login** — cache → federated probe → `setActiveQuarter`.
2. **Assigned join** — resolve target via `get_join_targets`, register on that quarter, show assignment banner (no free picker).
3. **Federated probe before join** — prevent accidental double-registration.
4. **localStorage cache** — persist `home_quarter` canister ID for instant reconnect.
5. **Multi-quarter activation** (optional) — session routing among existing memberships, distinct from registration.

Canonical product doc: root [`QUARTERS.md`](../../QUARTERS.md). Tracking: GitHub [#156](https://github.com/smart-social-contracts/realms-gos/issues/156).

## Acting inherit at quarter birth (issue #301)

New quarters seed their org chart from the codex, then **inherit capital
seat-holders as acting officers** on positions where `inherit_from_capital` is
true (see `core/acting_appointments.py`). Copy runs once at quarter creation;
it does not re-sync on later capital changes.

- **Acting** holders are visible in Organizations (`access_manager`) with an
  "acting" label; substantive appointments replace them locally.
- **Congress** and other locally elected seats are appointed by each quarter's
  own governance after beta — capital acting holders are a bootstrap only.
- Department **target policies** (e.g. 5-of-10) are persisted at seed time and
  applied to live policy at beta; quarter birth does not ratchet policies.

---

## Implementation Phases

### Phase 1: Core Loop (High Priority)
- Add `is_capital` to Realm entity
- Add `home_quarter` to User entity
- Fix `join_realm()`, `get_my_user_status()`, `change_quarter()` to persist/read `home_quarter`
- Frontend auto-routing on login
- Frontend two-step join flow

### Phase 2: Federation (Medium Priority)
- Peer gossip (`sync_quarters` inter-canister calls)
- `declare_independence()` / `join_federation()` endpoints
- Deliberate multi-quarter registration / activation UX (not GuestUser)
- CLI `--capital` flag for `quarter create`

### Phase 3: Automation (Low Priority)
- Auto-provisioning new quarters at capacity — **implemented** (codex
  `should_deploy_quarter` hook + 90%-of-N default, `scale_in_flight` guard,
  `process_quarter_scaling()` → direct Casals `create_canister` + bootstrap)
- Optional QuarterRouter canister (cache/accelerator, not required)
