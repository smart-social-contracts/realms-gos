# GGG — Generalized Global Governance

**Status:** draft v0.1 for review (phase 0 of [REFACTOR_PLAN.md](../REFACTOR_PLAN.md)).

GGG is the standard a realm implements: the **entities** it stores, the **operations** that change
them, and the **invariants** that must always hold. It describes behaviour, not code. The realm's
`ggg/` package is the Python binding of the entities, its `@op` registry is the binding of the
operations, and the invariants are enforced by Cedar guardrails and checked by the acceptance tests.

When this document and the code disagree, the code is wrong.

Design decisions still open are marked **[D1]**…**[D6]** and listed at the end.

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
| `governed` | When the governing organization's policy is not direct, calling it files a `Proposal` that replays the exact call once approved. |
| `signing` | Produces an act that only an accountable human may make. Agents can never perform it, directly or by delegation. |

Results are `{"ok": true, ...}` or `{"ok": false, "error": "..."}`.

### Callers

A call comes from a **user** (possibly acting `on_behalf_of` a grantor), an **agent** (a user holding
an agent appointment), an **extension** (limited to the operations its manifest declares), a **codex**
(limited to its declared capabilities), or a **replayed proposal** (realm authority). Cedar decides
every call; nothing else does.

## 2. Identity and membership

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `User` | `id` (principal), `nickname`, `avatar`, `status`, `home_quarter`, `joined_at`, `profiles` → `Profile`*, `private_data` (encrypted) | One per principal known to this quarter. **[D2]** absorbs today's `Member` and `GuestUser`. |
| `Human` | `id`, `user` → `User`, `date_of_birth`, `h3_index` | A verified person behind a user. At most one user per human. |
| `Identity` | `id`, `human` → `Human`, `kind` (`passport`, `email`, `registry`, …), `issuer`, `ref`, `verified_at` | An attestation. Proof material is stored encrypted or off-chain; `ref` points to it. |
| `Profile` | `id` (name), `description`, `operations` (Json list of permissions) | A named role. Defaults: `admin` (`all`), `member`. **[D3]** replaces `UserProfile` and `Permission`. |
| `Invite` | `id` (code hash), `profile`, `organization`, `position`, `max_uses`, `uses`, `redeemed_by` (Json), `expires_at`, `revoked`, `created_by` | Today's `RegistrationCode`. The code itself is never stored. |

### Lifecycles

- `User.status`: `visitor` → `member` → `suspended` ⇄ `member`; `member` → `banned` (terminal);
  `guest` for users whose `home_quarter` is another quarter.
- `Identity`: `pending` → `verified` | `rejected`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `realm_join` (with optional invite code) | `self.join` | |
| `user_get`, `user_list` | `user.view` | read |
| `me` (own record, profiles, permissions, delegations) | `self.data_view` | read, delegable |
| `profile_update` (nickname, avatar) | `self.update_public_profile` | delegable |
| `private_data_update` | `self.update_private_data` | |
| `user_add`, `user_import` (bulk) | `user.add` | governed |
| `user_edit` | `user.edit` | |
| `user_set_status` | `user.update_status` | governed |
| `user_remove` | `user.delete` | governed |
| `identity_submit` | `identity.submit` | delegable |
| `identity_review` | `identity.review` | signing |
| `invite_create`, `invite_revoke`, `invite_list` | `invite.manage` | |
| `profile_create`, `profile_edit` | `permission.grant` | governed |
| `role_assign`, `role_revoke` (profile ↔ user) | `role.assign`, `role.revoke` | governed |

### Invariants

- I-ID-1: A user's `id` equals the principal that signs their calls; nobody can act as another user
  except through a `Delegation`.
- I-ID-2: An invite grants at most the profile, organization and position it names, at most
  `max_uses` times, never after `expires_at` or `revoked`.
- I-ID-3: Nobody can assign a profile containing a permission they do not hold themselves.
- I-ID-4: A `banned` user can call nothing except read operations on public data.
- I-ID-5: Private data is readable only by its owner and by principals the owner shared it with.

## 3. Organizations

