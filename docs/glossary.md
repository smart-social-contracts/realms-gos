# Glossary

| Term | Meaning |
|---|---|
| **Realm** | A governance space: members, departments, votes, treasury, land, courts. Technically, one realm backend canister, or a federation of them (quarters) that share a codex. |
| **GOS** | Governance operating system. Realms GOS is the platform; a realm is one installation of it. |
| **GGG** | Generalized Global Governance: the standard a realm implements (entities, operations, invariants). See [GGG](ggg/README.md). |
| **Operation (op)** | A named function of the realm, such as `proposal_vote`. The only way to read or change a realm. See [operations](architecture/operations.md). |
| **Permission** | A dotted name such as `proposal.vote` that an operation requires. Users get permissions from profiles, appointments and grants. |
| **Profile** (`UserProfile`) | A named set of permissions, such as `member` or `admin`. |
| **Department** | A unit of the realm (Treasury, Health, …) with staff, permissions and a decision policy. The **root** department governs realm-wide matters. |
| **Organization** | An external party (a clinic, a contractor) with representatives. |
| **Position / Appointment** | A seat in a department or organization, and one user (or agent) holding it. |
| **Governed operation** | An operation that becomes a proposal when its department decides by vote instead of directly. |
| **Proposal** | A matter put to a vote. It may carry an action (`{op, args}`) that is replayed when it passes. |
| **Replay** | Running a passed proposal's action, with origin `replay`. |
| **Delegation** | A time-limited power of attorney: a delegate acts for a grantor, within a scope of permissions. |
| **Signing operation** | An operation that only a human may perform in person: never an agent, never under delegation. |
| **Codex** | The realm's laws as code: a manifest plus a hooks module, run sandboxed. One is active per realm. See [codices](codices/README.md). |
| **Hook** | A codex function the realm calls at a known moment: `init`, `on_user_register`, `on_stage_change`, `assign_quarter`. |
| **Effect** | An `(op, args)` pair a hook returns. The realm applies it after the hook ends. |
| **Capabilities** | The operations a codex declares it may read or apply. |
| **Extension** | A feature package: a UI, an optional sandboxed backend, and the operations it may call. See [extensions](extensions/README.md). |
| **Declared operations** | The `operations` an extension manifest lists; the only ones it can call. |
| **Sandbox** | A fresh Python subinterpreter, with an instruction budget and an allow-list of actions, that runs codex and extension code. |
| **Cedar** | The policy language and engine that authorizes every call. See [authorization](architecture/authorization.md). |
| **Guardrail** | A Cedar `forbid` rule that nothing at runtime can override. |
| **Origin** | Where a call comes from: `user`, `extension`, `codex`, `replay`, `shell`. |
| **Quarter** | One realm backend canister in a federation. Each holds its own users. |
| **Capital** | The quarter that publishes the shared codex and admits quarters. |
| **Federal vote** | A vote held in every quarter, decided by a published rule over their results. |
| **Stage** | The realm's lifecycle status: `setup` → `alpha` → `beta` → `production` → `deprecation` → `terminated`. |
| **Founder** | The user who sets up a new realm. Their setup rights end at launch. |
| **File registry** | A canister that stores published extension and codex files, by namespace `ext/<id>/<version>`. |
| **Marketplace** | The canister that lists packages and approves (stamps) them on the file registry. |
| **Trust policy** | The realm's rule for which code it accepts: `require_marketplace_approval` and `trusted_approvers`. |
| **Casals** | The tool that deploys and converges fleets of canisters from a declarative sheet (`casals.json`). |
| **Basilisk** | The Python CDK the realm backend is written in. |
| **Stand** | A group of canisters in a Casals sheet, such as the marketplace or the file registry. |
