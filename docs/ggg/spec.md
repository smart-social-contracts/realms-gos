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
| Personal data in clear (I-PRV-1) | an attribute's `kind` and `value` are read by `attribute_list` (the person's own) and by the reviewer's `attribute_review` reply; no operation lists another person's, and `realm_export` of `Attribute` takes `attribute.review` besides `db.export`. An `@op`'s `private` arguments are left out of the audit log (I-ALL-5): an attribute's kind and value, a request's `body` and `reply`, a share's wrapped `keys` |
| Signing policy (§11) | not read yet: Realms GOS applies the model's defaults. A Cedar guardrail refuses `signing` operations to agents, and no delegation covers a `signing` permission |

The canister exposes every operation through two methods: `read(name, args)` for `read` operations
(queries) and `run(name, args)` for the rest. Both take and return JSON text. Results are
`{"ok": true, ...}` or `{"ok": false, "error": "...", "kind": "..."}`, and `ops_list` lists every
operation with its permission, flags and description.

A refusal's `kind` is the model's error in snake case: `invalid_argument`, `not_found`, `forbidden`,
`unauthenticated`, `conflict` or `unavailable`. A refusal by the permission check adds
`"denied": true` and the `permission` the caller lacks, when holding it would be enough. An operation
says which kind it means by the exception it raises (`Fail` is a bad argument, `NotFound`,
`Forbidden`, `Conflict` and `Unavailable` the others, in
[`core/db.py`](../../src/realm_backend/core/db.py)).

An operation's reply holds the model's result under the key its `@op` names with `returns`: the
record's id (`"dispute"`), a page's list (`"disputes"`, with `next`), a value, or for a governed
operation the id of the record it created. Without `returns`, the whole reply is the result.

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
- every ratio is on GGG's scale, an integer in hundred-millionths (100 % is 100 000 000): proposal thresholds
  and quorums, department quorums, allocation shares, tender rubric weights and tax rates (`rate`). The
  operations name them as GGG does, without a unit: `governance_update` takes `voting_window` in seconds
  and `voting_threshold` and `voting_quorum`, kept outside the `Realm` row; a `voting_window` of
  0 leaves a realm-wide proposal open until its outcome is certain. An argument still sent under its
  old name (`threshold`, `quorum_percent`, `rate_bp`) is refused, naming the new one;
- every number an operation takes is in the range `ggg/facts.json` gives it (`Ratio` 0 to 100 000 000, `Rate` 0 to
  10 000 000 000, `Money` 0 to 2^53 − 1, `SignedMoney` ± that, `Period.count` 0 to 1 000 000, a plain number to the
  Cedar Long maximum), or the call is refused with `InvalidArgument` before anything is written. The bounds are
  `src/realm_backend/ggg_ranges.py`, generated with the other `ggg.did` files, and a time is bounded in seconds.
  A text of an integer counts as the number. Two things follow from GGG's ranges: land coordinates are not negative,
  and an amount of a token with many decimals is limited to 2^53 − 1 of its smallest unit. Not bounded here: the maps
  that stand for lists of pairs (a tender's rubric weights, bid scores), `expires_in`, which only Realms GOS has, a
  number inside a variant (except the tokens step of `setup_step`), a derived fact, and `limit`, which is `count` and
  is capped by the page size;
- a proposal stores the rule that decides it (I-GOV-3): `kind`, `matter`, `threshold`, `quorum` and `threshold_of`
  (`cast` or `eligible`, lower case) are arguments of `proposal_create`, a stricter rule than the scope's default is
  stored and a weaker one refused. The default is the realm's `voting_threshold` and `voting_quorum` over the votes
  cast for a realm-wide proposal, m/n of the policy (rounded down) with the policy's quorum for a department's; a governed
  call's proposal takes it. A department's threshold is a share of the policy's `n` seats, kept when the vote opens in
  `tally.seats`, whoever sits in them: the default needs exactly `m` yes votes, a stricter `t` needs ceil(`t` · `n`), a
  direct policy needs one, and a vacancy never makes a vote easier (a departure from the model, which words it over the
  eligible seats). The electorate is counted when the vote opens, in `tally.eligible`, for the quorum and for the end
  when everyone has voted. The realm's casting vote is set with `governance_update` (`casting_vote`: the principal of an active
  member or `""`, as `department_edit` names a head, kept with the other voting rules, outside the `Realm` row) and copied onto a realm-wide proposal when it is
  filed; a department's proposals have none;