**[D1]** One `Organization` entity covers both internal departments and external parties
(companies, clinics, schools, courts' operators). Today these are two entities (`Department`,
`Organization`) with crossed names: departments use `organization.*` permissions and their positions
are documented as "product name: Organization", while external organizations have no members at all.

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Organization` | `id` (name), `kind` (`department`, `external`), `description`, `parent` → `Organization`, `is_root`, `head` → `User`, `policy` (Json), `target_policy` (Json), `fund` → `Fund`, `extensions` → `Extension`* | `policy` = `{m, n, quorum_percent, veto: [principals]}`. Exactly one root department per realm. |
| `Position` | `id` (key), `organization` → `Organization`, `title`, `profile` → `Profile`, `headcount`, `salary` (int), `salary_period`, `status`, `inherit_from_capital` | A seat. |
| `Appointment` | `id`, `position` → `Position`, `user` → `User`, `kind` (`substantive`, `acting`), `agent` (bool), `supervisor` → `User`, `started_at`, `ended_at`, `status`, `source_quarter` | One user holding one seat. Agents are users with `agent = true`. |
| `Authority` | `id`, `grantor` → `Organization`, `target` (organization or quarter ref), `operations` (Json) | One organization's powers over another. Today's `DepartmentAuthority`. |

### Lifecycles

- `Position.status`: `open` ⇄ `frozen`; `open` | `frozen` → `closed`.
- `Appointment.status`: `active` → `ended`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `org_create` | `org.create` | governed |
| `org_edit`, `org_delete` | `org.set_policy` | governed |
| `org_set_policy` | `org.set_policy` | governed |
| `org_get`, `org_list`, `org_chart` | `realm.data_view` | read |
| `position_create`, `position_edit`, `position_close` | `org.manage_members` | governed |
| `appoint`, `appoint_acting`, `appointment_end` | `org.appoint` | governed |
| `org_member_add`, `org_member_remove` | `org.manage_members` | governed |
| `org_expel` | `org.expel` | governed |
| `authority_grant`, `authority_revoke` | `org.grant_authority`, `org.revoke_authority` | governed |

### Invariants

- I-ORG-1: A user holds an organization's permissions only through an active appointment in it (or
  through an `Authority` granted to an organization they hold an appointment in).
- I-ORG-2: Active appointments of a position never exceed its `headcount`.
- I-ORG-3: An agent appointment always has a supervisor who holds an active, non-agent appointment in
  the same organization.
- I-ORG-4: An agent never holds a `signing` permission. (Cedar guardrail.)
- I-ORG-5: A policy satisfies `1 ≤ m ≤ n`; veto principals hold an appointment in the organization.
- I-ORG-6: The root department always exists and cannot be deleted.

## 4. Governance

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Proposal` | `id`, `title`, `description`, `proposer` → `User`, `on_behalf_of`, `organization` → `Organization` (scope; root = realm-wide), `action` (Json: `{op, args}` or codex code ref + checksum), `status`, `opens_at`, `deadline`, `threshold`, `quorum_percent`, `tally` (Json), `result` (Json) | |
| `Vote` | `id`, `proposal` → `Proposal`, `voter` → `User`, `cast_by`, `choice` (`yes`, `no`, `abstain`), `cast_at` | One per voter per proposal; may be replaced until the deadline. |
| `Mandate` | `id`, `title`, `scope` (Json), `executor` → `Organization`, `proposal` → `Proposal`, `status`, `expires_at` | An authorization to act, granted by governance. |
| `Contract` | `id`, `title`, `parties` (Json list of `Entity:id`), `mandate` → `Mandate`, `terms_ref`, `terms_hash`, `status`, `starts_at`, `ends_at` | Agreement between parties, possibly under a mandate. |
| `Delegation` | `id`, `grantor`, `delegate`, `scope` (Json), `status`, `label`, `description`, `requires_acceptance`, `granted_by`, `accepted_at`, `starts_at`, `expires_at`, `revoked_at`, `revoked_by` | Power of attorney. |
| `DelegationRequest` | `id`, `grantor`, `delegate`, `scope`, `label`, `description`, `starts_at`, `expires_at`, `request_expires_at` | A delegate asking a member for a grant. Deleted once decided. |

### Lifecycles

- `Proposal.status`: `draft` → `voting` → `accepted` | `rejected` | `expired`; `accepted` →
  `executed` | `failed`; `draft` | `voting` → `withdrawn`.
- `Mandate.status`: `active` → `completed` | `revoked` | `expired`.
- `Contract.status`: `draft` → `active` → `fulfilled` | `terminated` | `disputed`; `disputed` →
  `active` | `terminated`.
