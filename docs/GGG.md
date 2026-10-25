# GGG — Generalized Global Governance

**Status:** draft v0.2 for review (phase 0 of [REFACTOR_PLAN.md](../REFACTOR_PLAN.md)).

GGG is the standard a realm implements: the **entities** it stores, the **operations** that change
them, and the **invariants** that must always hold. It describes behaviour, not code. The realm's
`ggg/` package is the Python binding of the entities, its `@op` registry is the binding of the
operations, and the invariants are enforced by Cedar guardrails and checked by the acceptance tests.

When this document and the code disagree, the code is wrong.

Design decisions still open are marked **[D1]**…**[D5]** and listed at the end.

## 1. Conventions

| Topic | Rule |
|---|---|
| Identity | Every entity has a text `id`. Human-facing codes (`CASE-12`, `LIC-7`) are ids, not extra fields. |
| References | Relations name both ends. A reference to "any entity" is a text `Entity:id`. |
| Principals | Text. A user's `id` is their principal. |
| Time | Integer seconds since the epoch. `0` means "not set". No ISO strings. |
| Money | Integer amounts in the token's smallest unit, with the token `symbol`. No floats. |
| Status | A text field with an explicit lifecycle (listed per entity). Terminal states are final. |
| Structured data | `Json` fields, never JSON encoded by hand into `String`. |
| Behaviour | Entities hold data only: no hooks, no methods beyond trivial reads. Behaviour lives in operations. |
| Audit | Every update operation writes one `Event`. |
| Privacy | Personal or sensitive content is stored encrypted (vetKeys envelopes); entities hold only a reference and a hash. |

### Operations

An operation has a **name** (`proposal_vote`), a **permission** (`proposal.vote`), arguments and a
result. Several operations may share a permission. Flags:

| Flag | Meaning |
|---|---|
| `read` | Query; changes nothing; not audited. |
| `delegable` | May be called with `on_behalf_of`: the caller acts for a grantor whose active `Delegation` covers the permission. Both principals are audited. |
| `governed` | When the governing department's policy is not direct, calling it files a `Proposal` that replays the exact call once approved. |
| `signing` | Produces an act that only an accountable human may make. Agents can never perform it, directly or by delegation. |

Results are `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

### Callers

A call comes from a **user** (possibly acting `on_behalf_of` a grantor), an **agent** (a user holding
an agent appointment), an **extension** (limited to the operations its manifest declares), a **codex**
(limited to its declared capabilities), or a **replayed proposal** (realm authority). Cedar decides
every call; nothing else does.

## Running example

All examples follow one realm, **Agora**: a capital and the quarters **North** and **South**, currency
**REALM**. People: **Alice** and **Bob** (members), **Dana** (head of Civil Registry), **Carla**
(planning officer, Infrastructure), **Dr. Ana** (physician at Clinic B), **Judge Elena**, **Mallory**
(a spammer). Departments: Civil Registry, Infrastructure, Finance, Health, Justice. External
organizations: **Clinic B** (health provider) and **BuildCo** (contractor). Agents: `claude-conn`
(Alice's AI connection) and `records-bot` (Clinic B's records agent).

## 2. Identity and membership

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `User` | `id` (principal), `nickname`, `avatar`, `status`, `home_quarter`, `joined_at`, `profiles` → `Profile`*, `private_data` (encrypted) | One per principal known to this quarter. **[D1]** absorbs today's `Member` and `GuestUser`. | `alice`: nickname Alice, `member`, home quarter North, profiles `[member]` |
| `Human` | `id`, `user` → `User`, `date_of_birth`, `h3_index` | A verified person behind a user. At most one user per human. | Alice's verified person record: born 1990-04-12, home cell `871f1d489ffffff` |
| `Identity` | `id`, `human` → `Human`, `kind` (`passport`, `email`, `registry`, …), `issuer`, `ref`, `verified_at` | An attestation. Proof material is stored encrypted or off-chain; `ref` points to it. | Alice's passport proof: kind `passport`, issuer `zkpassport`, verified 2026-03-02 |
| `Profile` | `id` (name), `description`, `operations` (Json list of permissions) | A named role. Defaults: `admin` (`all`), `member`. **[D2]** replaces `UserProfile` and `Permission`. | `member` → `[self.join, proposal.vote, proposal.create, dispute.create, …]` |
| `Invite` | `id` (code hash), `profile`, `department`, `position`, `max_uses`, `uses`, `redeemed_by` (Json), `expires_at`, `revoked`, `created_by` | Today's `RegistrationCode`. The code itself is never stored. | 50-use code for new clerks: profile `member`, Civil Registry, position `clerk`, expires in 30 days |
| `Delegation` | `id`, `grantor`, `delegate`, `scope` (Json), `status`, `label`, `description`, `requires_acceptance`, `granted_by`, `accepted_at`, `starts_at`, `expires_at`, `revoked_at`, `revoked_by` | Power of attorney. | Alice → `claude-conn`: `[proposal.vote, proposal.create]` for 24 h, `active` |
| `DelegationRequest` | `id`, `grantor`, `delegate`, `scope`, `label`, `description`, `starts_at`, `expires_at`, `request_expires_at` | A delegate asking a member for a grant. Deleted once decided. | Claude asks Alice for `[proposal.vote]` for 7 days; the link is valid 1 h |

### Lifecycles

- `User.status`: `visitor` → `member` → `suspended` ⇄ `member`; `member` → `banned` (terminal);
  `guest` for users whose `home_quarter` is another quarter.
- `Identity`: `pending` → `verified` | `rejected`.
- `Delegation.status`: `pending` → `active` → `revoked` | `expired`; `pending` → `declined`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `realm_join` (with optional invite code) | `self.join` | | Bob joins with code `X7K-29Q` → `{user: "bob", status: "member"}` |
| `user_get`, `user_list` | `user.view` | read | Dana looks up `bob`: nickname, status, profiles |
| `me` (own record, profiles, permissions, delegations) | `self.data_view` | read, delegable | Alice's dashboard: her profiles, permissions and the grant to Claude |
| `profile_update` (nickname, avatar) | `self.update_public_profile` | delegable | Claude sets Alice's avatar `{avatar: "…/alice.png", on_behalf_of: "alice"}` |
| `private_data_update` | `self.update_private_data` | | Alice stores her home address, encrypted |
| `user_add`, `user_import` (bulk) | `user.add` | governed | Civil Registry imports 1,200 residents from the old register (2-of-3 approval) |
| `user_edit` | `user.edit` | | Dana fixes a typo in Bob's nickname |
| `user_set_status` | `user.update_status` | governed | `{user: "mallory", status: "suspended"}` → proposal `P-9` under Civil Registry's policy |
| `user_remove` | `user.delete` | governed | Remove Bob's duplicate test account |
| `identity_submit` | `identity.submit` | delegable | Alice submits her passport proof |
| `identity_review` | `identity.review` | signing | Dana approves Alice's passport proof |
| `invite_create`, `invite_revoke`, `invite_list` | `invite.manage` | | Dana creates 50 clerk invites; revokes one sent to the wrong email |
| `profile_create`, `profile_edit` | `permission.grant` | governed | New profile `treasurer` → `[treasury.view, transfer.create]` |
| `role_assign`, `role_revoke` (profile ↔ user) | `role.assign`, `role.revoke` | governed | Give Bob `treasurer` → proposal under the root department's policy |
| `delegation_grant`, `delegation_revoke`, `delegation_accept` | `self.delegate` | | Alice lets Bob vote for her for 2 weeks while she travels |
| `delegation_request` (by the delegate), `delegation_request_decide` (by the grantor) | `self.delegate` | | Claude asks for `[proposal.vote, proposal.create]`, 24 h; Alice approves only voting |
| `delegation_list`, `delegable_operations` | `self.data_view` | read | Alice's grants; delegable operations: `[proposal.vote, proposal.create, …]` |

### Invariants

- I-ID-1: A user's `id` equals the principal that signs their calls; nobody can act as another user
  except through a `Delegation`.
- I-ID-2: An invite grants at most the profile, department and position it names, at most
  `max_uses` times, never after `expires_at` or `revoked`.
- I-ID-3: Nobody can assign a profile containing a permission they do not hold themselves.
- I-ID-4: A `banned` user can call nothing except read operations on public data.
- I-ID-5: Private data is readable only by its owner and by principals the owner shared it with.
- I-ID-6: A delegation never covers a permission the grantor lacks at the time of use, never a
  `signing` permission, never `all`, and never lasts more than one year.
- I-ID-7: A delegate acts only within `starts_at`…`expires_at` and while the grant is `active`.
- I-ID-8: Every delegated act records both the grantor and the delegate.

## 3. Departments and organizations

Two kinds of body:

- A **department** belongs to the realm. Its positions, appointments and policy are realm data and
  change only under the realm's own rules.
- An **organization** is an external party: a company, clinic, school or contractor. How it runs
  inside is its own business. The realm records its licences and its **representatives**: the people
  and agents who may act or sign for it.

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Department` | `id` (name), `description`, `parent` → `Department`, `is_root`, `head` → `User`, `policy` (Json), `target_policy` (Json), `fund` → `Fund`, `extensions` → `Extension`* | `policy` = `{m, n, quorum_percent, veto: [principals]}`. Exactly one root department per realm. | Civil Registry: under Administration, 2-of-3 policy, head `dana` |
| `Organization` | `id` (name), `description`, `status`, `admins` → `User`* | An external party. Its admins manage its representatives. | Clinic B: `active`, admin `dr-ana` |
| `Position` | `id` (key), `department` → `Department` or `organization` → `Organization`, `title`, `profile` → `Profile`, `headcount`, `salary` (int), `salary_period`, `status`, `inherit_from_capital` | A seat in a department, or a representative seat in an organization. | `civil-registry.clerk`: 4 seats, 2,000 REALM/month, profile `clerk`; `clinic-b.physician`: 3 seats |
| `Appointment` | `id`, `position` → `Position`, `user` → `User`, `kind` (`substantive`, `acting`), `agent` (bool), `supervisor` → `User`, `started_at`, `ended_at`, `status`, `source_quarter` | One user holding one seat. Agents are users with `agent = true`. | Bob holds a clerk seat since 2026-05-01; `records-bot` holds `records_agent` at Clinic B, agent, supervisor `dr-ana` |
| `Authority` | `id`, `grantor` → `Department`, `target` (department or quarter ref), `operations` (Json) | One department's powers over another. Today's `DepartmentAuthority`. | Finance lets Infrastructure use `[transfer.create]` on the `ROADS` fund |

