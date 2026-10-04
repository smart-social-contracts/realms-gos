# Organizations (issue #240)

Governance units inside a quarter. Product name: **Organization**.
GGG entity today: `Department` (identity `Organization` is unrelated — land/licenses).

## Rules

1. **Members are people/agents only** — no org nesting.
2. Each org has a **policy** (M/N, quorum %, veto principals).
3. Each org may have a **budget** (`Fund` link).
4. Orgs hold **permissions**, including **authority over other orgs**.
5. Each quarter has exactly one top org named **`root`**.
6. **Capital** = the quarter with `Realm.is_capital`. Capital `root` may grant authority over orgs in other quarters (fields on `DepartmentAuthority`; enforcement is phased).

## Entities

| Entity | Role |
|--------|------|
| `Department` | Org: members, policy fields, `is_root`, optional `fund` |
| `DepartmentAuthority` | Grantor org → target org (local or remote canister + name) |
| `Fund.department` | Budget envelope for one org |

## Default behaviour

- Realm init / upgrade calls `ensure_root_org()` and grants root default manage perms over local orgs (`org.appoint`, `org.expel`, …).
- `access_manager` UI is labeled **Organizations**.
- Nesting via `parent` is rejected by the API.

## Acting inherit at quarter birth (issue #301)

When a new quarter is provisioned, capital seat-holders are copied as **acting**
appointments on matching local positions (`inherit_from_capital` on `Position`,
default `true`). This is **creation-time only** — it bootstraps governance until
each quarter staffs its own orgs.

- A later **local substantive** appointment ends the acting term for that seat.
- Seats with `inherit_from_capital: false` are skipped (local-only election).
- `target_policy` on `Department` is seeded from codex JSON; live `policy_*` is
  ratcheted at the alpha→beta transition via `apply_target_policies()`, not at
  quarter birth.
- **Congress** is not inherited long-term: each quarter appoints its own Congress
  after beta.

## Phases

See [#240](https://github.com/smart-social-contracts/realms-gos/issues/240): Phase 1 schema+UI (done here); Phase 2 policy enforcement on actions; Phase 3 cross-quarter verification.