- `Delegation.status`: `pending` → `active` → `revoked` | `expired`; `pending` → `declined`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `proposal_create` | `proposal.create` | delegable |
| `proposal_withdraw` | `proposal.create` (own) | delegable |
| `proposal_vote` | `proposal.vote` | delegable |
| `proposal_open`, `proposal_finalize`, `proposal_execute` | `proposal.manage` | |
| `proposal_get`, `proposal_list` | `realm.data_view` | read |
| `mandate_create`, `mandate_revoke` | `mandate.create` | governed |
| `mandate_assign_executor` | `mandate.assign_executor` | governed |
| `contract_create` | `contract.create_under_mandate` | |
| `contract_terminate` | `contract.terminate` | signing |
| `delegation_grant`, `delegation_revoke`, `delegation_accept` | `self.delegate` | |
| `delegation_request` (by the delegate), `delegation_request_decide` (by the grantor) | `self.delegate` | |
| `delegation_list`, `delegable_operations` | `self.data_view` | read |
| `governance_update` (voting windows, thresholds, quorum) | `governance.update` | governed |

### Invariants

- I-GOV-1: A replayed proposal executes exactly the `action` that was voted on, and only once.
- I-GOV-2: Votes count only from users eligible for the proposal's scope at the deadline.
- I-GOV-3: A proposal is accepted only if quorum and threshold are met at finalization.
- I-GOV-4: A delegation never covers a permission the grantor lacks at the time of use, never a
  `signing` permission, never `all`, and never lasts more than one year.
- I-GOV-5: A delegate acts only within `starts_at`…`expires_at` and while the grant is `active`.
- I-GOV-6: Every delegated act records both the grantor and the delegate.
- I-GOV-7: A contract created under a mandate stays within the mandate's scope and term.