### Lifecycles

- `Position.status`: `open` ⇄ `frozen`; `open` | `frozen` → `closed`.
- `Appointment.status`: `active` → `ended`.
- `Organization.status`: `active` ⇄ `suspended`; `active` | `suspended` → `dissolved`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `department_create` | `org.create` | governed | Create department Health under the root → proposal `P-12` |
| `department_edit`, `department_delete` | `org.set_policy` | governed | Rename Roads to Mobility |
| `department_set_policy` | `org.set_policy` | governed | Infrastructure goes from 1-of-1 to 2-of-3 with 50% quorum |
| `department_get`, `department_list`, `department_chart` | `realm.data_view` | read | Civil Registry's chart: 3 positions, 7 of 9 seats filled |
| `position_create`, `position_edit`, `position_close` | `org.manage_members` | governed | Open 2 `inspector` seats in Infrastructure at 2,400 REALM/month |
| `appoint`, `appoint_acting`, `appointment_end` | `org.appoint` | governed | Appoint Carla planning officer; Bob acts as head while Dana is on leave |
| `department_expel` | `org.expel` | governed | Expel a member from Infrastructure after a misconduct verdict |
| `authority_grant`, `authority_revoke` | `org.grant_authority`, `org.revoke_authority` | governed | Finance grants Infrastructure spending on `ROADS` |
| `organization_register` | `organization.add` | | Dr. Ana registers Clinic B and becomes its admin |
| `organization_edit` | `organization.edit` (its admins) | | Clinic B updates its description and adds an admin |
| `representative_add`, `representative_remove` | `organization.edit` (its admins) | | Clinic B adds `records-bot` as an agent supervised by Dr. Ana |
| `organization_set_status` | `organization.delete` | governed | Suspend BuildCo while it is investigated for fraud |
| `organization_get`, `organization_list` | `realm.data_view` | read | Licensed health providers in North |

### Invariants

- I-ORG-1: A user holds a department's permissions only through an active appointment in it (or
  through an `Authority` granted to a department they hold an appointment in).
- I-ORG-2: Active appointments of a position never exceed its `headcount`.
- I-ORG-3: An agent appointment always has a supervisor who holds an active, non-agent appointment in
  the same department or organization.
