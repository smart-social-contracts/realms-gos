# Operations

An **operation** is a named function of the realm, such as `proposal_vote` or `invoice_create`.
Operations are the only way to read or change a realm. There are a little over 200 of them, in one
registry shared by all callers.

## Calling an operation

The canister has two endpoints, both `(text, text) -> text`:

| Endpoint | Kind | Use for |
|---|---|---|
| `read(name, args)` | query (fast, nothing is kept) | operations flagged `read` |
| `run(name, args)` | update (goes through consensus) | everything else |

`args` and the reply are JSON text. Calling a changing operation with `read` returns
`"<name> changes the realm: call it with run, not read"`.

From the CLI:

```bash
realms local call proposal_list '{"status": "voting"}'          # local realm
realms realm call <realm-id-or-name> me                        # any realm
icp canister call realm_backend run '("proposal_vote", "{\"proposal\": \"P-7\", \"choice\": \"yes\"}")'
```

From the web UI ([`src/lib/realm.ts`](../../src/realm_frontend/src/lib/realm.ts)):

```ts
import { read, run, must } from '$lib/realm';

const me = await must(read('me'));                             // throws RealmError on { ok: false }
await run('proposal_vote', { proposal: 'P-7', choice: 'yes' });
```

## Replies

Every reply is a JSON object with `ok`:

```json
{"ok": true, "invoice": "INV-12", "pay_to": "…"}
{"ok": false, "error": "proposal_vote needs proposal.vote"}
{"ok": true, "proposal": "P-8", "requires_vote": true}
```

The last form is a [governed](governance-and-delegation.md) call that became a proposal instead
of running.

| What went wrong | Reply |
|---|---|
| Unknown operation | `{"ok": false, "error": "unknown operation 'x'"}` |
| Not allowed (Cedar) | `{"ok": false, "error": "<op> needs <permission>"}` and similar |
| Invalid input (`Fail`) | `{"ok": false, "error": "<message>"}` |
| A required argument is missing | `{"ok": false, "error": "missing 'proposal'"}` |
| Any other exception | the call **traps**: every change it made is rolled back |

## Discovering operations

`ops_list` is public and returns every operation with its permission, flags and docstring. The
CLI uses it to choose `read` or `run`; AI tools (MCP) are generated from it.

```bash
realms local call ops_list
```

The [GGG spec](../ggg/spec.md) lists every operation by domain, with examples. A test
(`tests/ggg/test_spec.py`) fails when the code and the spec's tables disagree.

## Writing an operation

An operation is a function in one of the [`api/`](../../src/realm_backend/api) modules, decorated
with `@op`. Its name is the function name. It takes `c` (the call) and `args` (a dict) and returns
a dict.

```python
from core.db import Fail, find
from core.op import op


@op("proposal.vote", delegable=True)
def proposal_vote(c, args):
    """Vote on ``proposal`` before its deadline: ``choice`` is yes, no or abstain. Returns ``vote``."""
    p = find(Proposal, args["proposal"])          # Fail("unknown Proposal 'P-9'") if missing
    if args["choice"] not in CHOICES:
        raise Fail(f"choice must be one of {', '.join(CHOICES)}")
    ...
    return {"vote": v.id}
```

What an operation does **not** contain: permission checks (Cedar does them), `try/except`
(dispatch turns `Fail` into an error reply), JSON handling, response classes, or audit code.

### `@op` arguments

```python
op(perm, read=False, delegable=False, governed=None, signing=False,
   parties=None, public=False, perm_for=None)
```

| Argument | Meaning |
|---|---|
| `perm` | The permission the caller needs, e.g. `"treasury.manage"`. `None` with `public=True`. |
| `read` | Changes nothing; callable through `read`. Not audited. |
| `delegable` | A delegate may call it with `on_behalf_of`. |
| `governed` | `governed(args) -> Department`: that department's policy decides whether the call runs now or becomes a proposal. Use `realm_wide` for the root department. |
| `signing` | Must be done by a human in person: never by an agent, never under delegation. |
| `parties` | `parties(args) -> principals` with a stake in the matter; they may not perform it. |
| `public` | Anyone may call it, including anonymous callers. |
| `perm_for` | `perm_for(args) -> permission`, when the permission depends on the arguments. |

### The call object `c`

| Field | Meaning |
|---|---|
| `c.user` | Who acts: the caller, or the grantor when a delegate passes `on_behalf_of` |
| `c.by` | Who signed the message: the caller (or the delegate) |
| `c.delegated` | `c.user != c.by` |
| `c.origin` | `user`, `extension`, `codex`, `replay` or `shell` |
| `c.proposal` | The passed proposal this call replays, if any |

### Calling other canisters

An operation that calls another canister is a generator: it `yield`s the call, and dispatch drives
it. Such an operation cannot be a `read` (queries cannot make update calls).

```python
@op("token.freeze", signing=True, governed=realm_wide, parties=lambda a: {a.get("account", "")})
def token_freeze(c, args):
    token = find(Token, args["symbol"])
    method = "freeze_account" if args.get("frozen", True) else "unfreeze_account"
    reply = yield from chain.call(token.ledger, method,
                                  f'(record {{ account = {account(args["account"])}; '
                                  f'reason = opt {text(args.get("reason", "")[:64])} }})')
    if outcome(reply) is None:
        raise Fail(f"{method} refused: {reply[:200]}")
    return {"account": args["account"], "frozen": method == "freeze_account"}
```

### Pagination

Lists are paged by id range. Pass `from` (default 1) and `count` (default 100, at most 500); the
reply carries `next`, the `from` of the next page, or `null` at the end.

```python
@op("user.view", read=True)
def user_list(c, args):
    rows, nxt = page(User, args)
    return {"users": [card(u) for u in rows], "next": nxt}
```

A page may hold fewer rows than `count` when a filter drops some; keep going until `next` is null.

### Helpers in [`core/db.py`](../../src/realm_backend/core/db.py)

| Helper | Use |
|---|---|
| `find(Entity, id)` | Load a row or `Fail("unknown Entity 'id'")` |
| `first(Entity, field, value)` | First row with an indexed field value, or `None` |
| `each(Entity, field, value)` | Iterate rows by an indexed field |
| `page`, `page_by` | Paginated lists (above) |
| `next_id(Entity, "P")` | The next readable id, e.g. `P-8` |
| `ref(row)` / `deref("User:alice")` | Polymorphic references as `Entity:id` text |
| `view(row, *fields)` | A plain dict of a row's fields |
| `Fail(message)` | Refuse with a readable error |

## Audit

Every successful call of a non-read operation is stored as an `Event`: operation, user, signer,
origin, arguments and the plain fields of the result, with secrets (`code`, `private_data`,
`payload`, `ciphertext`) removed. The latest 2,000 events are kept. Read them with `events`
(permission `realm.monitor`).

## Calling operations from Python

Inside the backend, `core.op.call(name, args, user, origin)` runs an operation through dispatch
and Cedar and returns its reply as a dict. Sandboxed code uses `rpc(name, **args)` instead; see
[sandboxing](sandboxing.md).

The admin REPL is the `shell` operation (permission `shell.execute`). It evaluates one expression
in the sandbox, where `ops.<name>(args)` calls an operation as you:

```bash
realms local call shell '{"expr": "ops.proposal_list({\"status\": \"voting\"})"}'
```
