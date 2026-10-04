# Voting: four proposal types + host-side executor

> **Status:** Spec for implementation  
> **App:** `extensions/extensions/voting/` + `src/realm_backend/core/proposal_execution.py`  
> **Repo:** smart-social-contracts/realms-gos

Nothing here is live. Delete the Codex-Files-only form rather than adding typed forms beside it, and do not keep a compatibility branch for the old payload shape.

---

## Why this exists

The Voting UI today only submits remote Python (`code_url` / `codices[]`). Typed actions (vault send, package install, poll) have no first-class form. Execution always goes through the sandbox.

That path is broken for anything that is not a deferred `treasury.transfer` effect:

| Fact | Consequence |
|---|---|
| Subinterpreter `sys.path = []`; only `ggg_sdk` is injected | `from core…` in generated replay code fails at spawn |
| `_PROPOSAL_MAIN_ADAPTER` rejects generators from `main()` | `install_*_from_registry` and `approve_orchestration_action` cannot run as `main()` |
| `drive_async_effects` allows only `treasury.transfer` | Install / baton approve have no deferred verb |
| No code → `"No code URL"` → `failed` | Poll cannot close |
| Voting sets `executed` if no exception escapes, ignoring `result["success"]` | Adapter failures can look successful (same on the URL path and multi-codex finalize) |
| `Proposal.metadata` is `String(max_length=4096)` | Source must not live in metadata |
| `finalize_proposal` has `entry_access: proposal.manage` in the manifest but is absent from `EXTENSION_FUNCTIONS` | A vote that expires below threshold can never be closed (`cast_vote` only auto-accepts when threshold+quorum are met) — register it |
| `get_governance_params` returns `notice_hours` but voting never reads it | Enforce it as a timelock (below) or drop the key |
| Role Manager stores `requested_permissions: ["role.assign"]` | Not a `codex_bridge` verb; `normalize_proposal_permissions` strips it |

Typed proposals must be a **structured action + host dispatcher**. The sandbox stays only for **Code Execution**.

### The change raises the stakes of three existing holes

Today a proposal's worst case is sandboxed code holding declared bridge verbs. Once `transaction` and `upgrade` dispatch host-side with realm authority, the same paths move tokens and install wasm. Fix these **in this change**, not after:

| Hole | Today | After |
|---|---|---|
| `approve_proposal` (`proposal.manage`) sets `accepted` + schedules execution with no ballot | admin runs sandboxed code | admin sends vault tokens / installs any version with **no vote** |
| `demo_approve_and_execute` (`demo.manage`) force-approves from **any** status, including `executed`, then re-runs | demo re-run of codex install | **repeatable** vault transfer — the `status != accepted` guard is defeated by the forced reset |
| Single submit gate `submit_proposal: proposal.create` | any proposer posts Python | any proposer puts a vault transfer or core wasm approval on the ballot |

**Required:** delete `approve_proposal` and `demo_approve_and_execute`. If a demo shortcut must exist, the dispatcher refuses everything except `poll` when execution was not reached through a resolved ballot — and it must never re-run a proposal already in `executing` / `executed`.

---

## Types

`metadata.proposal_type` is one of:

| Type | What voters decide | Executor |
|---|---|---|
| `transaction` | Send vault tokens to a principal | Host → `vault.transfer` |
| `upgrade` | Install a pinned Codex/Extension, or approve a Casals baton action | Host → installer / `approve_orchestration_action` |
| `poll` | Answer a question | Host no-op → `executed` |
| `code_execution` | Run stored Python with frozen bridge verbs | Sandbox → `execute_proposal_code` |

No other strings on the Voting form. No client-supplied Python for the first three. No client-supplied permissions except on `code_execution`.

---

## Storage

**Metadata (small, frozen at submit):**

```json
{
  "proposal_type": "transaction",
  "action": { },
  "requested_permissions": ["treasury.transfer"]
}
```