- I-ORG-4: An agent never holds a `signing` permission. (Cedar guardrail.)
- I-ORG-5: A policy satisfies `1 ≤ m ≤ n`; veto principals hold an appointment in the department.
- I-ORG-6: The root department always exists and cannot be deleted.
- I-ORG-7: A department's positions and policy change only under the realm's rules; an organization's
  representatives are managed only by its admins.
- I-ORG-8: Nobody can act for a suspended or dissolved organization.

## 4. Governance

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Proposal` | `id`, `title`, `description`, `proposer` → `User`, `on_behalf_of`, `department` → `Department` (scope; root = realm-wide), `action` (Json: `{op, args}` or codex code ref + checksum), `status`, `opens_at`, `deadline`, `threshold`, `quorum_percent`, `tally` (Json), `result` (Json) | | `P-7` Bike lane on Main St, scope Infrastructure, action `transfer_create` 50,000 REALM to BuildCo, `voting` until 8 Nov |
| `Vote` | `id`, `proposal` → `Proposal`, `voter` → `User`, `cast_by`, `choice` (`yes`, `no`, `abstain`), `cast_at` | One per voter per proposal; may be replaced until the deadline. | Alice votes `yes` on `P-7`, cast by `claude-conn` |
| `Mandate` | `id`, `title`, `scope` (Json), `executor` → `Department`, `proposal` → `Proposal`, `status`, `expires_at` | An authorization to act, granted by governance. | Infrastructure may contract road works up to 200,000 REALM until 2027-06-30 (from `P-4`) |
| `Contract` | `id`, `title`, `parties` (Json list of `Entity:id`), `mandate` → `Mandate`, `terms_ref`, `terms_hash`, `status`, `starts_at`, `ends_at` | Agreement between parties, possibly under a mandate. | `C-9`: Agora × BuildCo, bike lane works, Dec–Mar, terms `sha256:4be1…` |

### Lifecycles

- `Proposal.status`: `draft` → `voting` → `accepted` | `rejected` | `expired`; `accepted` →
  `executed` | `failed`; `draft` | `voting` → `withdrawn`.
- `Mandate.status`: `active` → `completed` | `revoked` | `expired`.
- `Contract.status`: `draft` → `active` → `fulfilled` | `terminated` | `disputed`; `disputed` →
  `active` | `terminated`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `proposal_create` | `proposal.create` | delegable | `{title: "Bike lane on Main St", department: "infrastructure", action: {…}}` → `P-7` |
| `proposal_withdraw` | `proposal.create` (own) | delegable | Bob withdraws his draft `P-8` |
| `proposal_vote` | `proposal.vote` | delegable | `{proposal: "P-7", choice: "yes", on_behalf_of: "alice"}` → `V-31` |
| `proposal_open`, `proposal_finalize`, `proposal_execute` | `proposal.manage` | | After the deadline: 412 yes, 120 no, quorum met → `accepted`; execute pays BuildCo |
| `proposal_get`, `proposal_list` | `realm.data_view` | read | Open proposals in quarter North |
| `mandate_create`, `mandate_revoke` | `mandate.create` | governed | Grant Infrastructure the road-works mandate (via `P-4`) |
| `mandate_assign_executor` | `mandate.assign_executor` | governed | Make Infrastructure the mandate's executor |
| `contract_create` | `contract.create_under_mandate` | | Sign `C-9` with BuildCo under the road-works mandate |
| `contract_terminate` | `contract.terminate` | signing | End `C-9` after BuildCo misses two milestones (court order) |
| `governance_update` (voting windows, thresholds, quorum) | `governance.update` | governed | Voting window from 7 to 5 days |

### Invariants

- I-GOV-1: A replayed proposal executes exactly the `action` that was voted on, and only once.
- I-GOV-2: Votes count only from users eligible for the proposal's scope at the deadline.
- I-GOV-3: A proposal is accepted only if quorum and threshold are met at finalization.
- I-GOV-4: A contract created under a mandate stays within the mandate's scope and term.

## 5. Finance

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Treasury` | `id` (`1`), `config` (Json: epoch length, anchor month, source fund, auto-allocate) | Singleton. Absorbs `TreasuryConfig`. | Monthly epochs, source fund `ROOT`, auto-allocation on |
| `Token` | `id` (symbol), `ledger`, `indexer`, `decimals`, `fee` | A fungible ledger the realm uses (ICRC-1). | `ckBTC`: ledger `mxzaz-…`, 8 decimals, fee 10 |
| `NFTToken` | `id` (symbol), `canister`, `name`, `description`, `transferable`, `required_license` (licence type or empty), `supply_cap`, `total_supply` | An NFT ledger (ICRC-7) the realm mints into. | `LAND` (transferable); `CRED` (non-transferable, minting needs a licence) |
| `Fund` | `id` (code), `name`, `kind` (general, special revenue, …), `department` → `Department` | | `ROADS`: special revenue, owned by Infrastructure |
| `FiscalPeriod` | `id`, `name`, `starts_at`, `ends_at`, `status` | | `FY2026`: 2026-01-01 to 2026-12-31, `open` |
| `Budget` | `id`, `fund` → `Fund`, `period` → `FiscalPeriod`, `category`, `kind`, `planned`, `actual`, `status`, `proposal` → `Proposal` | | Roads FY2026, capital: planned 300,000, actual 120,500, `adopted` |
| `AllocationRule` | `id`, `source_fund`, `rules` (Json), `status`, `proposal` → `Proposal` | How revenue is split between funds. | From `ROOT`: 30% `WELFARE`, 20% `ROADS`, 50% `GENERAL` |
| `LedgerEntry` | `id`, `transaction`, `kind` (asset, liability, equity, revenue, expense), `category`, `debit`, `credit`, `symbol`, `fund` → `Fund`, `period` → `FiscalPeriod`, `subject` (`Entity:id`), `at`, `description` | Double entry. One `subject` reference replaces today's eight optional foreign keys. | Tx `T-88`: debit `ROADS` capital expense 50,000, credit cash 50,000, subject `Contract:C-9` |
| `Invoice` | `id`, `payer` (`Entity:id`), `amount`, `symbol`, `due_at`, `status`, `reason` (`Entity:id`), `paid_at`, `payment` (Json) | Paid into the subaccount derived from its `id`. | `INV-301`: Alice owes 120 REALM property tax, due 2026-12-31, `pending` |
| `Transfer` | `id`, `from`, `to`, `symbol`, `amount`, `ledger_tx`, `invoice` → `Invoice`, `status`, `at` | | 50,000 REALM from `ROADS` to BuildCo, ledger tx 18234, `completed` |
| `Trade` | `id`, `contract` → `Contract`, `leg_a` → `Transfer`, `leg_b` → `Transfer`, `status` | An exchange of two transfers. | `C-9` milestone: realm pays 10,000 REALM, BuildCo hands over the road deed NFT |
| `PaymentAccount` | `id`, `owner` (`Entity:id`), `network`, `address`, `symbol`, `label`, `verified` | External accounts for payouts. | Bob's ckBTC payout address, verified |
| `FinancialReport` | `id`, `kind`, `period` → `FiscalPeriod`, `as_of`, `statements` (Json), `source_hash`, `issued_by`, `supersedes` → `FinancialReport` | Frozen statements. | FY2025 closing statements issued by Finance, `sha256:77d0…` |
| `TaxPolicy` | `id`, `status`, `document` (Json), `description`, `proposal` → `Proposal`, `adopted_at` | Parameters; the codex calculates. | `TAX-POLICY-3`: property tax 0.4% up to 50,000, 0.8% above, annual, `adopted` |
| `TaxDeclaration` | `id`, `declarant` (`Entity:id`), `period`, `base`, `amount`, `status`, `invoice` → `Invoice` | New: self-declared bases (revenue, payroll). | Clinic B declares 182,000 revenue for 2026-Q3 → `INV-412` |