## 5. Finance

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Treasury` | `id` (`1`), `config` (Json: epoch length, anchor month, source fund, auto-allocate) | Singleton. Absorbs `TreasuryConfig`. |
| `Token` | `id` (symbol), `ledger`, `indexer`, `decimals`, `fee` | A fungible ledger the realm uses (ICRC-1). |
| `Collection` | `id` (symbol), `canister`, `name`, `transferable`, `required_license` (licence type or empty), `supply_cap` | An NFT ledger (ICRC-7). Absorbs `NFTToken`. |
| `Fund` | `id` (code), `name`, `kind` (general, special revenue, …), `organization` → `Organization` | |
| `FiscalPeriod` | `id`, `name`, `starts_at`, `ends_at`, `status` | |
| `Budget` | `id`, `fund` → `Fund`, `period` → `FiscalPeriod`, `category`, `kind`, `planned`, `actual`, `status`, `proposal` → `Proposal` | |
| `AllocationRule` | `id`, `source_fund`, `rules` (Json), `status`, `proposal` → `Proposal` | How revenue is split between funds. |
| `LedgerEntry` | `id`, `transaction`, `kind` (asset, liability, equity, revenue, expense), `category`, `debit`, `credit`, `symbol`, `fund` → `Fund`, `period` → `FiscalPeriod`, `subject` (`Entity:id`), `at`, `description` | Double entry. One `subject` reference replaces today's eight optional foreign keys. |
| `Invoice` | `id`, `payer` (`Entity:id`), `amount`, `symbol`, `due_at`, `status`, `reason` (`Entity:id`), `paid_at`, `payment` (Json) | Paid into the subaccount derived from its `id`. |
| `Transfer` | `id`, `from`, `to`, `symbol`, `amount`, `ledger_tx`, `invoice` → `Invoice`, `status`, `at` | |
| `Trade` | `id`, `contract` → `Contract`, `leg_a` → `Transfer`, `leg_b` → `Transfer`, `status` | An exchange of two transfers. |
| `PaymentAccount` | `id`, `owner` (`Entity:id`), `network`, `address`, `symbol`, `label`, `verified` | External accounts for payouts. |
| `FinancialReport` | `id`, `kind`, `period` → `FiscalPeriod`, `as_of`, `statements` (Json), `source_hash`, `issued_by`, `supersedes` → `FinancialReport` | Frozen statements. |
| `TaxPolicy` | `id`, `status`, `document` (Json), `description`, `proposal` → `Proposal`, `adopted_at` | Parameters; the codex calculates. |
| `TaxDeclaration` | `id`, `declarant` (`Entity:id`), `period`, `base`, `amount`, `status`, `invoice` → `Invoice` | New: self-declared bases (revenue, payroll). |

Removed: `Balance` (ledgers are the source of truth), `Instrument` (→ `Token`/`Collection`),
`MarketPlace` (→ realm canister settings), `TreasuryConfig` (→ `Treasury.config`).

### Lifecycles

- `Invoice.status`: `pending` → `paid` | `overdue` | `cancelled`; `overdue` → `paid` | `cancelled`.
- `Transfer.status`: `pending` → `completed` | `failed`.
- `Budget.status`: `draft` → `proposed` → `adopted` → `amended`* → `closed`.
- `FiscalPeriod.status`: `open` → `closed` → `archived`.
- `AllocationRule.status`, `TaxPolicy.status`: `draft` → `proposed` → `adopted` → `superseded`.
- `TaxDeclaration.status`: `filed` → `assessed` → `settled` | `disputed`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `treasury_view`, `ledger_view`, `report_view` | `treasury.view` | read |
| `treasury_configure`, `token_register`, `collection_register` | `treasury.manage` | governed |
| `transfer_create` (from a fund) | `transfer.create` | delegable, governed |
| `transfer_revert` | `transfer.delete` | governed |
| `invoice_create` | `treasury.manage` | |
| `invoice_check` (own) | `self.invoice_refresh` | delegable |
| `invoice_refresh` (any) | `invoice.refresh` | |
| `my_invoices` | `self.data_view` | read, delegable |
| `fund_create`, `period_open`, `period_close` | `org.manage_budget` | governed |
| `budget_draft`, `budget_propose`, `budget_amend` | `org.manage_budget` | governed |
| `allocation_rule_draft`, `allocation_rule_propose` | `org.manage_budget` | governed |
| `payroll_run` | `org.manage_budget` | governed |
| `report_issue` | `treasury.manage` | signing |
| `tax_policy_draft`, `tax_policy_propose` | `tax.manage` | governed |
| `tax_assess` | `tax.assess` | |
| `tax_declare` | `tax.declare` | delegable |
| `payment_account_add`, `payment_account_remove` | `self.data_manage` | delegable |
| `token_force_transfer`, `token_freeze` | `token.force_transfer`, `token.freeze` | signing, governed |

### Invariants

- I-FIN-1: Every transaction's ledger entries balance (Σ debit = Σ credit).
- I-FIN-2: Money moves out of a fund only through `transfer_create` under the fund owner's policy.
- I-FIN-3: An invoice is `paid` only when the ledger shows the amount in its subaccount.
- I-FIN-4: Adopted budgets, allocation rules and tax policies are frozen; changes create a new version.
- I-FIN-5: A closed fiscal period accepts no new entries; corrections go into a restating report.
- I-FIN-6: The realm can never move a member's own funds.

## 6. Assets: land, collections and credentials

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Land` | `id`, `x`, `y`, `width`, `height`, `kind`, `owner` (`Entity:id`), `status`, `token_id`, `registered_by`, `metadata` (Json) | A parcel. |
| `Zone` | `id` (h3 index), `name`, `kind`, `land` → `Land`, `user` → `User`, `metadata` (Json) | |
| `Credential` | `id`, `schema`, `collection` → `Collection`, `token_id`, `holder`, `issuer` → `Organization`, `license` → `License`, `prepared_by`, `signer`, `issued_at`, `expires_at`, `status`, `payload_hash`, `payload_ref` | The realm's record of a credential NFT. |

Every NFT the realm mints carries a `ggg:` metadata header:

| Key | Meaning |
|---|---|
| `ggg:v` | header version (`1`) |
| `ggg:realm`, `ggg:quarter`, `ggg:network` | where the record lives |
| `ggg:schema` | codex-defined schema, e.g. `health.prescription/1` |
| `ggg:subject` | `Entity:id` inside that canister |
| `ggg:issuer`, `ggg:license`, `ggg:signer`, `ggg:prepared_by` | who issued it and under which licence |
| `ggg:issued_at`, `ggg:expires_at` | validity |
| `ggg:payload_hash`, `ggg:payload_ref` | private content: hash and encrypted pointer only |

Codices declare schemas (licence type, signer position, human signer required, transferable, public
fields) in their manifest; GGG does not define any schema.

### Lifecycles