- a proposal settles itself: the deciding vote, the last vote of every eligible member, or its deadline
  closes it and runs its action at once; `proposal_finalize` does the same for a deadline missed, and
  `proposal_execute` stays for a scheduled proposal's time and for GGG;
- a proposal with an `effective_at` (whole seconds, not in the past) runs its action then, not at the vote. While it waits,
  its status is `execution_scheduled`, and a one-step task of the realm's task scheduler runs it, which
  the scheduler arms again after an upgrade. A vote ends at its deadline, or earlier the moment every
  eligible member has voted (I-GOV-7). A department's vote also ends as soon as its yes votes reach its stored
  threshold of the policy's `n` seats (m approvals under the policy's own rule), every veto holder who can still vote has voted (none against)
  and its quorum is met. Only an acceptance ends it early: until
  its deadline, or until everyone has voted, a no can be replaced. Realms GOS has a rule of its own besides: a
  realm-wide vote in a realm with no voting window ends as soon as its outcome is certain, while a department's vote
  there, if it can't pass on the votes cast so far, stays open until it passes, everyone has voted or its proposer
  withdraws it. After the vote ends no vote is accepted or replaced; when it is closed does not matter, so a timer
  that fires late, an upgrade or a late `proposal_finalize` never moves the end. A vote that ends after `effective_at`
  leaves the proposal `expired`, and nothing runs. One that ended in time but is closed after `effective_at`, by the
  timer, an upgrade or `proposal_finalize`, is accepted and its action runs at once. `proposal_stop` ends a waiting
  proposal as `withdrawn`, and `ggg_proposal_stop` replies `Done` without an id when it applies at once, `Proposed`
  with the new proposal's id under a voting policy. Proposals due at the same second run in no set order;
- a proposal's `links` are `{kind, proposal}` with the kind in lower-case text (`replaces`, `relates_to`) where `ggg.did` has
  a variant; `proposal_linked_from` is the paged read of the links *to* a proposal, `proposal_get` shows `replaced_by`,
  and `ProposalEdge` rows (one per link, indexed by the proposal they point to) are what it pages;
- `Realm.timezone` is `UTC` until the realm sets it with `realm_configure` (or the `realm` setup step). It must be
  `UTC` or a zone of the tz database the release bundles (`src/realm_backend/zonedata`, written by
  `scripts/build_zones.py`), else `InvalidArgument`. The realm reads its own zone's file when it is set and
  after an upgrade, never in a query; a change applies from that moment and rewrites nothing;
- a procedure's `decision_deadline` is `{count, unit}` with the unit in lower-case text (`day`, `working_day`,
  `month`, `year`), and its `silence` is `positive` or `negative`;
- a tender's rubric, a fund's balances, credential fields and wrapped keys are maps where `ggg.did` has
  lists of pairs; an allocation rule is the list of `{fund, share}` that `ggg.did` has;
- a limit in time may be given as `expires_in` seconds instead of `expires_at`, and a delegation's
  permissions are `operations` where the model has `scope`;
- `mandate_create` takes the approving `proposal` as an argument, and a mandate's limit is
  `max_value` inside its `scope`;