Removed: `Balance` (ledgers are the source of truth), `Instrument` (→ `Token`/`NFTToken`),
`MarketPlace` (→ realm canister settings), `TreasuryConfig` (→ `Treasury.config`).

### Lifecycles

- `Invoice.status`: `pending` → `paid` | `overdue` | `cancelled`; `overdue` → `paid` | `cancelled`.
- `Transfer.status`: `pending` → `completed` | `failed`.
- `Budget.status`: `draft` → `proposed` → `adopted` → `amended`* → `closed`.
- `FiscalPeriod.status`: `open` → `closed` → `archived`.
- `AllocationRule.status`, `TaxPolicy.status`: `draft` → `proposed` → `adopted` → `superseded`.
- `TaxDeclaration.status`: `filed` → `assessed` → `settled` | `disputed`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `treasury_view`, `ledger_view`, `report_view` | `treasury.view` | read | Finance sees `ROOT` 1.2M, `ROADS` 180k REALM |
| `treasury_configure`, `token_register`, `nft_token_register` | `treasury.manage` | governed | Quarterly allocation epochs; register ckUSDC |
| `transfer_create` (from a fund) | `transfer.create` | delegable, governed | `{fund: "ROADS", to: "buildco", amount: 50000}` → proposal under Infrastructure's policy |
| `transfer_revert` | `transfer.delete` | governed | Cancel a duplicate pending payment |
| `invoice_create` | `treasury.manage` | | Registration fee: 10 REALM for new member Bob |
| `invoice_check` (own) | `self.invoice_refresh` | delegable | Alice paid `INV-301` → `paid` |
| `invoice_refresh` (any) | `invoice.refresh` | | Finance re-checks all pending invoices |
| `my_invoices` | `self.data_view` | read, delegable | Alice: `INV-301`, 120 REALM, due 31 Dec |
| `fund_create`, `period_open`, `period_close` | `org.manage_budget` | governed | Create fund `WELFARE`; close `FY2025` |
| `budget_draft`, `budget_propose`, `budget_amend` | `org.manage_budget` | governed | Roads FY2027: 400,000 capital → proposal |
| `allocation_rule_draft`, `allocation_rule_propose` | `org.manage_budget` | governed | Welfare share from 30% to 35% → proposal |
| `payroll_run` | `org.manage_budget` | governed | November salaries: 23 seats, 51,200 REALM |
| `report_issue` | `treasury.manage` | signing | Finance issues the FY2025 statements |
| `tax_policy_draft`, `tax_policy_propose` | `tax.manage` | governed | Top bracket to 1.0% → realm-wide vote |
| `tax_assess` | `tax.assess` | | Annual run: 1,180 property-tax invoices |
| `tax_declare` | `tax.declare` | delegable | `{organization: "clinic-b", period: "2026-Q3", base: "revenue", amount: 182000}` → `INV-412` |
| `payment_account_add`, `payment_account_remove` | `self.data_manage` | delegable | Bob adds his ckBTC address |
| `token_force_transfer`, `token_freeze` | `token.force_transfer`, `token.freeze` | signing, governed | Court-ordered restitution: 500 REALM from Mallory to Alice; freeze Mallory's account meanwhile |

### Invariants

- I-FIN-1: Every transaction's ledger entries balance (Σ debit = Σ credit).
- I-FIN-2: Money moves out of a fund only through `transfer_create` under the fund owner's policy.
- I-FIN-3: An invoice is `paid` only when the ledger shows the amount in its subaccount.
- I-FIN-4: Adopted budgets, allocation rules and tax policies are frozen; changes create a new version.
- I-FIN-5: A closed fiscal period accepts no new entries; corrections go into a restating report.
- I-FIN-6: The realm can never move a member's own funds.