- `Land.status`: `active` → `disputed` → `active`; `active` → `transferred` | `revoked`.
- `Credential.status`: `draft` → `issued` → `revoked` | `expired`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `land_register` | `nft.mint` | governed |
| `land_transfer` | `nft.force_transfer` | signing, governed |
| `land_list`, `land_map` | `realm.data_view` | read |
| `land_mint_nft` | `nft.mint` | |
| `land_freeze`, `land_unfreeze` | `nft.freeze` | signing |
| `zone_set`, `zone_clear` | `self.data_manage` | delegable |
| `credential_prepare` | `credential.prepare` | |
| `credential_sign` | `credential.sign` | signing |
| `credential_revoke` | `credential.sign` | signing |
| `credential_verify` | — (public) | read |

### Invariants

- I-AST-1: A credential is minted only if the issuer holds an active licence of the schema's type, the
  signer holds an active non-agent appointment in a position the schema accepts, and the codex allows it.
- I-AST-2: A non-transferable collection's tokens never change owner except by a court order.
- I-AST-3: Revoking a licence suspends new mints under it; existing credentials follow the codex rule.
- I-AST-4: NFT metadata never contains personal data in clear text.
- I-AST-5: A parcel has exactly one owner.

## 7. Licensing and providers

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `License` | `id`, `kind` (court, business, professional, health, education, …), `holder` (`Entity:id`: organization or user), `issued_by` → `Organization`, `status`, `applied_at`, `issued_at`, `expires_at`, `evidence_ref`, `conditions` (Json) | Many licences per holder. An application is a licence in `applied`. |
| `Report` | `id`, `organization` → `Organization`, `kind`, `period`, `public` (Json), `payload_ref`, `payload_hash`, `signer`, `filed_at` | Regulatory reporting by licensed providers. Public part is aggregate only. |

### Lifecycles

- `License.status`: `applied` → `active` | `rejected`; `active` ⇄ `suspended`; `active` |
  `suspended` → `revoked` | `expired`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `license_apply`, `license_renew` | `license.apply` | delegable |
| `license_decide` | `license.issue` | signing |
| `license_suspend`, `license_revoke` | `license.revoke` | signing |
| `license_list`, `license_verify` | — (public) | read |
| `report_submit` | `report.submit` | |
| `report_list` | `realm.data_view` | read |

### Invariants

- I-LIC-1: Nobody decides a licence for an organization they hold an appointment in, or for themselves.
- I-LIC-2: Only `active`, unexpired licences authorize anything.
- I-LIC-3: An identified report is signed by a human holding an active appointment in the reporting
  organization.

## 8. Justice

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `JusticeSystem` | `id`, `name`, `kind` (`public`, `private`, `hybrid`), `status`, `license` → `License` | |
| `Court` | `id`, `name`, `system` → `JusticeSystem`, `level` (first instance, appellate, supreme, specialized), `jurisdiction`, `parent` → `Court`, `status`, `license` → `License` | |
| `Judge` | `id`, `user` → `User`, `court` → `Court`, `specialization`, `status`, `appointed_at` | |
| `Dispute` | `id`, `claimant` → `User`, `respondent` (`Entity:id`), `subject` (`Entity:id`, e.g. a contract), `title`, `description_ref`, `status`, `case` → `Case` | A complaint before it is accepted. |
| `Case` | `id`, `court` → `Court`, `plaintiff`, `defendant`, `judges` → `Judge`*, `title`, `status`, `filed_at`, `closed_at` | |
| `Verdict` | `id`, `case` → `Case`, `issued_by` → `Judge`, `decision`, `reasoning_ref`, `issued_at` | |
| `Appeal` | `id`, `case` → `Case`, `verdict` → `Verdict`, `court` → `Court`, `appellant` → `User`, `grounds_ref`, `status`, `decision`, `new_verdict` → `Verdict` | |
| `Penalty` | `id`, `verdict` → `Verdict`, `target` (`Entity:id`), `kind` (fine, restitution, community service, suspension, revocation, injunction), `amount`, `symbol`, `status`, `due_at`, `executed_at`, `invoice` → `Invoice` | |

### Lifecycles

- `Dispute.status`: `filed` → `accepted` (creates a `Case`) | `rejected` | `withdrawn`.
- `Case.status`: `filed` → `assigned` → `in_progress` → `verdict_issued` → `closed`;
  `verdict_issued` → `appealed` → `verdict_issued` | `closed`; any open state → `dismissed` |
  `transferred`.