- governed operations reply with the operation's own result or the filed proposal, not `Governed`;
- a tax policy's `taxes` are `TaxEntry` maps whose `subject` is `User` (every active member), `Land` (the owners of
  active parcels) or `Declaration` (what the taxpayer declares: `tax_declare` bills it, `tax_assess` does not). An
  entry charges by its `amount`, its `rate`, or the `brackets` of its `facts` (each an `up_to` and a `rate`, the
  last open), at most one of them; its other settings are facts too: `unit_value` (land), `exempt_below`,
  `exempt_profiles` (users), `due_days` and `enabled`. `tax_assess` bills one invoice for each taxpayer, entry and
  period, with the items the active codex computes for each of their subjects (`compute_tax_assess`, which gets the
  view of the subject's row, a parcel's `assessed_value` a `Money` in the accounting currency's decimals: a tax
  billed in another token is not computed by a codex, whatever its subject, and a codex that answers for it refuses the
  call) or, when no
  codex answers, one item from the entry's own charge, rounded to the cent as GGG rounds (the brackets of a
  progressive tax are added up exactly and the total rounded once). A codex answer that is not invoice items, that
  comes to less than nothing or more than an amount of money, or that covers some of a payer's subjects and not the
  others, refuses the call before anyone is billed;
- an invoice's `items` are lines with a `concept` and a signed `amount`, optionally a `quantity`, a `unit` and the
  `legislation` that charges it; they sum to the invoice's `amount`, and `invoice_item_add` adds a line to a pending
  invoice. The invoice of a fine or a restitution has one item, named for the kind of penalty;
- a budget line is `revenue` or `expense`, a category is one kind within a fund and period (`budget_draft` refuses a
  line of the other kind with `InvalidArgument`), and `budget_propose` takes the draft lines of a period together: those
  of the `fund` it names, which its owner governs under its own policy (the root when no department owns the fund), or
  every fund's, which the root governs (GGG D19). The pinned `ggg.did` has no `fund` in `BudgetProposeArgs` yet, so
  `ggg_budget_propose` proposes every fund's lines; the operation takes it. The lines are fixed when the proposal is
  filed, by the governed call or by `proposal_create`: they are `proposed`, with the proposal, a later `budget_propose`
  takes only the draft lines left (`Conflict` when there are none), and the vote adopts exactly those, in the order
  they were drafted, so a later line of a fund, period and category amends an earlier one. A proposal that ends without
  adopting them (`rejected`, `expired`, `withdrawn`, stopped or `failed`) gives them back as `draft`, with no proposal.
  A department that decides alone files no proposal and adopts the draft lines at once. `years` is a fact for
  legislation and is not kept. `budget_extend` copies a period's budget as it stood at the end of the
  period before into the period as `extended` lines, for each fund that has no extended, proposed or adopted line
  in it yet (`Conflict` when none is left): of each fund and category the newest line that was adopted or extended
  (or `closed` with the period), never a line a newer one replaced. An `extended` line stands in for an adopted one:
  it caps spending, `budget_view` lists it and `period_close` closes it. A proposal for the period replaces
  (`amended`) every extended line of each fund whose lines it adopts, whether it names the line's category or not,
  and leaves the other funds' extensions; `closed` means closed with its period, so a category an adopted budget
  drops does not come back at the next extension. An adopted budget is exhaustive: once a fund has one for the
  period, spending in a category it has no expense line for is refused, while a fund with only extended lines, or
  none, spends as before. Only an expense line caps spending, and its actual counts expense only; a revenue line
  caps nothing, yet a fund whose adopted lines are all revenue has an adopted budget, so it refuses all spending
  but the ledger's fee. A payment with no category is in the category `transfer` and a payroll in `payroll`, so each needs
  its line like any other. The ledger's fee on a payment is a mechanical cost of it, not a spending decision, and
  needs none: the realm books it itself under the category `ledger_fee`, which `transfer_create` and `budget_draft`
  refuse a caller to name (`InvalidArgument`);
- a loan's `term` is a `Period` with the unit in lower-case text (`day`, `working_day`, `month`, `year`), its `lender`
  an `External` the realm knows (`NotFound` otherwise), and `Treasury.debt_outstanding` is kept as a running total,
  like fund balances;
- `codex_activate` makes the pending layered codex, named by its id (its checksum), the active one at once and keeps
  the codex it replaces as `previous`; `codex_status` shows the pending one as `pending`. An `at` in the future is
  refused until activations can be scheduled;
- vetKD public keys, transport keys, wrapped keys and private data are hex text where `ggg.did` has
  `blob`;