## 6. Assets: land and NFTs

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Land` | `id`, `x`, `y`, `width`, `height`, `kind`, `owner` (`Entity:id`), `status`, `token_id`, `registered_by`, `metadata` (Json) | A parcel. | `L-17`: 3×2 cells at (12, 40), residential, owner `alice`, `LAND` #4412 |
| `Zone` | `id` (h3 index), `name`, `kind`, `land` → `Land`, `user` → `User`, `metadata` (Json) | | Cell `871f1d489ffffff` "Riverside", residential, Alice's home |

Certificates (prescriptions, diplomas, permits, titles) are NFTs, not realm entities: the ledger is
the source of truth, and the token's `ggg:` header carries what the realm knows about it. Every NFT the
realm mints carries that header:

| Key | Meaning | Example |
|---|---|---|
| `ggg:v` | header version (`1`) | `1` |
| `ggg:realm`, `ggg:quarter`, `ggg:network` | where the record lives | `agora-capital-id`, `north-quarter-id`, `ic` |
| `ggg:schema` | codex-defined schema, e.g. `health.prescription/1` | `health.prescription/1` |
| `ggg:subject` | `Entity:id` inside that canister | `Contract:C-88` |
| `ggg:issuer`, `ggg:license`, `ggg:signer`, `ggg:prepared_by` | who issued it and under which licence | `Organization:clinic-b`, `LIC-12`, `dr-ana`, `records-bot` |
| `ggg:issued_at`, `ggg:expires_at` | validity | `1792915200`, `1795507200` |
| `ggg:payload_hash`, `ggg:payload_ref` | private content: hash and encrypted pointer only | `sha256:9f2c…`, `user:alice:rx-88` |

Codices declare schemas (licence type, signer position, human signer required, transferable, public
fields) in their manifest; GGG does not define any schema.

### Lifecycles

- `Land.status`: `active` → `disputed` → `active`; `active` → `transferred` | `revoked`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `land_register` | `nft.mint` | governed | Register `L-17` for Alice after the survey |
| `land_transfer` | `nft.force_transfer` | signing, governed | Court orders `L-17` moved from Mallory to Alice (normal sales use the ledger) |
| `land_list`, `land_map` | `realm.data_view` | read | Map of quarter North's parcels |
| `land_mint_nft` | `nft.mint` | | Mint `LAND` #4412 for `L-17` to Alice |
| `land_freeze`, `land_unfreeze` | `nft.freeze` | signing | Freeze `L-17` during an ownership dispute |
| `zone_set`, `zone_clear` | `self.data_manage` | delegable | Alice picks Riverside as her home zone |
| `credential_issue` | `credential.sign` | signing | Dr. Ana issues `{schema: "health.prescription/1", holder: "alice", organization: "clinic-b", prepared_by: "records-bot"}` → `CRED` #4412 |
| `credential_revoke` | `credential.sign` | signing | Revoke a diploma issued in error; the token is burned |
| `credential_verify` | — (public) | read | A pharmacy checks `CRED` #4412: valid, issuer licensed, not expired |

### Invariants

- I-AST-1: A credential NFT is minted only if the issuing organization holds an active licence of the
  schema's type, the caller holds an active non-agent appointment in a position the schema accepts, and
  the codex allows it.
- I-AST-2: Tokens of a non-transferable `NFTToken` never change owner except by a court order.
- I-AST-3: Revoking a licence stops new mints under it; existing credential NFTs follow the codex rule.
- I-AST-4: NFT metadata never contains personal data in clear text.
- I-AST-5: A parcel has exactly one owner.

## 7. Licensing

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `License` | `id`, `kind` (court, business, professional, health, education, …), `holder` (`Entity:id`: organization or user), `issued_by` → `Department`, `status`, `applied_at`, `issued_at`, `expires_at`, `evidence_ref`, `conditions` (Json) | Many licences per holder. An application is a licence in `applied`. | `LIC-12`: health, holder Clinic B, issued by Health, active until 2028-10-25 |

### Lifecycles

- `License.status`: `applied` → `active` | `rejected`; `active` ⇄ `suspended`; `active` |
  `suspended` → `revoked` | `expired`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `license_apply`, `license_renew` | `license.apply` | delegable | Clinic B applies for a health licence with its dossier |
| `license_decide` | `license.issue` | signing | A Health officer approves → `LIC-12` active for 2 years |
| `license_suspend`, `license_revoke` | `license.revoke` | signing | Suspend `LIC-12` pending an inspection |
| `license_list`, `license_verify` | — (public) | read | A patient checks Clinic B: licence active |

### Invariants

- I-LIC-1: Nobody decides a licence for an organization they hold an appointment in, or for themselves.
- I-LIC-2: Only `active`, unexpired licences authorize anything.

Regulatory reports from licensed providers are department documents (`document_*`) or codex-defined
credential NFTs; GGG has no report entity.

## 8. Justice

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Court` | `id`, `name`, `operator` (`Entity:id`: a department for public courts, an organization for private ones), `level` (first instance, appellate, supreme, specialized), `jurisdiction`, `parent` → `Court`, `status`, `license` → `License` | | North First Instance Court, run by the Justice department; appeals go to Agora Appeals Court |
| `Judge` | `id`, `user` → `User`, `court` → `Court`, `specialization`, `status`, `appointed_at` | | Judge Elena, North First Instance Court, civil |
| `Dispute` | `id`, `claimant` → `User`, `respondent` (`Entity:id`), `subject` (`Entity:id`, e.g. a contract), `title`, `description_ref`, `status`, `case` → `Case` | A complaint before it is accepted. | `D-14`: Bob vs Infrastructure, "building permit refused unfairly", `filed` |
| `Case` | `id`, `court` → `Court`, `plaintiff`, `defendant`, `judges` → `Judge`*, `title`, `status`, `filed_at`, `closed_at` | | `CASE-12` from `D-14`, North First Instance, Judge Elena, `in_progress` |
| `Verdict` | `id`, `case` → `Case`, `issued_by` → `Judge`, `decision`, `reasoning_ref`, `issued_at` | | `CASE-12`: refusal annulled, issue the permit within 15 days |
| `Appeal` | `id`, `case` → `Case`, `verdict` → `Verdict`, `court` → `Court`, `appellant` → `User`, `grounds_ref`, `status`, `decision`, `new_verdict` → `Verdict` | | Infrastructure appeals `CASE-12` to Agora Appeals Court, `under_review` |
| `Penalty` | `id`, `verdict` → `Verdict`, `target` (`Entity:id`), `kind` (fine, restitution, community service, suspension, revocation, injunction), `amount`, `symbol`, `status`, `due_at`, `executed_at`, `invoice` → `Invoice` | | Mallory: 500 REALM fine for spam, due in 30 days → `INV-520` |

### Lifecycles

- `Dispute.status`: `filed` → `accepted` (creates a `Case`) | `rejected` | `withdrawn`.
- `Case.status`: `filed` → `assigned` → `in_progress` → `verdict_issued` → `closed`;
  `verdict_issued` → `appealed` → `verdict_issued` | `closed`; any open state → `dismissed` |
  `transferred`.