`action` is the only source of truth at execute. Re-reading form fields or re-resolving “latest” is forbidden. Nothing reads `metadata["action"]` today, so the key is free; `details` is Agora's convention and dies with Agora's `metadata.type`.

`_HEAVY_METADATA_KEYS` (stripped from list responses) drops `code_inline` and `details` along with them. Keep `action` **unstripped** — the list view needs it for the summary card, and it is small by construction.

**Code (Code Execution only):** write a `Codex` row at submit (`name`, `url`, `checksum`, filesystem `code`). Point at it with `action.codex_name` + `Proposal.code_checksum`. Do not store `code_inline` in metadata.

**The name is derived, never proposer-supplied.** `Codex` is one global name-aliased namespace whose `code` property writes `/{name}` on the persistent filesystem, and both `runtime_codex` and the registry installer upsert by name. Installed codex packages seed one row per module **file stem** — Agora contributes `governance`, `procurement`, and friends, not `agora` — so a proposer-chosen `codex_name: "governance"` overwrites live codex code **at submit, before a single vote**. Use `proposal_<proposal_id>`, reserve that prefix from installs, and reject any submitted `codex_name`.

Consequence: Code Execution never edits an existing codex, so the amendment/diff feature (`is_amendment`, `original`, `MonacoDiffPane`) has no subject and is deleted. Changing an installed codex is an **Upgrade**, which goes through the installer. Do not keep a code-overwrite path alive to feed the diff view — that is the hole above.

**Entity fields that stay:** `title`, `description`, `code_url` (empty unless Code Execution was fetched from a URL), `code_checksum`, `org_scope`, tallies, deadline.

---

## Per-type contracts

### Transaction

Vault ICRC-1 send. Not treasury book-keeping. Budget Manager allocation stays in Budget Manager.

**Form:** token from vault `get_active_tokens` (`treasury.view`), amount in ledger base units, recipient as `to_principal` (that is the vault arg name — not `principal`). Recipient may be any principal, in this realm or another; offer a member picker that resolves to a principal, and show the vault balance next to the amount.

**Frozen `action`:**

```json
{
  "token": "ICP",
  "to_principal": "<principal>",
  "amount": "100000000"
}
```

`amount` is a **decimal string**, parsed to `int` host-side. A JSON number goes through JS as a float64 and silently loses precision past 2^53 — reachable for 8-decimal supplies and routine for 18-decimal tokens. `Vault.svelte`'s own `toBaseUnits` already returns a `number`, so the admin path has this bug today; do not copy it into the ballot, where the wrong figure is what people voted on.

**Permissions:** none. `requested_permissions` would be decorative here: the host dispatches the transfer directly, so no bridge verb authorizes it and listing `treasury.transfer` invites the reader to think one does. The ballot itself is the authority. Let `get_governance_params` raise rigor from `proposal_type: "transaction"`.

**Execute:** host calls `vault.transfer` with those three fields via `extension_async_call`, so the canister-as-caller path skips the admin `transfer.create` gate — deliberate, because the ballot is the authority, and the reason the vote-bypass paths above must die first. `drive_async_effects` (the Code Execution route to the same verb) drops `token` today — pass it through there too. Mark `executed` only when the vault result has `success` / `ok`. Otherwise `failed` plus the vault's `error_code`; never parse its English message.

A passed Transaction can still fail at execute (insufficient balance, fee, frozen account). It stays `failed` and is **not** retried automatically; re-running requires a new proposal. Do not add a "retry" button — that is the replay hole.

### Upgrade

Three subtypes in `action.target`: `codex` | `extension` | `core`.

**Codex / Extension form:** package id + version picker. List versions the way Package Manager does (`GET /api/extensions` and `/api/codices` on the file registry; realm has `list_available_codices` but no `list_available_extensions`). “Latest” is a submit-time resolve (`null` / `""` / `"latest"` → max semver); the **pinned version** is what is stored and installed.

```json
{
  "target": "extension",
  "package_id": "voting",
  "version": "1.4.0",
  "registry_canister_id": "<from realm config, not the form>"
}
```

