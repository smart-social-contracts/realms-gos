# realm_backend refactor plan

Goal: rewrite `src/realm_backend` to **at most 10,000 lines of Python**, structured like
`Casals/src`: one registry of operations, one dispatcher, flat domain modules, entities without
logic, no boilerplate. We are not live, so there are no compatibility aliases and no data migration.
Callers that break are fixed.

## Where we are

| | today | target |
|---|---|---|
| Python lines (tracked, excluding tests) | 59,277 in 204 files | ≤ 10,000 in ~25 files |
| `main.py` | 8,685 lines, hand-written | ~350 lines, generated |
| Ways into backend logic | 3 (candid endpoints, extension bridge verbs, codex bridge verbs) | 1 (`@op` + `dispatch`) |
| Authorization | Cedar, opt-in per build, next to 8 styles of Python checks | Cedar only, on every op |
| Response wrappers | 27 `Record` classes, 194 `try/except`, 15 `_json` duplicates | JSON text in and out, one error format |

Breakdown of today's lines: `core/` 35,093 · `main.py` 8,685 · `ggg/` 7,605 · `api/` 6,311 ·
`ggg_sdk.py` 1,543. (`extension_packages/` is a gitignored build copy, not source.)

## Scope

Tracked code in `realms-gos` (`.py .ts .js .svelte .sh .did .cedar`), today and target:

| Folder | Today | Target | Why it's in scope |
|---|---|---|---|
| `src/realm_backend` | 204 files · 59,277 | ~25 files · ≤ 10,000 | the refactor itself |
| `tests/` and `src/realm_backend/tests` | 140 files · 35,208 | ≤ 5,000 | rewritten as op unit tests + acceptance scenarios |
| `cli/` (Python, excluding tests) | 23,663 | ≤ 5,000 | talks to every backend endpoint; packages extensions into the old layout |
| `scripts/` | 52 files · 12,645 | ≤ 1,500 | build, publish and deploy steps tied to the old layout; deploys go through Casals |
| `packages/` | 26 files · 2,090 | ~1,000 | `extension-bridge` becomes `rpc(op, args)` |
| `src/declarations` | 6,284 | regenerated | generated from the new `.did` |
| `src/realm_frontend` | 157 files · 26,757 | API layer only | 35 backend call sites become one `op()` client; UI unchanged |
| `docs/` | | GGG spec | rewritten around the spec (see Safety net) |
| **Total, budgeted folders** (backend, tests, CLI, scripts, packages) | **132,883** | **≤ 22,500** | |

Out of scope for this round: `src/marketplace_backend` (4,710), `src/marketplace_frontend` (8,868),
`src/platform_dashboard_frontend` (2,225). They are separate canisters and get their own pass.

Other repositories that must follow: `codices` (realms-codices: hooks against the new SDK),
`extensions` (realms-extensions: `rpc(op, args)`), `geister` (MCP tools generated from `ops_list`)
and `Casals` (e2e harness and realm deploy).

## Safety net

Today's tests cannot protect the refactor:

- **They don't all pass.** Run file by file (they must be: groups replace modules with different mocks),
  backend tests give 2,215 passed, 123 failed, 10 errors in 18 of 110 files. Frontend unit tests:
  383 passed, 10 failed. CLI: 569 passed, 26 failed, 1 collection error.
- **Nothing runs them.** `ci-pr.yml` and `ci-main.yml` only run on manual dispatch (their `push` and
  `pull_request` triggers are commented out). Only the cross-quarter tests run on push. The full e2e
  (`realms-e2e.yml`) is manual.
- **They test the implementation, not the behaviour.** Most are white-box tests of modules that the
  refactor deletes (bridges, verb tables, REPL, source-scan ratchets). They'll be deleted with them.

So the guarantee comes from a new suite that describes behaviour and is written **before** the new code:

1. **Acceptance scenarios** (`tests/e2e/scenarios.py`, like Casals): user stories run against a
   local deploy, through the public API only. One per GGG capability: join a realm; create a department
   and appoint; propose, vote, execute; governed replay; grant and use power of attorney; issue and pay
   an invoice; adopt a tax policy and assess; mint land and a credential; licence an organization;
   file a dispute and record a verdict; install a codex and run its hooks; install an extension and
   call it; create a quarter, sync, pass a federal vote; encrypt and share a document.
2. **Authorization matrix** (`test_auth_matrix.py`, like Casals): every op × every role
   (anonymous, member, department head, admin, agent position, delegate, extension, codex) → allowed
   or denied. This pins Cedar's behaviour and the guardrails.
3. **Op unit tests**: each op run in-process against a fake `chain` (Casals `tests/unit/fake_chain.py`).
4. **CI on**: lint, unit tests, auth matrix and the budget check on every PR; acceptance scenarios on
   merge to `main` and nightly. Nothing merges red.

Because there are no compatibility aliases, the scenarios are written against the **new** op API.
Recording what the old system does is still useful: before deleting it, run each scenario by hand
against the old realm and note any behaviour the new design deliberately changes.

## Design

### One operation, declared once

```python
@op("proposal.vote", delegable=True)
def proposal_vote(principal, args):
    p = find(Proposal, args["proposal"])
    ...
    return {"vote": v.id}
```

`op(perm, at=None, read=False, delegable=False, governed=None)` registers the function. Everything
else is in `dispatch`, written once (Casals `op.py` is 56 lines):

1. parse the JSON args, resolve the caller;
2. **delegation**: when `delegable` and `args["on_behalf_of"]` is set, check the caller holds an active
   grant covering `perm`, then act as the grantor. The audit keeps both principals;
3. **authorize**: ask Cedar `(principal, action=perm, resource=scope)` via `auth.authorize`;
4. **governed**: when `governed="org"` (or `"realm"`) and the governing org's policy is not direct,
   file a proposal that replays `(op, args)` and return `{"proposal": id}`. The replay calls `dispatch`
   again with realm authority;
