# Principal delegation (Power of Attorney)

Scoped **act-on-behalf** grants from a realm user to another principal. Distinct
from **role delegation** in [ACCESS_CONTROL.md](./ACCESS_CONTROL.md) (department
heads assigning profiles via `delegate:<profile>` permissions).

## Model

| Field | Meaning |
|---|---|
| **Grantor** | User whose rights are exercised (`User.id` = principal) |
| **Delegate** | Principal that signs calls (`ic.caller()`): another member, an agent, or an AI connection (Geister MCP) that is not a member |
| **Label / description** | Name and purpose shown in both parties' lists |
| **Scope** | JSON: `{"operations": ["proposal.vote", …]}` or `{"all": true}` |
| **Starts / expires** | `starts_at` (0 = now) and `expires_at`, unix seconds. Expiry is required, at most a year after the start |
| **Status** | `pending` → `active` → `revoked` / `expired` |

Authentication is always the delegate's Internet Identity (or agent key). The
backend validates an active delegation and applies business logic to the
**grantor's** `User` when `on_behalf_of` is set in JSON args.

Grantable operations (`DELEGABLE_OPERATIONS` in `core/delegation.py`):

| Operation | Label |
|---|---|
| `proposal.vote` | Vote on proposals |
| `proposal.create` | Submit proposals |
| `transfer.create` | Propose treasury transactions (needs `proposal.create` too) |

`grantor` and `delegate` are indexed (`find_by`); lists are paginated. Existing
rows get the index from a backfill timer at upgrade (`fi_backfill:Delegation:v2`).

## API (realm backend)

| Method | Type | Description |
|---|---|---|
| `grant_delegation_json` | update | Create delegation; caller = grantor or admin |
| `accept_delegation_json` | update | Delegate accepts `pending` grant |
| `revoke_delegation_json` | update | Grantor, delegate, or admin revokes |
| `list_delegations_page_json` | query | One page for the caller as grantor and as delegate: `{grantor_cursor, delegate_cursor, count}` → `next_grantor_cursor` / `next_delegate_cursor` (0 = done, or pass 0 to skip that side) |
| `list_delegations_json` | query | First page only (older callers) |
| `get_delegable_operations_json` | query | The grantable operations, and those the caller may grant |
| `request_delegation_json` | update | The caller (future delegate) asks `grantor` for `scope` with a grant window, a self-declared `label` and a `description`; returns the request (open for an hour) |
| `get_delegation_request_json` | query | `{request_id}` → the open request, for its grantor or delegate; for the grantor each asked permission says whether they hold it |
| `approve_delegation_request_json` | update | Grantor only: `{request_id, operations?, expires_at?}`, a subset of the permissions and an expiry no later than asked; replaces an open grant to the same delegate |
| `decline_delegation_request_json` | update | Grantor declines or delegate withdraws |

Grant payload example:

```json
{
  "grantor": "ufptc-evwin-…",
  "delegate": "abc12-…",
  "scope": { "operations": ["proposal.vote", "proposal.create"] },
  "label": "Claude",
  "description": "Vote on the budget proposals while I'm away",
  "starts_at": 0,
  "expires_at": 1793000000,
  "requires_acceptance": true
}
```

`expires_in_hours` (default 168) is used when `expires_at` is absent.

Delegated extension call (voting):

```json
{
  "proposal_id": "…",
  "vote": "yes",
  "on_behalf_of": "ufptc-evwin-…"
}
```

`extension_sync_call` / `extension_async_call` admit a caller without the
extension-call permission when it holds an active grant from `on_behalf_of`
and that grantor has the permission (`delegate_may_call`). The extension gate
(`core/extension_access.py`) then lets it through only if the grant covers the
method's level; the extension resolves the acting user with
`resolve_acting_context`.

### Requests

`DelegationRequest` holds only open requests: approving, declining or finding
one expired deletes it. A newer request from the same delegate to the same
grantor replaces the older one. At most 10 open requests per grantor and 20
per delegate, so every scan is a few rows (`find_by` on the indexed `grantor`
and `delegate`).

## Frontend

- `/delegations` (menu → **Power of attorney**) — grant form (principal, name,
  description, permission checklist, start and expiry), inbox, accept/decline,
  revoke. Query parameters `delegate`, `name`, `description`, `ops`
  (comma-separated) and `hours` pre-fill the form.
- `/delegations/authorize?request=<id>` — the consent page for a request
  (Geister's `request_power_of_attorney` links here): self-declared name,
  requesting principal, the permissions asked for (ones the member does not
  hold greyed out), expiry, "signed in as". The member can untick permissions
  or shorten the expiry, then Authorize or Cancel. The page refuses to run
  framed by anything but the realm itself or its portal.
- Navbar **DelegationSwitcher** — session-scoped acting context
- **DelegationBanner** when not acting as self
- `withDelegationArgs()` / `withDelegationJson()` in `$lib/stores/delegation.js`

## Geister

Agents grant from their own identity:

```python
grant_delegation(
    grantor="<agent-principal>",
    delegate="<your-II-principal>",
    scope={"operations": ["proposal.vote", "proposal.create"]},
    identity="swarm_agent_001",
    realm_principal="3eotx-…",
)
```

AI connections (Claude, ChatGPT, …) through Geister's MCP server are read-only
until the user grants the connection's principal power of attorney here,
usually by approving the request the AI files; see geister `docs/MCP_SERVER.md`.

## Rules

1. Delegation cannot exceed grantor's permissions at grant time, and only
   grantable operations can be delegated.
2. The grantor must be a registered user. A member delegate must accept; a
   non-member delegate's grant is active from its start.
3. One open (pending or active) grant per grantor and delegate; revoke it to
   change it, or approve a new request, which replaces it.
4. Votes and other writes record grantor as subject; delegate in `metadata`.
5. Token transfers require separate ICRC-2 allowance (not covered by realm delegation).

## Test mode

`test_mode_ii_bypass` / PEM URL login is **not** used for production delegation.
Use real II for the delegate and grant from the agent via `grant_delegation`.