One package per proposal. The multi-codex batch goes away with the Codex Files form; a two-package upgrade is two ballots.

**Execute:** host `yield from install_extension_from_registry(...)` / `install_codex_from_registry(...)` with that pinned version and the configured registry id. Those are async generators; the `@require` for `extension.install` / `codex.install` sits on the `main.py` wrapper, so calling the `api.file_registry` function from the dispatcher runs without a caller permission check — again deliberate (the ballot is the authority), again dependent on closing the bypass paths.

The existing multi-codex TaskManager only HTTP-upserts `Codex` rows — do not reuse it. Call the installer from the host dispatcher.

**Self-upgrade:** `package_id: "voting"` would replace the voting extension from inside its own `_do_execute_proposal`. Refuse it with `error_code: "self_upgrade_unsupported"` until there is a path that survives its own replacement.

**Core form:** visible only when `manifest_data.casals.baton_canister_id` is set. Field: existing baton `action_id` (+ optional reject). Realms has **no** wrapper over baton `list_actions` / `get_action` — add a thin query or require a pasted id. The realm cannot `install_code` on itself; `request_upgrade` is a different (registry-credit) path and is not this subtype.

```json
{
  "target": "core",
  "action_id": "<baton action id>",
  "decision": "approve"
}
```

**Execute:** host `yield from approve_orchestration_action(...)`. Check the returned `success`.

**Permissions:** none.

### Poll

Title + description are the question. Yes / no / abstain is enough.

**Frozen `action`:** `{}`

**Execute:** if status is `accepted`, set `executed`. No Codex, no sandbox, no download.

### Code Execution

Paste **or** fetch URL at submit (fetch button → store bytes + checksum immediately). Either way the bytes live on the `Codex` entity before voting starts. Execute must not re-download.

**Form:** source editor or URL+fetch (2 MB download cap, same as today’s HTTP fetch), plus a checklist of the 15 `codex_bridge.known_verbs()` — not a profile, and not Role Manager strings like `role.assign`. Default: no verbs. High-risk verbs (`treasury.transfer`, `member.assign_profile`, `member.revoke_profile`, `member.activate`) stay available but raise rigor via `get_governance_params`.

**Frozen `action`:**

```json
{
  "codex_name": "proposal_prop_001",
  "source_url": "https://… or empty"
}
```

**Permissions:** proposer-chosen, validated with `normalize_proposal_permissions` (unknown verbs dropped). Freeze the filtered list on the proposal. Voters see it read-only.

**Execute:** load `Codex[codex_name].code`, verify checksum, `execute_proposal_code(...)`. **Require `result["success"] is True`** before setting `executed`. Drive deferred effects as today.

Do not advertise “uses the `ggg` library”. The sandbox exposes `ggg_sdk` + declared verbs.

---

## Submit / vote / execute

```
submit_proposal
  → validate type + action
  → freeze metadata (backend sets permissions for typed types)
  → Code Execution: persist Codex + checksum
  → auto_start_voting (status=voting). No pending_review.
cast_vote / finalize
  → accepted | rejected | no_quorum
  → accepted → _schedule_execution → host dispatcher
```

`submit_proposal` **requires** `proposal_type`. It must **not** require `code_url` / `codices` / `code_inline`. Register `finalize_proposal` in `EXTENSION_FUNCTIONS`.

**Submit authority is per type.** One `proposal.create` gate for all four means any proposer can put a vault transfer or a core wasm approval on the ballot. Split it in `entry_access.functions` (or check inside `submit_proposal`):

| Type | Gate |
|---|---|
| `poll` | `proposal.create` |
| `code_execution` | `proposal.create` |
| `transaction` | `transfer.create` — the same authority the Vault admin path requires |
| `upgrade` (codex/extension) | `extension.install` / `codex.install` |
| `upgrade` (core) | `orchestration.approve` |

That is a gate on *proposing*, not on executing. Execution authority stays with the ballot.

