# Authorization

Every call to every operation is decided by one [Cedar](https://www.cedarpolicy.com) policy set.
There is no other check: no `if is_admin` in the operations, and no back door. The schema, the
policies and the code that builds each request are in
[`core/auth.py`](../../src/realm_backend/core/auth.py), about 140 lines.

## The request

For each call, `dispatch` asks Cedar one question: *may this principal `call` the realm, in this
context?*

- **Principal**: `Realm::User::"<the user>"`, with two attributes: `permissions` (the set the user
  holds, see below) and `banned`.
- **Action**: `Realm::Action::"call"`. There is only one action; the operation is in the context.
- **Resource**: `Realm::Realm::"1"`.
- **Context**: facts about this call.

| Context field | Meaning |
|---|---|
| `op` | The operation name, e.g. `proposal_vote` |
| `permission` | The permission the operation needs (from `@op`, or `perm_for(args)`) |
| `user` | Who the call is for: the signer, or the grantor in `on_behalf_of` |
| `signer` | Who signed the message |
| `origin` | `user`, `extension`, `codex` or `replay` |
| `read`, `public`, `signing`, `delegable` | The operation's flags |
| `delegated` | `user != signer` |
| `signer_agent` | The operation is `signing` and the signer holds an agent appointment |
| `scope` | The permissions the grantor delegated to the signer |
| `declared` | The operations the calling extension or codex declared |
| `parties` | The principals the matter is about (from `parties(args)`) |

## The policies

A call is allowed when at least one `permit` matches and no `forbid` matches.

```cedar
// Anyone may call a public operation.
permit (principal, action == Realm::Action::"call", resource) when { context.public };

// A user who holds the permission (or "all") may call the operation.
permit (principal, action == Realm::Action::"call", resource)
  when { principal.permissions.contains("all") || principal.permissions.contains(context.permission) };

// A codex may call the operations it declared.
permit (principal, action == Realm::Action::"call", resource)
  when { context.origin == "codex" && context.declared.contains(context.op) };
```

## The guardrails

The guardrails are `forbid` policies. A forbid beats every permit, so they hold even for a user
with `all`. They are a constant in the code: no operation, vote, codex or extension can change
them (invariant I-ALL-2).

```cedar
// Agents never make signing acts (a judge's ruling, a licence, a freeze).
forbid (principal, action, resource) when { context.signing && context.signer_agent };
// Signing acts are never done under a power of attorney...
forbid (principal, action, resource) when { context.delegated && context.signing };
// ...nor are operations that are not delegable...
forbid (principal, action, resource) when { context.delegated && !context.delegable };
// ...nor anything outside the delegated scope.
forbid (principal, action, resource) when { context.delegated && !context.scope.contains(context.permission) };
// Nobody decides a matter they are a party to.
forbid (principal, action, resource)
  when { context.parties.contains(context.user) || context.parties.contains(context.signer) };
// A banned member can still read, but changes nothing.
forbid (principal, action, resource) when { principal.banned && !context.read };
// Extensions and codices reach only the operations their manifest declares.
forbid (principal, action, resource)
  when { (context.origin == "extension" || context.origin == "codex") && !context.declared.contains(context.op) };
```

When Cedar says no, `reason()` turns the context into a readable message, such as
`proposal_vote needs proposal.vote`, `resolution_issue must be done by a human, not an agent` or
`dispute_accept: you are a party to this matter`.

## Where permissions come from

`permissions(principal)` builds the principal's set fresh for each call:

| Source | Grants |
|---|---|
| A controller of the canister | `all` |
| The installer canister (`Realm.installer_canister_id`) | `all` |
| The anonymous principal | nothing (only `public` operations work) |
| Everyone else | `self.join` |
| The user's profiles (`admin`, `member`, …) | each profile's `allowed_to` |
| Direct grants (`permission_grant`) | `User.permissions` |
| A guest from another quarter | `GuestUser.permissions` |
| A canister registered as a quarter | `quarter.sync` |
| Each **active** appointment | the position's profile, the department's permissions, and the operations of `Authority` rows granted to the department |

Profiles are created at setup ([`api/setup.py`](../../src/realm_backend/api/setup.py)): `admin`
holds `all`; `member` holds the everyday permissions; `founder` holds the setup permissions and is
removed when the realm launches.

## Special cases

- **Anonymous callers** are refused with `sign in first` before Cedar runs, unless the operation is
  `public`.
- **Replays** (a passed proposal running its action, origin `replay`) skip authorization: the vote
  was the authorization. The original caller was checked when they filed it.
- **Extensions** call as the user (origin `extension`). They need the user's permission *and*
  the operation in their manifest. An extension can never do more than the user could.
- **Codices** call as the realm itself (origin `codex`). The third permit lets them through, but
  only for declared operations, and the guardrails still apply.

## Signing permissions

The GGG spec lists the signing permissions (§13), for example `identity.review`, `license.issue`,
`dispute.accept`, `resolution.issue`, `token.freeze` and `bid.evaluate`. An operation with one of
them is declared `@op(..., signing=True)`, and the guardrails keep agents and delegates away from it.

## Testing a rule

The unit tests decide with [`cedarpy`](https://pypi.org/project/cedarpy/), the same Cedar engine,
loaded with the same `SCHEMA`, `GUARDRAILS` and `POLICIES`. In the canister, `decide` uses
`ic_basilisk_toolkit.cedar`. A rule is tested by calling an operation. From
[`tests/ggg/test_kernel.py`](../../tests/ggg/test_kernel.py), where `t_sign` and `t_decide` are test
operations declared `signing=True` and with `parties`:

```python
def test_agent_never_signs():
    member("bot", "credential.sign")
    Appointment(id="1", user="bot", agent=True, position=Position(id="clinic.records"))
    assert "human" in denied("t_sign", "bot")


def test_party_cannot_decide():
    member("elena", "dispute.accept")
    assert "party" in denied("t_decide", "elena", claimant="elena")
    ok("t_decide", "elena", claimant="bob")
```

See [Testing](../development/testing.md).