5. run the function (driving its generator if it makes inter-canister calls);
6. audit every update as an `Event`;
7. return `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

Op functions contain no `try/except`, no response classes, no permission checks and no JSON handling.

### Every caller uses the same registry

| Caller | How |
|---|---|
| Frontend, CLI, agents | candid endpoints generated from the registry: `name(args: text) -> text` |
| Extensions (sandboxed) | the sandbox's `rpc(name, args)` calls `dispatch` as the calling user, limited to the extension's declared ops |
| Codices (sandboxed hooks) | effects are `(op, args)` pairs applied through `dispatch` with codex authority, limited to declared capabilities |
| Proposals | replay = `dispatch(op, args)` with realm authority |
| MCP (geister) | `ops_list` returns the registry (name, perm, docstring, delegable), so tools are generated, not hand-written |

This removes all ten `*_bridge.py` modules, `extension_access`, `call_origin` and the per-endpoint
delegation code, and it makes "which operations can be delegated" a flag instead of a hand-kept list.

### Authorization: Cedar, and only Cedar

Cedar is the realm's single decision point. Today it is opt-in per build and sits next to eight styles
of Python checks (`_check_access`, `_require_admin`, `can_manage`, …); after the refactor it is the only
gate:

- `dispatch` asks Cedar for **every** op. No Python permission checks remain in op functions.
- Every build links Cedar. There is no stock build without it, and it fails closed.
- `guardrails.cedar` holds the non-negotiable `forbid` rules, including the new ones:
  an agent position never signs; nobody decides a matter they filed or that was filed on their
  behalf; a grant never exceeds the grantor's own permissions.
- `policies.cedar` holds the ordinary rules; codices extend them.
- The Cedar schema and the entity slice are generated from `ggg`, so adding an entity needs no
  hand-written Cedar plumbing.

`auth.py` is the thin glue: resolve the principal (and the grantor, for delegation), build the slice,
call the engine from `ic-basilisk-toolkit`. The `.cedar` files are policy data and are not counted
in the Python budget.

### `ggg` is data only

`ggg/` holds the GGG standard as entities, without methods beyond trivial helpers. It's split by domain
in a handful of files (`identity`, `governance`, `finance`, `land`, `justice`, `system`), with a `Json`
field type like Casals `model.py`. No posthooks, no business logic, no logging. Target ~1,500 lines (today
7,605). Leaner models from earlier discussions are applied here: many licences per organization,
`Position` usable by both departments and organizations (with `agent` and `supervisor`), credentials
as NFTs with a `ggg:` metadata header.

### Style rules

- Domain modules ≤ 600 lines; functions short; names say what they do.
- Comments only where the code cannot say it. No module docstring essays.
- Imports at the top. A lazy import only where Basilisk requires it, with a one-line reason.
- No defensive code for impossible states; validate input once, at the op boundary (`find`, `args[...]`).
- No demo or test code in the canister. Test mode is a single flag; demo data is seeded by the CLI through ops.

## Target layout and budget

| File | Covers (replaces) | Budget |
|---|---|---|
| `main.py` | generated by `scripts/gen_main.py` | 350 |
| `op.py` | registry, dispatch, governed replay, audit, REPL hook (`governed_action`, `api_errors`, `extension_errors`, `repl_host`, endpoint wrappers) | 180 |
| `auth.py` | Cedar decision point: principal and grantor, entity slice, schema from `ggg`, embedded policies (`cedar_authz`, `cedar_entities`, `cedar_schema_runtime`, `cedar_policies`, `access`, `call_origin`, `org_policy`) | 400 |
| `chain.py` | caller, time, timers, inter-canister calls, ledgers (`time_utils`, `random`, `candid_types_realm`) | 200 |
| `ggg/` | entities, including the full justice model (`ggg/*`, `facade`, `realm_ref`) | 1,500 |
| `members.py` | join, profiles, invites, roles, bulk import (`membership`, `admin_users`, `user_get_record`, `citizen_import`, `join_targets`, `console_bridge`, `api/user`) | 350 |
| `orgs.py` | departments, organizations, positions, appointments, policies (`department_*`, `org_member_admin`, `position_admin`, `acting_appointments`) | 450 |
| `proposals.py` | proposals, votes, execution (`proposal_dispatch`, `proposal_execution`, `lifecycle_gate`) | 450 |
| `delegation.py` | power of attorney, requests (`delegation`) | 200 |
| `treasury.py` | vault, transfers, invoices, budgets, payroll, reports (`treasury_*`, `payroll`, `financial_reports`, `realm_currency`) | 550 |
| `taxes.py` | tax policy, declaration, assessment (`taxation`, `tax_bridge`) | 300 |
| `tokens.py` | fungible token, NFTs, credentials (`api/tokens`, `api/nft`) | 350 |
| `licenses.py` | licence applications and decisions | 150 |
| `land.py` | parcels, zones (`land_bridge`, `api/zones`) | 200 |
| `justice.py` | justice systems, courts, judges, disputes, cases, verdicts, appeals, penalties (`core/justice/*`) | 800 |
| `procurement.py` | tenders, sealed bids, scoring, awards, vendor reputation (`core/procurement/*`) | 400 |
| `notifications.py` | (`notification_bridge`, `activity`) | 120 |
| `crypto.py` | vetKeys, key envelopes, scopes, department documents (`crypto_scopes`, `api/vetkeys`, `api/crypto`, `dept_docs_bridge`) | 250 |
| `codex.py` | install, overlay, revert, hook dispatch (`codex_*`, `runtime_codex`, `package_manager`) | 450 |
| `sandbox.py` | subinterpreter spawn, rpc, effects (`runtime_sandbox`, `codex_bridge`, `async_bridge`, `bridge_core`, `sandbox_admin`) | 400 |
| `sdk.py` | in-sandbox SDK source (`ggg_sdk`) | 350 |
| `extensions.py` | install, call, grants, sidebar (`runtime_extensions`, `extension_bridge`, `extension_grants`, `extensions`, `core_extensions`, `privileged_extensions`, `sidebar_visibility`, `api/extensions`) | 450 |
| `quarters.py` | bootstrap, sync, directory, scaling, cross-quarter calls (`quarter_*`, `autoscale`, `cross_quarter`, `fleet_registry`, `api/cross_quarter`, `api/quarter_*`, `api/messaging`) | 600 |
| `federation.py` | federal votes, secession (`federation`, `federal_vote_runtime`, `federal_tally`) | 250 |
| `setup.py` | first run, config, branding, status, upgrade info, `/version` (`setup*`, `api/setup`, `realm_config_admin`, `runtime_flags`, `realm_locales`, `build_variant`, `api/status`, `api/upgrade`, `api/registry`, `api/version_http`) | 300 |
| **Total** | | **10,000** |

Quarters and federation are must-haves; their budget (900 lines, today ~5,000) is met by
removing duplication, not features.

### Moved out of the canister (not counted)

| What | Lines today | Where it goes |
|---|---|---|
| `api/file_registry.py` | 1,639 | the file-registry canister, called directly by frontend and CLI |

### Deleted

`demo_registration`, `demo_notice`, `test_identity_seed`, `system_snapshot`, `entity_import`,
`listing` / `api/ggg_entities` (replaced by one generic read op), all `_json` endpoint twins,
all candid `Record` response classes.

## Enforcing the budget

`scripts/loc_budget.py`, run in CI and as a pre-commit hook, fails when:

- `src/realm_backend` (excluding `tests/`) exceeds 10,000 lines;
- any module exceeds its budget above by more than 10%;
- `main.py` differs from `scripts/gen_main.py --check`.

## Phases

Work happens on a branch with a fresh `src/realm_backend`; the old tree is read as reference and
deleted at the end. From phase 1 on, each phase ends with its op unit tests, the authorization matrix
rows for its ops and its acceptance scenarios passing on a local deploy.

| # | Phase | Exit criterion |
|---|---|---|
| 0 | **GGG spec first**: `docs/GGG.md` (entities, lifecycles, operations, invariants); acceptance scenarios and auth matrix written from it (failing); CI turned on | spec reviewed; scenarios and matrix exist and run (red) |
| 1 | Skeleton: `op.py`, `auth.py` (Cedar), `chain.py`, `gen_main.py`, `loc_budget.py`, fake chain | an empty realm deploys; `ops_list` works |
| 2 | `ggg/` entities, exactly as specified | all entities defined; budget met |
| 3 | Governance: members, orgs, proposals, delegation | governance scenarios green |
| 4 | Finance and assets: treasury, taxes, tokens, licences, land, justice | finance and asset scenarios green |
| 5 | Codex: codex, sandbox, sdk; rewrite the three codices against it | agora, dominion and syntropia scenarios green |
| 6 | Extensions runtime; drop the CLI/script path that copies extensions into `extension_packages/` | extension scenarios green |
| 7 | Quarters and federation | quarter and federal-vote scenarios green |
| 8 | Setup, notifications, crypto, http | all scenarios green locally |
| 9 | Callers: frontend API layer, CLI, scripts, packages, geister, Casals harness; delete the old tree | staging deploy passes every scenario |

## Decisions

Confirmed:

- Cedar stays, as the single authorization mechanism.
- Quarters and federation stay, rewritten lean.
- The full justice model stays in GGG (justice systems, courts, judges, disputes, cases, verdicts,
  appeals, penalties). Its budget is met by trimming other modules, keeping the 10,000 cap.
- No compatibility aliases and no data migration.
- The budget counts all tracked Python under `src/realm_backend` except `tests/`, including the
  generated `main.py` and the sandbox SDK; `.cedar` policy files are not counted.
- Scope includes `tests/`, `cli/`, `scripts/` and `packages/` with the budgets in the Scope table;
  the marketplace and platform dashboard are left for a later pass.
- Procurement stays a core GGG domain (`procurement.py`); department documents become ops in `crypto.py`.
- The REPL stays: Basilisk's `__shell__` calls ops through `dispatch` and Cedar, like any other client.
  `repl_host`'s 821 lines become a small hook in `op.py`.

Module budgets are targets; the hard rule is the 10,000 total.