**Org scope.** `submit_proposal` does not accept `org_scope` today — only Access Manager and `governed_action` set it, writing the field directly. Add it as a submit argument (department name, empty = realm-wide) so a typed proposal can be a department ballot. It decides who may vote; it does not widen what the action may do. State the scope on the ballot for every type.

**Timelock.** `notice_hours` becomes a real delay between `accepted` and dispatch for `transaction` and `upgrade`/`core`. That is the window in which a realm notices a hostile transfer. `poll` and `code_execution` dispatch immediately.

Reject:

- unknown `proposal_type`
- any `requested_permissions` on a type other than `code_execution`
- client `code_inline` / `codices` / `codex_name` on any type
- Upgrade with `version` empty or `"latest"`, or `package_id` = `"voting"`
- Core upgrade when no baton is configured
- Code Execution with empty source or missing checksum
- `amount` that is not a positive decimal string

---

## Host dispatcher

Replace `_do_execute_proposal`’s “always sandbox” body with a type switch. One place, host-side:

| `proposal_type` | Call |
|---|---|
| `poll` | set `executed` |
| `transaction` | `vault.transfer` (token, to, amount) |
| `upgrade` / `codex` | `install_codex_from_registry` |
| `upgrade` / `extension` | `install_extension_from_registry` |
| `upgrade` / `core` | `approve_orchestration_action` |
| `code_execution` | checksum + sandbox + `result["success"]` |

Any other type → `failed` + `error_code: "unknown_proposal_type"`.

The dispatcher runs only for a proposal whose `accepted` status came from a resolved ballot, and refuses any proposal already `executing` or `executed`. Do not generate Python. Do not import `core.*` from proposal source.

### How the dispatcher gets driven

Every typed executor except `poll` is an async generator. Keep `_schedule_execution` on `ic.set_timer(0, cb)` where `cb` **returns** `_do_execute_proposal(...)`. Basilisk already drives that: if the timer callback's return value has `send`, `ic_set_timer` spawns `drive_generator` on it (`basilisk/.../ic_api.rs`). Nested closures work because `resolve_timer_callback` stores the callback object in `__main__` globals, not a `__name__` lookup.

Do **not** put each proposal on TaskManager. That is for multi-step work; Transaction / Upgrade / Code Execution are one host call then a status write. TaskManager is still available later if an Upgrade needs several steps. Delete the old multi-codex TaskManager path with the Codex Files form.

`poll` needs no timer: resolve it inline where the ballot closes.

**Set the status before scheduling.** `_schedule_execution` checks nothing today, so two `cast_vote` calls arriving while the proposal is still `accepted` can each queue a timer. Move to `executing` as the compare-and-set at scheduling time, and have the dispatcher refuse anything not in `executing`.

Voting is a core extension (`CORE_EXTENSION_IDS`) running in-process with host imports, so the dispatcher can `yield from api.file_registry.…` directly. The codex install path's internal-caller check treats `ic.caller() == ic.id()` as internal, which is what makes the canister-driven install pass.

### Follow-up: the other proposal producers (separate spec)

Budget Manager, Access Manager, Role Manager, `sandbox_admin`, federal votes, and `ProposalModal` create their own structured proposals (`treasury_action`, `governed_action`, `member_action`, `position_action`, `payroll_action`, `role_assignment`, `role_revocation`, `sandbox_config`). Every one generates `from core…` replay code, so every one is broken by the same subinterpreter finding — but they are not Voting form options and each has its own payload shape.

Fixing them means deleting `build_treasury_proposal_code` / `build_extension_replay_code` and giving each producer a host-side executor keyed off the payload it already stores. That is a bigger change than this spec and should not ride along in two sentences. **Write it as its own spec**, enumerating each producer, its payload, and its executor. The dispatcher built here is the extension point they plug into.

---

## Permissions and rigor

`requested_permissions` is meaningful **only** for Code Execution, where the sandbox actually enforces it. For the other three types it would be decoration; an empty list is the honest value.

