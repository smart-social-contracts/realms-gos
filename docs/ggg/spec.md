# Realms GOS and the GGG model

Realms GOS implements every domain of the [GGG model](../../ggg/model/README.md): its entities,
operations, lifecycles, invariants and permissions. The model lives in the
[`ggg`](https://github.com/smart-social-contracts/ggg) repository, checked out here as the `ggg`
submodule, and is the same for every GOS.

This page holds only what Realms GOS adds to the model and how it binds it. Look up an entity, an
operation or a rule in the model first.

When the model, this page and the code disagree, the code is wrong.

## 1. How Realms GOS binds the model

| Part of the model | In Realms GOS |
|---|---|
| Entities | the [`ggg/`](../../src/realm_backend/ggg) package, one class per entity, holding data only: behaviour lives in operations |
| Operations | `@op` functions in [`api/`](../../src/realm_backend/api), registered by name with their permission and flags |
| Authorization (I-ALL-1) | Cedar decides every call |
| Guardrails (I-ALL-2) | the `GUARDRAILS` Cedar policies in [`core/auth.py`](../../src/realm_backend/core/auth.py) |
| Private content (§13) | vetKeys, with the [`api/crypto.py`](../../src/realm_backend/api/crypto.py) operations |
| Signing policy (§11) | not read yet: Realms GOS applies the model's defaults. A Cedar guardrail refuses `signing` operations to agents, and no delegation covers a `signing` permission |

The canister exposes every operation through two methods: `read(name, args)` for `read` operations
(queries) and `run(name, args)` for the rest. Both take and return JSON text. Results are
`{"ok": true, ...}` or `{"ok": false, "error": "..."}`, and `ops_list` lists every operation with its
permission, flags and description.

It also implements [`ggg.did`](../../ggg/ggg.did): every method, as a typed call to the same
operation ([the `ggg.did` methods](README.md#the-gggdid-methods)). Both interfaces read and write the
same rows.

`read` and `run` predate the model's Internet Computer conventions (decision D6), and keep them:

- times are integer seconds, with `0` for "not set";
- principals are text;
- lists and batches (tax assessment, invoice refresh, marking notifications read, document deletion)
  take `from` and `count` and return `next`;
- statuses are lower-case text;
- references are `Kind:id`, or a bare id for an organization of that name, or else a user;
- a proposal's action is `{op, args}`, an operation name and its JSON arguments;
- proposal thresholds and quorums, allocation shares and tender rubric weights are whole
  percentages, not basis points, and `governance_update` takes `voting_window` in seconds and
  `threshold` and `quorum_percent` in percent, kept outside the `Realm` row;
- a tender's rubric, an allocation rule, a fund's balances, credential fields and wrapped keys are
  maps where `ggg.did` has lists of pairs;
- a limit in time may be given as `expires_in` seconds instead of `expires_at`, and a delegation's
  permissions are `operations` where the model has `scope`;
- `mandate_create` takes the approving `proposal` as an argument, and a mandate's limit is
  `max_value` inside its `scope`;
- governed operations reply with the operation's own result or the filed proposal, not `Governed`;
- vetKD public keys, transport keys, wrapped keys and private data are hex text where `ggg.did` has
  `blob`.

Where the two interfaces cannot say the same thing, the `ggg.did` methods do this:

- A `Call` to an operation that has no `ggg_` method keeps the operation's name, and its arguments
  are JSON, not Candid. Codex hooks and extensions can file such proposals.
- ICRC-3 values have no null or boolean. A `null` crosses as an empty map, and a boolean as `Nat` 0
  or 1.
- The treasury epoch and a position's salary period are named (`monthly`, `quarterly`, `yearly`).
  `ggg.did` reads them as 30, 91 and 365 days.
- A proposal's `result` is the replayed operation's JSON reply, in `Reply`.
- A federal vote proposed on a quarter lives on the capital. The quarter replies with what it knows,
  with an empty rule and tally.
- The operations' refusals are text. A refusal the policy makes is `Forbidden` or `Unauthenticated`.
  Otherwise the words decide:
  - "unknown …" is `NotFound`;
  - a refusal because of a record's status is `Conflict`;
  - one because of who is asking is `Forbidden`;
  - anything else is `InvalidArgument`.
- `ggg_user_get` always answers callers about themselves.

Realms GOS also differs from the model in what it keeps:

- `Event` does not yet record whether the caller acted as an agent, nor its supervisor (I-ALL-7);
- `realm_configure` and its variants also take Realms GOS settings (`ai_assistant_enabled`,
  `test_mode`, `primary_color`, token and NFT canister ids), which `ggg.did` would carry as
  `realms_gos_` fields, and setup has `install` and `infrastructure` steps of its own
  (`realm_configure_infrastructure` is a Realms GOS operation, not GGG);
- a federal vote's title is kept inside its action;
- only the last 2,000 events are kept, and older ones are deleted rather than archived (I-EXT-4).

## 2. Fields Realms GOS adds to model entities

`UserProfile.extensions` and `Department.extensions` (→ `Extension`*) say which extensions a
profile or a department sees.

Indexed lookup keys, kept so that no lookup scans a collection: `Delegation.link` (grantor and
delegate while pending or active), `Vote.proposal_id`, `Budget.slot` (`fund:period:category`),
`Land.cell` (`x//16:y//16`) and `TaxDeclaration.key` (declarant, base and period). `KeyEnvelope.scope`,
`KeyEnvelope.principal` and `Transfer.payroll` are indexed too, and a `KeyEnvelope`'s id is
`scope|principal`. Totals such as fund balances and period statements are kept as running totals.

Next to the model's fields, the `Realm` row holds:

- `manifest_data`, the realm manifest it was created from;
- canisters: `principal_id`, `frontend_canister_id`, `token_canister_id`, `nft_canister_id`,
  `file_registry_canister_id`, `marketplace_canister_id`, `installer_canister_id`,
  `shared_tokens_json`;
- scaling and sync: `auto_scale_enabled`, `scale_in_flight`, `scale_requested_at`,
  `bootstrap_state`, `sync_state`, `codex_install_state`, `last_activity_at`;
- `ai_assistant_enabled`;
- test flags: `can_test_mode`, `test_mode` and the `test_mode_*` switches.

## 3. Entities Realms GOS adds

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Task`, `TaskStep`, `TaskSchedule`, `TaskExecution`, `Call` | as in `ic-basilisk-toolkit` | Scheduled and multi-step automation. | Monthly payroll on the 1st; last run succeeded |
| `Extension` | `id` (name), `version`, `status`, `operations` (Json: the only operations its frontend and backend may call), `manifest` (Json), `source` (its backend, when the manifest sets `backend`), `access` (Json: profiles and departments), `sidebar` (Json) | A feature package with its own frontend and, optionally, backend code. | `voting` 2.1.0, for `[member]`, in the Governance menu |

Extensions are callers of their own (see the model's "Callers"): an extension calls operations
only as the user using it, and only those its manifest declares.

## 4. Codices and extensions

A codex is a `manifest.json` (`codex_api_version` 2, `hooks`, `capabilities`, configuration) and one
hooks module, run in a fresh sandbox per call. A hook reads its context (the codex configuration, the
realm, the time) and the read operations it declares, and returns `(op, args)` effects that the realm
applies as itself with origin `codex`; Cedar refuses the undeclared ones. Hooks: `init` (after
install), `on_user_register` `{user}`, `on_stage_change` `{stage}`, `assign_quarter`
`{user, preferred, quarters}`.

The trust policy that I-EXT-3 sets for codex code applies to extension code too. Code is accepted
under `require_marketplace_approval` when its checksum is pinned in
`trusted_approvers`, or when the file registry reports it approved by a trusted approver (by default
the realm's marketplace).

Credential schemas are in the active codex's manifest as
`credentials: {schema: {license, positions, collection, transferable, public, after_license}}`:
`positions` lists accepted position ids or titles, `public` the only extra keys allowed on the token,
and `after_license` (`keep` or `void`) whether credentials stand once their licence is gone (I-AST-3).
`credential_verify` takes the token's header and frozen flag as read from the ledger.

## 5. Operations Realms GOS adds

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `task_create`, `task_edit`, `task_delete`, `task_run`, `task_schedule`, `task_cancel` (only the task's creator, or a controller; a cancelled task stays cancelled) | `task.*` | | Schedule a daily invoice check |
| `task_list`, `task_logs` | `task.view` | read | Payroll task and its last 3 runs |
| `ops_list` | — (public) | read | geister reads it to build MCP tools |
| `orchestration_decide` (approve or reject a Casals baton action for this realm) | `orchestration.approve` | | Approve Casals' action "upgrade North to 0.9.1" |
| `extension_install`, `extension_uninstall` | `extension.install`, `extension.uninstall` | governed | Install `procurement` 1.0.3 (marketplace-approved) → vote |
| `extension_call` | `extension.call` + the extension's declared operation | | The voting extension casts Alice's vote through `proposal_vote` |
| `extension_set_access`, `sidebar_configure` | `realm.configure` | governed | Show `vault` only to Finance |
| `extension_list` | `realm.data_view` | read | Alice's sidebar shows `voting`, not `vault` |
| `shell` (REPL; calls operations as the caller) | `shell.execute` | | An admin runs `ops.proposal_list({"status": "voting"})` |
| `status`, `version` | — (public) | read | Thin summaries of `realm_info` |
| `token_catalog` | — (public) | read | The ICRC-1 ledgers this GOS ships with, not the realm's registered tokens |
| `scale_status` | — (public) | read | Quarter populations against this GOS's auto-scale capacity |
| `realm_configure_infrastructure` | `realm.configure.infrastructure` | governed | Frontend, file-registry, marketplace, installer and Casals baton canister ids |

Permissions these add: `extension.install`, `extension.uninstall`, `extension.call`, `task.create`, `task.edit`, `task.delete`, `task.run`, `task.schedule`,
`task.cancel`, `task.view`, `orchestration.approve`, `shell.execute`.

## 6. Invariants Realms GOS adds

- I-ADM-1: Test flags can be enabled only on non-production networks or when `can_test_mode` was set
  at install.
- I-EXT-1: An extension can call only the operations its manifest declares, always as the calling
  user, never with more permissions than that user.

## 7. History

What the model replaced in earlier versions of Realms GOS:

- Entities: `RegistrationCode` → `Invite`; `DepartmentAuthority` → `Authority`; `TreasuryConfig` →
  `Treasury.config`; `Instrument` → `Token`/`NFTToken`; `Rfp` → `Tender`; `EntityMigration` →
  `Forward`; `MenuItemConfig`, `MenuCategoryConfig`, `MenuDepartmentVisibility` → `Extension.sidebar`;
  `MarketPlace` → `Realm.marketplace_canister_id`; `BidPayload` merged into `Bid`.
- Removed entities: `Balance` (the ledgers are the source of truth), `Service` (services are provided
  by licensed organizations), `JusticeSystem` (courts stand on their own), `QuarterResident` (no
  central user directory), `RfpTransition` (the `Event` log records transitions), `VendorRecord`
  (derived from awarded tenders).
- New entities: `Event`, `TaxDeclaration`.
- New permissions: `self.delegate`, `tax.manage`, `tax.assess`, `tax.declare`, `credential.sign`,
  `license.apply`, `tender.manage`, `quarter.sync`.
- Removed permissions: `realm.admin` (same as `all`), `extension.sync_call` and
  `extension.async_call` (one `extension.call`), `db.manage` (no raw database writes), `demo.manage`
  (no demo code in the canister), `realm.configure.codex` (→ `codex.install`), `scope.authorize`,
  `permission.view` and `permission.revoke` (viewing and revoking roles is covered by `user.view`
  and `role.revoke`).

## 8. Decisions

| # | Decision | Outcome |
|---|---|---|
| D5 | Keep the `Task*` entities in `ic-basilisk-toolkit`, shared with Casals | Accepted |

Decisions D1 to D4 and D6 onwards concern the model and are recorded there. D8 is why extensions
are on this page and not in the model.