- `Appeal.status`: `filed` → `under_review` → `granted` | `denied`; `filed` → `withdrawn`.
- `Penalty.status`: `pending` → `executed` | `waived`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `dispute_file`, `dispute_withdraw` | `dispute.create` | delegable | `{respondent: "Department:infrastructure", title: "Permit refused"}` → `D-14` |
| `dispute_get` (own) | `dispute.view` | read, delegable | Bob checks `D-14` |
| `dispute_list_all` | `dispute.view_all` | read | The court clerk lists all open disputes |
| `dispute_accept`, `dispute_reject` | `dispute.accept`, `dispute.reject` | signing | The court accepts `D-14` → `CASE-12` |
| `case_assign` | `dispute.assign` | | Assign Judge Elena to `CASE-12` |
| `evidence_submit` | `dispute.create` (party) | delegable | Bob uploads his building plans, encrypted |
| `evidence_evaluate` | `evidence.evaluate` | signing | Elena admits the plans as evidence |
| `verdict_draft` | `resolution.draft` | | Elena drafts the verdict |
| `verdict_issue`, `verdict_finalize` | `resolution.issue`, `resolution.finalize` | signing | Elena issues it; it becomes final when the 14-day appeal window passes |
| `verdict_link_contract`, `verdict_modify_terms` | `resolution.link_contract`, `resolution.modify_terms` | signing | Link the verdict to contract `C-9` it terminates |
| `appeal_file` | `dispute.create` (party) | delegable | Infrastructure appeals within 14 days |
| `appeal_decide` | `appeal.allow` | signing | The appeals court denies the appeal |
| `penalty_execute` (fine → invoice, freeze, revoke access, terminate contract, reassign resource, lock instrument) | `fine.apply`, `access.revoke`, `contract.terminate`, `resource.reassign`, `instrument.lock`, `trade.execute` | signing | Mallory's 500 REALM fine → invoice `INV-520` |
| `enforcement_record`, `enforcement_escalate`, `enforcement_notify` | `enforcement.record`, `enforcement.escalate`, `notification.send` | | Record that the permit was issued as ordered |
| `court_create`, `judge_appoint`, `judge_revoke` | `org.create`, `org.appoint` | governed | Create South First Instance Court; appoint Judge Elena |
| `resolution_query` | `resolution.query` | read | All verdicts about building permits since 2026 |

### Invariants

- I-JUS-1: No judge sits on a case in which they, or a department or organization they hold an
  appointment in, is a party. (Cedar guardrail.)
- I-JUS-2: Only judges assigned to a case issue its verdict.
- I-JUS-3: An appeal is heard by a court of higher level than the original.
- I-JUS-4: A penalty executes only after its verdict is final (no pending appeal, appeal window passed).
- I-JUS-5: A court run by an organization operates only under an active court licence.

## 9. Procurement

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Tender` | `id`, `title`, `description`, `requester` → `Department`, `status`, `opens_at`, `closes_at`, `rubric` (Json), `winner` → `Bid`, `contract` → `Contract` | Today's `Rfp`. | `T-5` Bike lane works, by Infrastructure, open 1–30 Nov, rubric: price 60%, quality 40% |
| `Bid` | `id`, `tender` → `Tender`, `vendor` → `Organization`, `submitted_at`, `sealed`, `payload_ref`, `payload_hash`, `score` | Content is encrypted until the reveal. | BuildCo's bid on `T-5`, sealed until 30 Nov |
| `BidScore` | `id`, `bid` → `Bid`, `evaluator` → `User`, `criterion`, `score`, `scored_at` | | Carla scores BuildCo's quality 8/10 |

Removed: `RfpTransition` (the `Event` log records transitions), `VendorRecord` (derived from awarded
tenders), `BidPayload` (merged into `Bid` as an encrypted reference).

### Lifecycle

`Tender.status`: `draft` → `open` → `closed` → `evaluation` → `awarded` → `contracted`;
`draft` | `open` → `cancelled`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `tender_create`, `tender_open`, `tender_close`, `tender_cancel` | `tender.manage` | governed | Infrastructure drafts `T-5`, then opens it |
| `tender_list`, `tender_get` | `realm.data_view` | read | Open tenders |
| `bid_submit`, `bid_withdraw` | `bid.submit` | delegable | BuildCo's agent submits an encrypted bid on `T-5` |
| `bids_reveal` | `tender.manage` | | After 30 Nov the 4 bids are decrypted |
| `bid_score` | `bid.evaluate` | signing | Carla and two colleagues score each bid |
| `tender_award` | `tender.manage` | signing, governed | Award `T-5` to BuildCo (score 86) → contract `C-9` |

### Invariants

- I-PRC-1: Sealed bid contents are unreadable by anyone, including the requester, until `bids_reveal`
  after `closes_at`.
- I-PRC-2: No evaluator scores a bid from an organization they hold an appointment in. (Cedar guardrail.)
- I-PRC-3: The award goes to the best-scored bid under the published rubric, or the deviation is
  justified and recorded.
- I-PRC-4: Bids are accepted only between `opens_at` and `closes_at`.

## 10. Quarters and federation

A realm can be one canister or a federation of quarters, one of which is the capital.

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Realm` | `id` (`1`), `name`, `manifesto`, `welcome`, `status`, `is_capital`, `federation_id`, `currency`, `currency_decimals`, `canisters` (Json), `settings` (Json), `test_flags` (Json), `state` (Json), `calendar` (Json), `manifest` (Json) | Singleton per canister. Today ~60 columns; grouped into Json by purpose. Absorbs `Calendar` and `Registry`. | Agora: `beta`, capital, currency REALM (8 decimals) |
| `Quarter` | `id` (canister id), `name`, `index`, `status`, `population`, `codex_id`, `codex_version`, `last_sync`, `last_activity_at` | Known on the capital. | North: population 1,240, `active`, codex agora 1.4.2 |
| `Forward` | `id`, `subject` (`Entity:id`), `from`, `to`, `moved_at`, `signature` | Signed pointer left when a user moves quarter. Today's `EntityMigration`. | Bob moved North → South on 2026-09-01, signed by North |
| `FederalVote` | `id`, `origin_quarter`, `action` (Json), `rule` (Json), `hash`, `deadline`, `status`, `tally` (Json) | | `FV-3` Adopt tax policy 3: majority of quarters, deadline 15 Nov |
| `FederalVoteLeg` | `id`, `vote` → `FederalVote`, `quarter`, `proposal_id`, `outcome`, `yes`, `no`, `abstain`, `eligible`, `status`, `hash` | One per quarter. | North on `FV-3`: 812 yes, 301 no of 1,240 eligible → passed |
| `FederationMessage` | `id`, `topic`, `source`, `body` (Json), `response` (Json) | | North → capital: population report |

There is no central user-to-quarter directory. As [QUARTERS.md](reference/QUARTERS.md) requires, a
user's home quarter is found from their own client pointer, the quarter index, or a broadcast search,
so no canister grows with the population of the whole federation.

### Lifecycles