- `Appeal.status`: `filed` → `under_review` → `granted` | `denied`; `filed` → `withdrawn`.
- `Penalty.status`: `pending` → `executed` | `waived`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `dispute_file`, `dispute_withdraw` | `dispute.create` | delegable |
| `dispute_get` (own) | `dispute.view` | read, delegable |
| `dispute_list_all` | `dispute.view_all` | read |
| `dispute_accept`, `dispute_reject` | `dispute.accept`, `dispute.reject` | signing |
| `case_assign` | `dispute.assign` | |
| `evidence_submit` | `dispute.create` (party) | delegable |
| `evidence_evaluate` | `evidence.evaluate` | signing |
| `verdict_draft` | `resolution.draft` | |
| `verdict_issue`, `verdict_finalize` | `resolution.issue`, `resolution.finalize` | signing |
| `verdict_link_contract`, `verdict_modify_terms` | `resolution.link_contract`, `resolution.modify_terms` | signing |
| `appeal_file` | `dispute.create` (party) | delegable |
| `appeal_decide` | `appeal.allow` | signing |
| `penalty_execute` (fine → invoice, freeze, revoke access, terminate contract, reassign resource, lock instrument) | `fine.apply`, `access.revoke`, `contract.terminate`, `resource.reassign`, `instrument.lock`, `trade.execute` | signing |
| `enforcement_record`, `enforcement_escalate`, `enforcement_notify` | `enforcement.record`, `enforcement.escalate`, `notification.send` | |
| `justice_system_create`, `court_create`, `judge_appoint`, `judge_revoke` | `org.create`, `org.appoint` | governed |
| `resolution_query` | `resolution.query` | read |

### Invariants

- I-JUS-1: No judge sits on a case in which they, or an organization they hold an appointment in, is
  a party. (Cedar guardrail.)
- I-JUS-2: Only judges assigned to a case issue its verdict.
- I-JUS-3: An appeal is heard by a court of higher level than the original.
- I-JUS-4: A penalty executes only after its verdict is final (no pending appeal, appeal window passed).
- I-JUS-5: Courts and justice systems operate only under an active licence.

## 9. Procurement

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Tender` | `id`, `title`, `description`, `requester` → `Organization`, `status`, `opens_at`, `closes_at`, `rubric` (Json), `winner` → `Bid`, `contract` → `Contract` | Today's `Rfp`. |
| `Bid` | `id`, `tender` → `Tender`, `vendor` → `Organization`, `submitted_at`, `sealed`, `payload_ref`, `payload_hash`, `score` | Content is encrypted until the reveal. |
| `BidScore` | `id`, `bid` → `Bid`, `evaluator` → `User`, `criterion`, `score`, `scored_at` | |

Removed: `RfpTransition` (the `Event` log records transitions), `VendorRecord` (derived from awarded
tenders), `BidPayload` (merged into `Bid` as an encrypted reference).

### Lifecycle

`Tender.status`: `draft` → `open` → `closed` → `evaluation` → `awarded` → `contracted`;
`draft` | `open` → `cancelled`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `tender_create`, `tender_open`, `tender_close`, `tender_cancel` | `tender.manage` | governed |
| `tender_list`, `tender_get` | `realm.data_view` | read |
| `bid_submit`, `bid_withdraw` | `bid.submit` | delegable |
| `bids_reveal` | `tender.manage` | |
| `bid_score` | `bid.evaluate` | signing |
| `tender_award` | `tender.manage` | signing, governed |

### Invariants

- I-PRC-1: Sealed bid contents are unreadable by anyone, including the requester, until `bids_reveal`
  after `closes_at`.
- I-PRC-2: No evaluator scores a bid from an organization they hold an appointment in.
- I-PRC-3: The award goes to the best-scored bid under the published rubric, or the deviation is
  justified and recorded.
- I-PRC-4: Bids are accepted only between `opens_at` and `closes_at`.

## 10. Quarters and federation

A realm can be one canister or a federation of quarters, one of which is the capital.

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Realm` | `id` (`1`), `name`, `manifesto`, `welcome`, `status`, `is_capital`, `federation_id`, `currency`, `currency_decimals`, `canisters` (Json), `settings` (Json), `test_flags` (Json), `state` (Json), `calendar` (Json), `manifest` (Json) | Singleton per canister. Today ~60 columns; grouped into Json by purpose. Absorbs `Calendar` and `Registry`. |
| `Quarter` | `id` (canister id), `name`, `index`, `status`, `population`, `codex_id`, `codex_version`, `last_sync`, `last_activity_at` | Known on the capital. |
| `Resident` | `id` (principal), `quarter` | Directory of where users live. Today's `QuarterResident`. |
| `Forward` | `id`, `subject` (`Entity:id`), `from`, `to`, `moved_at`, `signature` | Signed pointer left when a user moves quarter. Today's `EntityMigration`. |
| `FederalVote` | `id`, `origin_quarter`, `action` (Json), `rule` (Json), `hash`, `deadline`, `status`, `tally` (Json) | |
| `FederalVoteLeg` | `id`, `vote` → `FederalVote`, `quarter`, `proposal_id`, `outcome`, `yes`, `no`, `abstain`, `eligible`, `status`, `hash` | One per quarter. |
| `FederationMessage` | `id`, `topic`, `source`, `body` (Json), `response` (Json) | |