- a dispatch's outcome is `{"Confirmed": {"at", "receipt"}}` or `{"Failed": reason}`; its status is `queued`,
  `confirmed` or `failed`, with `outcome_at`, `receipt` and `failure` beside it. `external_add` is refused for the id
  `realm`, which `tender_notice` uses for the realm's own journal, and a `channel` is `manual` or an `https://` URL.
  `notification_send` to an `External` queues a dispatch (its `body` is `{"text": body}`) and answers `dispatch` where it answers `notification` to
  anyone else; its `translations` are kept with a notification, and `notification_list` shows each reader the one in
  their `User.language` (the same tag, else the same primary language), else the title and body sent. A dispatch to an
  outside body carries no translations. `document_publish` with an outside journal in `to` queues one dispatch
  (`topic` `publication`, `body` the clear copy's `ref` and `hash`) and adds a `Publication` to the document that its
  confirmation fills; a second call queues again only when the first dispatch failed. `external_report` is audited as
  its `Event`, whose arguments the audit log keeps up to `AUDIT_ARGS_MAX` (2000) characters of JSON: a longer report is
  refused, and a long body belongs in the file its `evidence_ref` names;
- GGG's `Task` is a task of the realm's scheduler (`ic-basilisk-toolkit`'s) with one step and one schedule that does not
  repeat: `task_schedule` makes it, named `TASK-<n>`, and it runs once, through `dispatch` with origin `task`, as the
  user who scheduled it. Only a user calling for themselves schedules: a codex, an extension, the replay of a proposal
  or another task is refused with `InvalidArgument`, as is an operation that only reads. A task that is refused ends
  `failed` (it has no event); one that ran keeps the id of the operation's own `Event` (`event`). Its `scheduled_by` is
  `User:<principal>`, or `Realm:1` for a task the realm scheduled itself, such as a proposal's run. `task_list` shows
  every task of the scheduler as a `Task` made of its first step and first schedule, with the step's arguments as the
  audit log keeps them (without the secrets and the operation's private arguments), and filters by `op` and by `status`
  (`scheduled`, `run`, `failed`, `cancelled`). `task_cancel` of a task `task_schedule` made that already ran or failed
  is `Conflict`; any other task cancels. A task with several steps, or a repeat, is made with `task_create` and
  `task_add_schedule`;
- a tender's `kind`, `procedure` and `estimated_value` are optional on `tender_create`; `tender_notice` appends a
  notice to the tender in the order recorded, and the derived fact `published_in` of `tender_award` is the journals of
  its notices, each once. A contract made from a tender (`contract_create` with `tender`, or `tender_award`) takes its
  kind and procedure, and `contract_create` refuses others.

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
- `ggg_user_get` always answers callers about themselves;
- `document_get` is public. For a published document it answers anyone with the record of its public copy:
  the title, kind, languages, `public` and `publications`, but not the ciphertext `ref` and `hash` nor the
  `facts` (I-PRV-4). Staff and key holders get the whole record, and for a document not published anyone else
  is refused. `ggg_document_get` does the same: the `Document` record conceals those three fields for any reader who
  is neither staff nor a key holder, whichever reply carries it;
- `License.decision_due` is set when the application is made, from the `Procedure` of its kind, as the end of the
  last day of the deadline on the realm's clock (`localtime.add_period`): that day counts in full, so positive silence
  applies from the next. A deadline in `WorkingDay`s needs the calendar of the layers the realm runs; a realm with none
  refuses the application with `Unavailable`;
- a request's `body` and `reply`, and a dispatch's `body`, are not checked for personal data, which the realm
  cannot see: whoever files or decides puts personal data in an encrypted `Document` and names its id there;
- `request_get` answers `NotFound` for a request that is not the caller's, as for one that does not exist.

Realms GOS also differs from the model in what it keeps:

- `Event` does not yet record whether the caller acted as an agent, nor its supervisor (I-ALL-7);
- `realm_configure` and its variants also take Realms GOS settings (`ai_assistant_enabled`,
  `test_mode`, `primary_color`, token and NFT canister ids), which `ggg.did` would carry as
  `realms_gos_` fields, and setup has `install` and `infrastructure` steps of its own
  (`realm_configure_infrastructure` is a Realms GOS operation, not GGG). They do what GGG's
  `host_install` and `host_infrastructure` do, which run the same code; the GaaS installer still
  calls the steps. The host is a controller or the installer the canister was created with, and
  the marketplace, installer and Casals baton canisters go in `extra` as `realms_gos:marketplace`,
  `realms_gos:installer` and `realms_gos:baton`. `host_install`'s `registry` is kept for
  `realm_register`;
- a federal vote's title is kept inside its action;
- calls between realms (a link's notices and reports, and the reads of the check for a cycle) are not bounded-wait:
  basilisk 0.15 has no call with a timeout, so a canister that never answers holds the message of the vote that
  called it open, and an open call blocks stopping the realm for an upgrade. The model asks for bounded-wait
  calls (§10); until basilisk has them, a vote names only realms its members trust. A call that is rejected, a
  stopped realm among them, is handled: the vote's notice is sent again, and a check that cannot read a realm is
  `Unavailable`;
- the check for a cycle (I-NEST-2) runs when the vote applies, not when a proposal that carries it is filed: filing
  makes no call to another realm. A vote refused at the end of the vote leaves the proposal `failed`;
- `Quarter.compliance` is written by the capital when a quarter reports the layered codex it runs, and a minute
  after the capital switches codex (§9.5 of the layered codex); it reads `Unknown` until a quarter has reported one;
- only the last 2,000 events are kept, and older ones are deleted rather than archived (I-EXT-4).

A realm created before attributes and requests existed keeps the `member` profile it stored, which lacks
`attribute.submit` and `request.file`: its admins add both with `profile_edit` (`profile: member`, `allowed_to` the
current list plus those two), or its members cannot submit attributes or file requests.

## 2. Fields Realms GOS adds to model entities

`UserProfile.extensions` and `Department.extensions` (→ `Extension`*) say which extensions a
profile or a department sees.

`Zone.defined` tells a zone of the realm (`zone_define`, which members name as their `home_zone`) from a member's
home cell, and `Zone.population` is a running total of the members whose `home_zone` it is, kept by joining,
`user_edit` and `user_remove`. A member's home cell is private, like `Human.h3_index`: a layered codex's chapters see only the zones
the realm defined (`defined`), never a home cell, and read a member's district as `User.home_zone`. `zone_set` and
`zone_clear`, which take the cell, keep it from the legislation and the audit log (`secret=`).

Indexed lookup keys, kept so that no lookup scans a collection: `Attribute.human`, `Delegation.link` (grantor and
delegate while pending or active), `Dispatch.to` and `Dispatch.status`, `Vote.proposal_id`, `Budget.slot` (`fund:period:category`),
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
| `Task`, `TaskStep`, `TaskSchedule`, `TaskExecution`, `Call` | as in `ic-basilisk-toolkit` | Scheduled and multi-step automation. GGG's `Task` is one of these with a single step and a single schedule (see §1). | Monthly payroll on the 1st; last run succeeded |
| `Extension` | `id` (name), `version`, `status`, `operations` (Json: the only operations its frontend and backend may call), `manifest` (Json), `source` (its backend, when the manifest sets `backend`), `access` (Json: profiles and departments), `sidebar` (Json) | A feature package with its own frontend and, optionally, backend code. | `voting` 2.1.0, for `[member]`, in the Governance menu |
| `LayeredCodex` | `id` (`sha256:` and the checksum of its layers, layer paths and chapter checksums), `layers`, `layer_paths`, `chapters` (Json: chapter id → source, version, sha256, vouched_by, route, fetched_at, url, size), `previous` (a codex of either kind), `status` (`pending`, `active`, `inactive`, `reverted`), `activates_at`, `installed_at`, `proposal` | A realm's legislation built from the chapters of several sources ([layered codex](../codices/layered-codex.md) §9.1). Active in place of a `Codex`, never beside one. | `sha256:4be1…`, active, tax path EU → Spain → CV |
| `ChapterSource` | `sha256` (key), `source` | The text of a chapter or calendar, stored once under its checksum; at most 256 KB. | the text of `eu/es/tax` |
| `SyncSource`, `Follower` | `id` (a realm's capital, or a stand-in record's url), `status` (`requested`, `active`, `ended`), `ended_by`, `ended_at`, `unsent`, `checking`; a sync source also `name` and the record's `checksum`, a follower also `layers`, `compliance`, `population`, `reported_at` | The two sides of a link between realms (§8 of the [layered codex](../codices/layered-codex.md)): `Realm.sync_sources` and GGG's `Follower`. | CV's link to València: `active`; València's last report: compliant |
| `Release` | `id` (`<source>@<version>`), `source`, `version`, `relayed`, `rank`, `published`, `effective_from`, `commit`, `chapters` (domain → sha256), `calendar`, `notes` | One release in the realm's published record: its own, or one it relays from above, as the record that vouches for it lists it. | `eu/es@2.4.0`, relayed, rank 2 |
| `InstallJob` | `id`, `proposal`, `change_list` (`{url, checksum}`), `step`, `staged`, `status` (`running`, `paused`, `failed`, `done`), `error`, `started_at`, `finished_at` | One install of a layered codex. At most one job is `running` or `paused`. | `J-1`, running, fetching chapters |

Extensions are callers of their own (see the model's "Callers"): an extension calls operations
only as the user using it (as the delegate for the user, when a delegate calls it), and only those its manifest declares.

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

Flags are the model's (`ggg/model/README.md`, §1): `delegable` is the direct tier of power of attorney and `confirm` asks
each time. These operations get their tier and group in `core/poa.py` (`OWN`), as the model's get theirs in
`ggg/delegation.json`. An operation with no flag is the member's own, the founder's or needs no power of attorney.

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `task_create`, `task_edit`, `task_delete`, `task_run` (only the task's creator, or a controller; a cancelled task stays cancelled; under a grant, only the tasks made under that grant, whose steps run as the delegate and stop when the grant ends) | `task.*` | delegable | Create a daily invoice check |
| `task_add_schedule` (a time, and optionally a repeat, for the creator's named task) | `task.schedule` | delegable | Run the invoice check every day from 1 Jan |
| `task_logs` | `task.view` | read, delegable | Payroll task and its last 3 runs |
| `proposal_run_scheduled` (runs the action of a proposal waiting for its effective time once that time has come, else does nothing; the task scheduler calls it) | — (public) | | At `effective_at`, the realm runs `P-12`'s transfer |
| `codex_switch_staged` (a quarter switches to the codex it staged from its capital once the capital's switch time has come, else does nothing; the task scheduler calls it) | — (public) | | At `effective_at`, each quarter switches to the codex the realm voted for |
| `ops_list` | — (public) | read | geister reads it to build MCP tools |
| `orchestration_decide` (approve or reject a Casals baton action for this realm) | `orchestration.approve` | confirm | Approve Casals' action "upgrade North to 0.9.1" |
| `extension_install`, `extension_uninstall` | `extension.install`, `extension.uninstall` | governed, delegable | Install `procurement` 1.0.3 (marketplace-approved) → vote |
| `extension_call` | `extension.call` + the extension's declared operation | delegable (each operation it calls is held to the grant) | The voting extension casts Alice's vote through `proposal_vote` |
| `extension_set_access`, `sidebar_configure` | `realm.configure` | governed, delegable | Show `vault` only to Finance |
| `extension_list` | — (public) | read | Alice's sidebar shows `voting`, not `vault`; a visitor's shows the public dashboard |
| `shell` (REPL; calls operations as the caller) | `shell.execute` | delegable | An admin runs `ops.proposal_list({"status": "voting"})` |
| `status`, `version` | — (public) | read | Thin summaries of `realm_info` |
| `token_catalog` | — (public) | read | The ICRC-1 ledgers this GOS ships with, not the realm's registered tokens |
| `scale_status` | — (public) | read | Quarter populations against this GOS's auto-scale capacity |
| `quarter_add` (on the capital: Casals grows the realm's stand by <stand>-quarter-<n>, then the capital makes that canister a quarter and registers it; filed by the realm itself once a quarter is wanted) | `quarter.register` | governed, delegable | The members vote for a fourth quarter → Casals builds `agora-quarter-4` |
| `realm_configure_infrastructure` | `realm.configure.infrastructure` | governed, delegable | Frontend, file-registry, marketplace, installer and Casals baton canister ids |
| `realm_export` | `db.export` | read, delegable | One page of an entity's rows, as stored, for a backup. An entity with large rows (`ChapterSource`: up to 256 KB each) returns fewer rows than `count` asks for: follow `next` |
| `codex_install_resume` (an admin resumes the install of a layered codex that an upgrade paused: the step it stopped in runs again, for the change list the vote approved; nothing else can change) | `codex.install` | delegable | After an upgrade in the middle of an install, the admin resumes `J-3` |
| `db_convert_indexes` | `db.convert` | | After an upgrade from a release before ic-python-db 0.13: converts the index lists saved as one array each, a batch per call, until `done` (a list too long for one call is skipped and named in `refused`); a new realm, or one already converted, does nothing |
| `tax_policy_view` | `realm.data_view` | read, delegable | A member reads the tax policy they are taxed under (with its currency), the newest drafts, and their own declarations |
| `budget_view` | `realm.data_view` | read, delegable | A member sees each fund's balances, allocation rule and, for one fiscal period (default: now), its budgets in force, adopted or extended |

Permissions these add: `extension.install`, `extension.uninstall`, `extension.call`, `task.create`, `task.edit`, `task.delete`, `task.run`, `task.view`,
`orchestration.approve`, `shell.execute`. (`task.schedule` is the model's; `task.cancel` is no longer used: `task_cancel`
needs `task.schedule`, and `task_list` needs `realm.monitor`.)

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
- A fund that has an adopted budget refuses spending in a category it has no expense line for: the vault's and the
  voting extension's payments (category `transfer`) and a codex's payroll (`payroll`) need a line in such a fund,
  where they needed none before.
- A budget proposal still being voted on at the upgrade was filed when its lines were plain drafts: nothing ties them
  to it. If it is accepted it fails with `Conflict` ("fixed no budget line") and adopts nothing, rather than adopting
  every draft of the period, some of which nobody voted on; the drafts wait for a new proposal. If it ends otherwise
  it frees nothing, and never the lines of a newer proposal. A federal vote that carries `budget_propose` fails the same
  way, for its local leg files no lines.
- A budget line whose `kind` the upgrade turned into `expense` may share its fund, period and category with a
  `revenue` line, which the model now forbids. The upgrade leaves both rows; `budget_extend` carries only the newer of
  the two into the next period (a slot holds one line), and skips a line whose category the new period already holds
  as the other kind, even in a draft.
- A tax policy kept its taxes in `document.taxes`, each with a `base` and a `method`; they are `TaxPolicy.taxes`,
  typed entries (a flat tax is a tax on `User`, a land tax one on `Land`, a declared tax one on `Declaration`;
  a fixed tax is an `amount`, a proportional one a `rate`, the settings and brackets are `facts`). Stored
  policies and drafts waiting in a proposal convert on first read:

  ```
  before: document = {"symbol": "REALM", "taxes": [{"code": "property", "base": "land_area", "method": "progressive",
                      "unit_value": 100, "brackets": [{"up_to": 50000, "rate": 400000}, {"up_to": null, "rate": 800000}]},
                      {"code": "revenue", "base": "declared", "method": "proportional", "rate": 2000000}]}
  after:  document = {"symbol": "REALM"}
          taxes    = [{"code": "property", "kind": "tax", "subject": "Land",
                       "facts": {"unit_value": 100,
                                 "brackets": [{"up_to": 50000, "rate": 400000}, {"up_to": null, "rate": 800000}]}},
                      {"code": "revenue", "kind": "tax", "subject": "Declaration", "rate": 2000000, "facts": {}}]
  ```
- An invoice of the previous release has one item for its whole amount, named as the ledger names its revenue
  (`taxdeclaration` for a tax invoice, `invoice` when it has no reason); a budget line whose `kind` was any text is
  `expense` unless it was `revenue`.
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
