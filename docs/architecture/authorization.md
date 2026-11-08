# Authorization

Every call to every operation is decided by one [Cedar](https://www.cedarpolicy.com) policy set.
There is no other check: no `if is_admin` in the operations, and no back door. The schema, the
policies and the code that builds each request are in
[`core/auth.py`](../../src/realm_backend/core/auth.py), about 220 lines; the facts of a layered codex's forbids are
built in [`core/forbids.py`](../../src/realm_backend/core/forbids.py).

## The request

For each call, `dispatch` asks Cedar one question: *may this principal `call` the realm, in this
context?*

- **Principal**: `Realm::User::"<the user>"`, with two attributes: `permissions` (the set the user
  holds, see below) and `banned`.
- **Action**: `Realm::Action::"call"`. The permission decision is always asked on this action; the operation is in
  the context. A layered codex's forbids are asked on actions of their own, below.
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
permit (principal, action in Realm::Action::"call", resource) when { context.public };

// A user who holds the permission (or "all") may call the operation.
permit (principal, action in Realm::Action::"call", resource)
  when { principal.permissions.contains("all") || principal.permissions.contains(context.permission) };

// A codex may call the operations it declared.
permit (principal, action in Realm::Action::"call", resource)
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

## The forbids of a layered codex

A [layered codex](../codices/layered-codex.md) can add `forbid` policies, generated from the `forbid(...)` rules
of its chapters (§6, §9.4). They are loaded after `GUARDRAILS + POLICIES` when the codex becomes the running one,
parsed once, and name a Cedar action each: the operation (`tax_policy_propose`) or one of its lists
(`tax_policy_propose.taxes`, `tax_policy_draft.taxes`), all `in [Realm::Action::"call"]`, so the permits and guardrails
above cover them too. The facts of a forbid are the operation's arguments as GGG defines them
([`ggg_facts.py`](../../src/realm_backend/ggg_facts.py)) and the facts the operation derives (`derived=` on `@op`).

After the permission decision, `dispatch` calls `auth.refuse_forbidden`, which asks, for the operation, one request
per forbid-named action (one per element of a list), under the context `origin: "forbids"` that carries only the
operation and its facts. It asks nothing when no forbid names the operation, so a realm with a legacy codex or
none decides as before. It follows the replay of an accepted proposal as well, which has no permission decision of its
own (the vote was it): the forbids of the legislation in force still apply, and a replay one refuses ends the
proposal `failed`. No forbid can name a reserved operation, and none is asked of a reserved operation or of a
proposal about one.

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
  was the authorization. The generated forbids of a layered codex still apply to them (above). The original
  caller was checked when they filed it. A proposal a delegate filed is checked again when it runs: the grant
  it was filed under must not have been revoked or replaced, and must still be able to make the call
  (`proposals.check_grant`). An expired grant is enough.
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
over the policies and schema in force (`auth.live`: `SCHEMA`, `GUARDRAILS` and `POLICIES`, and the generated forbids
of the running layered codex). In the canister, `decide` uses
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