- `Realm.status`: `setup` → `alpha` → `beta` → `production` → `deprecation` → `terminated`.
- `Quarter.status`: `setup` → `active` ⇄ `suspended`; `active` → `splitting` | `merging` → `active`.
- `FederalVote.status`: `open` → `passed` | `failed` | `cancelled`.

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `quarter_register`, `quarter_deregister`, `quarter_configure` | `quarter.register`, `quarter.deregister`, `quarter.configure` | governed | Register quarter South |
| `quarter_secede` | `quarter.secede` | governed | South leaves the federation after its own vote |
| `quarter_join_federation` | `quarter.join_federation` | governed | The independent realm Harbor joins Agora as a quarter |
| `quarter_directory`, `join_targets`, `scale_status` | — (public) | read | Quarters and their populations |
| `self_change_quarter` | `self.change_quarter` | delegable | Bob moves to South |
| `quarter_sync`, `quarter_report` (quarter → capital) | quarter canister principal | | North reports population 1,240 and codex 1.4.2 to the capital |
| `federal_vote_propose` | `federal_vote.propose` | delegable | Propose `FV-3` |
| `federal_vote_cancel` | `federal_vote.manage` | governed | Withdraw `FV-3` before its deadline |
| `federal_vote_finalize` | — (anyone, after deadline) | | After 15 Nov: 3 of 4 quarters in favour → `passed` |
| `federation_message` | quarter canister principal | | The capital announces codex 1.5.0 to its quarters |

### Invariants