`get_governance_params` already receives `{proposal_type, requested_permissions}`. Shipped hooks ignore the verb list (Agora only special-cases `role_assignment`; Syntropia/Dominion are fixed). Update hooks so:

| Input | Floor (starting point; realm may raise) |
|---|---|
| `poll` | current defaults |
| `upgrade` (codex/extension) | at least current defaults |
| `upgrade` (core) | highest rigor + notice — this one changes the wasm |
| `transaction` | at least as strict as today’s treasury path, plus notice |
| `code_execution` + `treasury.transfer` or member-write verbs | raise quorum / threshold / notice |
| `role_assignment` (Access/Role Manager, not Voting form) | keep today’s raise |

Do not let the client pick a profile. Profiles are membership bundles; proposals name exact verbs.

---

## UI

**Submit:** type picker, then the type form. Delete the Codex Files block as the only form. Auto-start voting on submit.

**Detail:** type badge; for typed proposals a summary card (token / amount / recipient, or package + version, or baton `action_id`) **above** any code pane. Code pane only for `code_execution`. Show frozen permissions whenever the list is non-empty.

**List:** filter by `proposal_type`.

**Copy:** drop “Approved codices will be executed on the realm backend” for Upgrade (install, not `main()`).

---

## Delete

- Voting Codex-Files-only submit (no dual UI)
- `code_url` / `codices` / `code_inline` as a submit requirement
- `pending_review` as the default for these forms
- `metadata.code_inline` as the source of truth
- Generated replay that `from core… import …`
- Marking `executed` without checking the host/sandbox success field
- Any “legacy type” or “still accept old form” branch in Voting
- Demo-only `codex_amendment` and Agora `metadata.type` (`codex_change`, `treasury_spend`, …) — one field, `proposal_type`
- `approve_proposal` and `demo_approve_and_execute` — execution without a resolved ballot
- The amendment/diff view (`is_amendment`, `original`, `MonacoDiffPane`) and the Codex code-overwrite it depends on
- Multi-codex batch submit and its TaskManager steps

---

## Tests (must hit the real dispatcher)

1. Transaction → host `vault.transfer` called with frozen token/to/amount; `executed` only on vault ok.
2. Upgrade extension/codex → installer called with pinned version; `main()` not run.
3. Upgrade core → `approve_orchestration_action` with frozen `action_id`; Core hidden when no baton.
4. Poll with empty action → `executed`, no Codex, no sandbox.
5. Code Execution paste + URL-fetch: checksum at submit; execute reads Codex bytes; re-download not called.
6. Code Execution with `result["success": false]` → `failed`, not `executed`.
7. Client-supplied `requested_permissions` or `codex_name` rejected on every type but Code Execution.
8. `get_governance_params` raises floors for `treasury.transfer` on Code Execution.
9. **No execution without a resolved ballot:** a `voting` or `rejected` proposal cannot be dispatched by any entry point.
10. **No replay:** an `executed` Transaction cannot be re-dispatched, including after a forced status write.
11. Submitting `codex_name` matching an installed codex does not touch that codex's file.
12. `amount` above 2^53 survives submit → execute byte-exact.
13. `upgrade` naming `voting` is refused.
14. Timelock: a `transaction` accepted now does not dispatch before `notice_hours`.
15. A ballot that closes on a timer (not a manual update call) actually completes an Upgrade install — the generator is driven, not dropped.
16. Two `cast_vote` calls that both cross the threshold queue one execution, not two.

Do not treat mocked `execute_proposal_code` as coverage for the dispatcher.

---

## Out of scope

- Folding Budget Manager allocation into Transaction
- Fixing the other proposal producers (own spec — see above)
- Exposing raw `ggg` writes in the sandbox
- Realm self-install of wasm; upgrading the voting extension by proposal
- Dual Codex-Files + typed forms; multi-package Upgrade batches
- Re-fetching package “latest” or proposal source at execute