### Lifecycles

- `Realm.status`: `setup` → `alpha` → `beta` → `production` → `deprecation` → `terminated`.
- `Quarter.status`: `setup` → `active` ⇄ `suspended`; `active` → `splitting` | `merging` → `active`.
- `FederalVote.status`: `open` → `passed` | `failed` | `cancelled`.

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `quarter_register`, `quarter_deregister`, `quarter_configure` | `quarter.register`, `quarter.deregister`, `quarter.configure` | governed |
| `quarter_secede` | `quarter.secede` | governed |
| `quarter_join_federation` | `quarter.join_federation` | governed |
| `quarter_directory`, `join_targets`, `scale_status` | — (public) | read |
| `self_change_quarter` | `self.change_quarter` | delegable |
| `quarter_sync`, `quarter_report` (quarter → capital) | quarter canister principal | |
| `federal_vote_propose` | `federal_vote.propose` | delegable |
| `federal_vote_cancel` | `federal_vote.manage` | governed |
| `federal_vote_finalize` | — (anyone, after deadline) | |
| `federation_message` | quarter canister principal | |

### Invariants

- I-FED-1: A user is a member of exactly one home quarter; elsewhere they are a guest.
- I-FED-2: A federal vote passes only by its published rule over legs reported by every known quarter
  (or the rule's explicit absence policy).
- I-FED-3: Every quarter runs the same federation codex version as the capital, or is flagged as drifting.
- I-FED-4: Calls between quarters are accepted only from registered quarter canisters.

## 11. Codex, extensions and automation

### Entities

| Entity | Fields | Notes |
|---|---|---|
| `Codex` | `id`, `version`, `checksum`, `status`, `manifest` (Json), `capabilities` (Json), `installed_at`, `previous` → `Codex` | The realm's policy package. |
| `Extension` | `id` (name), `version`, `status`, `operations` (Json, declared), `access` (Json: profiles and organizations), `sidebar` (Json) | Absorbs `MenuItemConfig`, `MenuCategoryConfig`, `MenuDepartmentVisibility`. |
| `Task`, `TaskStep`, `TaskSchedule`, `TaskExecution`, `Call` | as in `ic-basilisk-toolkit` | Scheduled and multi-step automation. |
| `Notification` | `id`, `recipient` (`Entity:id`: user or organization), `topic`, `title`, `body`, `href`, `sender`, `visibility`, `read_by` (Json), `at` | |
| `Event` | `id`, `op`, `caller`, `on_behalf_of`, `args` (Json, secrets stripped), `result` (Json), `at` | New: the audit log. Append-only. |

Removed: `Service` (services are provided by licensed organizations), `MarketPlace`, `Registry`,
`Member`, `GuestUser`, `Permission`, `UserProfile`, `RegistrationCode`, `Department`,
`DepartmentAuthority`, `Calendar`, `TreasuryConfig`, `NFTToken`, `Instrument`, `Balance`,
`EntityMigration`, `QuarterResident`, `Menu*` (renamed or merged as noted in each section).

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `codex_install`, `codex_uninstall`, `codex_revert` | `codex.install`, `codex.uninstall`, `codex.revert` | governed |
| `codex_status` | `realm.data_view` | read |
| `extension_install`, `extension_uninstall` | `extension.install`, `extension.uninstall` | governed |
| `extension_call` | `extension.call` + the extension's declared operation | |
| `extension_set_access`, `sidebar_configure` | `realm.configure` | governed |
| `task_create`, `task_edit`, `task_delete`, `task_run`, `task_schedule`, `task_cancel` | `task.*` | |
| `task_list`, `task_logs` | `task.view` | read |
| `notification_list`, `notification_read` | `self.data_view` | read, delegable |
| `notification_send` | `notification.send` | |
| `events` | `realm.monitor` | read |
| `ops_list` | — (public) | read |

### Invariants

- I-EXT-1: An extension can call only the operations its manifest declares, always as the calling
  user, never with more permissions than that user.
- I-EXT-2: A codex can apply only the effects its manifest declares, and cannot weaken a guardrail.
- I-EXT-3: Installing or upgrading codex or extension code requires the realm's trust policy
  (marketplace approval or trusted approvers).
- I-EXT-4: The `Event` log is append-only.

## 12. Realm setup and administration

### Operations

| Operation | Permission | Flags |
|---|---|---|
| `status`, `version`, `realm_info` | — (public) | read |
| `setup_state`, `setup_step`, `setup_launch` | `realm.configure` (founder during `setup`) | |
| `realm_configure` | `realm.configure` | governed |
| `realm_configure_branding` | `realm.configure.branding` | governed |
| `realm_configure_tokens` | `realm.configure.tokens` | governed |
| `realm_configure_infrastructure` | `realm.configure.infrastructure` | governed |
| `realm_configure_trust_policy` | `realm.configure.trust_policy` | governed |
| `realm_register` | `realm.register` | governed |
| `realm_upgrade` | `realm.upgrade` | governed |
| `orchestration_decide` (approve or reject a Casals baton action for this realm) | `orchestration.approve` | |
| `realm_monitor` | `realm.monitor` | read |
| `realm_export` | `db.export` | read |
| `document_*` (department documents: store, share, revoke) | `document.manage` | |
| `crypto_*` (vetKeys, envelopes, sharing) | `self.data_manage` / scope rules | |
| `shell` (REPL; calls operations as the caller) | `shell.execute` | |

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
- Organizations: `org.create`, `org.appoint`, `org.expel`, `org.set_policy`, `org.grant_authority`,
  `org.revoke_authority`, `org.manage_budget`, `org.manage_members`.
- Governance: `proposal.create`, `proposal.vote`, `proposal.manage`, `mandate.create`,
  `mandate.assign_executor`, `contract.create_under_mandate`, `governance.update`,
  `orchestration.approve`.
- Finance: `treasury.view`, `treasury.manage`, `transfer.create`, `transfer.delete`,
  `invoice.refresh`, `tax.manage` (new), `tax.assess` (new), `tax.declare` (new),
  `token.force_transfer`, `token.freeze`.
- Assets: `nft.mint`, `nft.force_transfer`, `nft.freeze`, `credential.prepare` (new),
  `credential.sign` (new).
- Licensing: `license.apply` (new), `license.issue`, `license.revoke`, `report.submit` (new).
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
(no demo code in the canister), `organization.add`, `organization.edit`, `organization.delete`
(→ `org.*`), `realm.configure.codex` (→ `codex.install`), `scope.authorize`, `permission.view` and `permission.revoke` (unused today; viewing and
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
- I-ALL-3: Nobody decides a matter they filed, that was filed on their behalf, or in which an
  organization they hold an appointment in is a party.
- I-ALL-4: Every update operation is audited with the caller, the grantor (if delegated) and the
  outcome.
- I-ALL-5: Personal data never appears in clear text in public state, NFT metadata or the event log.
- I-ALL-6: An operation that fails changes nothing.

## 15. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Merge `Department` and `Organization` into one `Organization` with `kind` | Yes: removes the crossed naming and gives external providers positions, funds, policies and licences |
| D2 | Merge `Member` and `GuestUser` into `User.status` | Yes |
| D3 | Replace `UserProfile` + `Permission` with `Profile.operations` (roles hold operations; users hold roles; positions grant a role) | Yes: one way to hold a permission |
| D4 | Collapse `Realm`'s ~60 columns into a few Json groups (`canisters`, `settings`, `test_flags`, `state`, `calendar`) | Yes |
| D5 | Replace `LedgerEntry`'s eight optional foreign keys with one `subject` reference | Yes |
| D6 | Keep `Task*` entities defined in `ic-basilisk-toolkit` (shared with Casals) rather than in `ggg/` | Yes |