- I-FED-1: A user is a member of exactly one home quarter; elsewhere they are a guest.
- I-FED-2: A federal vote passes only by its published rule over legs reported by every known quarter
  (or the rule's explicit absence policy).
- I-FED-3: Every quarter runs the same federation codex version as the capital, or is flagged as drifting.
- I-FED-4: Calls between quarters are accepted only from registered quarter canisters.

## 11. Codex, extensions and automation

### Entities

| Entity | Fields | Notes | Example |
|---|---|---|---|
| `Codex` | `id`, `version`, `checksum`, `status`, `manifest` (Json), `capabilities` (Json), `installed_at`, `previous` → `Codex` | The realm's policy package. | `agora` 1.4.2, `active`, capabilities `[invoice.create, notification.create, …]` |
| `Extension` | `id` (name), `version`, `status`, `operations` (Json, declared), `access` (Json: profiles and departments), `sidebar` (Json) | Absorbs `MenuItemConfig`, `MenuCategoryConfig`, `MenuDepartmentVisibility`. | `voting` 2.1.0, for `[member]`, in the Governance menu |
| `Task`, `TaskStep`, `TaskSchedule`, `TaskExecution`, `Call` | as in `ic-basilisk-toolkit` | Scheduled and multi-step automation. | Monthly payroll on the 1st; last run succeeded |
| `Notification` | `id`, `recipient` (`Entity:id`: user, department or organization), `topic`, `title`, `body`, `href`, `sender`, `visibility`, `read_by` (Json), `at` | | To Alice: "P-7 passed", link `/proposals/P-7` |
| `Event` | `id`, `op`, `caller`, `on_behalf_of`, `args` (Json, secrets stripped), `result` (Json), `at` | New: the audit log. Append-only. | `proposal_vote` by `claude-conn` for `alice` at 09:31 → ok |

Removed or merged: `Service` (services are provided by licensed organizations), `MarketPlace` and
`Registry` (→ `Realm.canisters`), `Member` and `GuestUser` (→ `User.status`), `Permission` and
`UserProfile` (→ `Profile`), `RegistrationCode` (→ `Invite`), `DepartmentAuthority` (→ `Authority`),
`Calendar` (→ `Realm.calendar`), `TreasuryConfig` (→ `Treasury.config`), `Instrument` (→ `Token`),
`Balance` (ledgers are the source of truth), `EntityMigration` (→ `Forward`), `Menu*`
(→ `Extension.sidebar`), `JusticeSystem` (courts stand on their own), `QuarterResident` (no central
user directory).

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `codex_install`, `codex_uninstall`, `codex_revert` | `codex.install`, `codex.uninstall`, `codex.revert` | governed | Install agora 1.5.0 → realm-wide vote; revert to 1.4.2 if it misbehaves |
| `codex_status` | `realm.data_view` | read | agora 1.4.2, healthy |
| `extension_install`, `extension_uninstall` | `extension.install`, `extension.uninstall` | governed | Install `procurement` 1.0.3 (marketplace-approved) → vote |
| `extension_call` | `extension.call` + the extension's declared operation | | The voting extension casts Alice's vote through `proposal_vote` |
| `extension_set_access`, `sidebar_configure` | `realm.configure` | governed | Show `vault` only to Finance |
| `task_create`, `task_edit`, `task_delete`, `task_run`, `task_schedule`, `task_cancel` | `task.*` | | Schedule a daily invoice check |
| `task_list`, `task_logs` | `task.view` | read | Payroll task and its last 3 runs |
| `notification_list`, `notification_read` | `self.data_view` | read, delegable | Alice's 4 unread notifications |
| `notification_send` | `notification.send` | | Justice notifies Mallory of her fine |
| `events` | `realm.monitor` | read | The last 100 audited calls |
| `ops_list` | — (public) | read | geister reads it to build MCP tools |

### Invariants

- I-EXT-1: An extension can call only the operations its manifest declares, always as the calling
  user, never with more permissions than that user.
- I-EXT-2: A codex can apply only the effects its manifest declares, and cannot weaken a guardrail.
- I-EXT-3: Installing or upgrading codex or extension code requires the realm's trust policy
  (marketplace approval or trusted approvers).
- I-EXT-4: The `Event` log is append-only.

## 12. Realm setup and administration

### Operations

| Operation | Permission | Flags | Example |
|---|---|---|---|
| `status`, `version`, `realm_info` | — (public) | read | Version 0.9.0, `beta`, 3 quarters |
| `setup_state`, `setup_step`, `setup_launch` | `realm.configure` (founder during `setup`) | | The founder picks codex, token and branding, then launches |
| `realm_configure` | `realm.configure` | governed | Turn on open registration |
| `realm_configure_branding` | `realm.configure.branding` | governed | Logo and primary colour `#1E6B52` |
| `realm_configure_tokens` | `realm.configure.tokens` | governed | Accounting currency ckUSDC |
| `realm_configure_infrastructure` | `realm.configure.infrastructure` | governed | Point to the file-registry canister |
| `realm_configure_trust_policy` | `realm.configure.trust_policy` | governed | Require marketplace approval for new code |
| `realm_register` | `realm.register` | governed | List Agora in the realm registry |
| `realm_upgrade` | `realm.upgrade` | governed | Upgrade to release 0.9.1 |
| `orchestration_decide` (approve or reject a Casals baton action for this realm) | `orchestration.approve` | | Approve Casals' action "upgrade North to 0.9.1" |
| `realm_monitor` | `realm.monitor` | read | Cycles 4.2T, memory 310 MB |
| `realm_export` | `db.export` | read | Export a snapshot for backup |
| `document_*` (department documents: store, share, revoke) | `document.manage` | | Health stores an encrypted inspection report and shares it with Clinic B |
| `crypto_*` (vetKeys, envelopes, sharing) | `self.data_manage` / scope rules | | Alice derives her vetKey to open a shared document |
| `shell` (REPL; calls operations as the caller) | `shell.execute` | | An admin runs `ops.proposal_list({"status": "voting"})` |

### Invariants

- I-ADM-1: Test flags can be enabled only on non-production networks or when `can_test_mode` was set
  at install.
- I-ADM-2: During `setup`, only the founder can configure; after `setup_launch`, configuration is governed.
- I-ADM-3: A realm upgrade installs only a release the trust policy accepts.

## 13. Permissions

The permission keys, grouped by domain. **New** keys are introduced by this spec; **removed** keys
disappear.

- Identity: `self.join`, `self.update_public_profile`, `self.update_private_data`,
  `self.change_quarter`, `self.invoice_refresh`, `self.data_view`, `self.data_manage`,
  `self.delegate` (new), `user.add`, `user.edit`, `user.delete`, `user.update_status`, `user.view`,
  `invite.manage`, `identity.submit`, `identity.review`, `role.assign`, `role.revoke`,
  `permission.grant`.
- Departments: `org.create`, `org.appoint`, `org.expel`, `org.set_policy`, `org.grant_authority`,
  `org.revoke_authority`, `org.manage_budget`, `org.manage_members`.
- Organizations: `organization.add`, `organization.edit`, `organization.delete`.
- Governance: `proposal.create`, `proposal.vote`, `proposal.manage`, `mandate.create`,
  `mandate.assign_executor`, `contract.create_under_mandate`, `governance.update`,
  `orchestration.approve`.
- Finance: `treasury.view`, `treasury.manage`, `transfer.create`, `transfer.delete`,
  `invoice.refresh`, `tax.manage` (new), `tax.assess` (new), `tax.declare` (new),
  `token.force_transfer`, `token.freeze`.
- Assets: `nft.mint`, `nft.force_transfer`, `nft.freeze`, `credential.sign` (new).
- Licensing: `license.apply` (new), `license.issue`, `license.revoke`.
- Justice: `dispute.create`, `dispute.view`, `dispute.view_all`, `dispute.accept`, `dispute.reject`,
  `dispute.assign`, `evidence.evaluate`, `resolution.draft`, `resolution.issue`,
  `resolution.link_contract`, `resolution.modify_terms`, `resolution.finalize`, `resolution.query`,
  `appeal.allow`, `trade.execute`, `fine.apply`, `access.revoke`, `contract.terminate`,
  `resource.reassign`, `instrument.lock`, `notification.send`, `enforcement.escalate`,
  `enforcement.record`.
- Procurement: `tender.manage` (new), `bid.submit`, `bid.evaluate`.
- Federation: `quarter.register`, `quarter.deregister`, `quarter.configure`, `quarter.secede`,
  `quarter.join_federation`, `federal_vote.propose`, `federal_vote.manage`, `realm.register`.
- Platform: `codex.install`, `codex.uninstall`, `codex.revert`, `extension.install`,
  `extension.uninstall`, `extension.call`, `task.create`, `task.edit`, `task.delete`, `task.run`,
  `task.schedule`, `task.cancel`, `task.view`, `document.manage`.
- Administration: `all`, `realm.upgrade`, `realm.configure`, `realm.configure.branding`,
  `realm.configure.infrastructure`, `realm.configure.tokens`,
  `realm.configure.trust_policy`, `realm.data_view`, `realm.monitor`, `db.export`, `shell.execute`.

**Removed:** `realm.admin` (same as `all`), `extension.sync_call` and `extension.async_call` (one
`extension.call`), `db.manage` (no raw database writes; everything is an operation), `demo.manage`
(no demo code in the canister), `realm.configure.codex` (→ `codex.install`), `scope.authorize`, `permission.view` and `permission.revoke` (unused today; viewing and
revoking roles is covered by `user.view` and `role.revoke`).

**Signing permissions** (never held by agents, never delegable): `identity.review`,
`credential.sign`, `license.issue`, `license.revoke`, `dispute.accept`, `dispute.reject`,
`evidence.evaluate`, `resolution.issue`, `resolution.finalize`, `resolution.link_contract`,
`resolution.modify_terms`, `appeal.allow`, `fine.apply`, `access.revoke`, `contract.terminate`,
`resource.reassign`, `instrument.lock`, `trade.execute`, `nft.force_transfer`, `nft.freeze`,
`token.force_transfer`, `token.freeze`, `bid.evaluate`.

## 14. Cross-cutting invariants

- I-ALL-1: Every call is decided by Cedar; there is no other authorization path.
- I-ALL-2: Guardrails (`guardrails.cedar`) cannot be weakened by governance, codices or extensions.
- I-ALL-3: Nobody decides a matter they filed, that was filed on their behalf, or in which a
  department or organization they hold an appointment in is a party.
- I-ALL-4: Every update operation is audited with the caller, the grantor (if delegated) and the
  outcome.
- I-ALL-5: Personal data never appears in clear text in public state, NFT metadata or the event log.
- I-ALL-6: An operation that fails changes nothing.

## 15. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Merge `Member` and `GuestUser` into `User.status` | Yes |
| D2 | Replace `UserProfile` + `Permission` with `Profile.operations` (roles hold operations; users hold roles; positions grant a role) | Yes: one way to hold a permission |
| D3 | Collapse `Realm`'s ~60 columns into a few Json groups (`canisters`, `settings`, `test_flags`, `state`, `calendar`) | Yes |
| D4 | Replace `LedgerEntry`'s eight optional foreign keys with one `subject` reference | Yes |
| D5 | Keep `Task*` entities defined in `ic-basilisk-toolkit` (shared with Casals) rather than in `ggg/` | Yes |
