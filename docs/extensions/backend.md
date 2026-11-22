# Extension backend

An extension backend is one Python file, `backend/entry.py`, of plain functions. The realm runs a
function when someone calls `extension_call`, in a fresh sandbox, as that person.

## Example

From the realm's tests ([`tests/ggg/test_extensions.py`](../../tests/ggg/test_extensions.py)):

```python
# backend/entry.py
def cast_vote(proposal, choice):
    return rpc("proposal_vote", proposal=proposal, choice=choice)


def quorum(percent):
    return rpc("governance_update", voting_quorum=percent * 1_000_000)  # ratios are in hundred-millionths
```

```json
{ "name": "voting", "version": "2.1.0", "backend": true,
  "operations": ["proposal_vote", "governance_update"] }
```

Calling it:

```bash
realms local call voting cast_vote '{"proposal": "P-7", "choice": "yes"}'
```

which runs the operation

```json
extension_call {"extension": "voting", "function": "cast_vote",
                "args": {"proposal": "P-7", "choice": "yes"}}
```

and replies `{"ok": true, "result": <what cast_vote returned>}`.

## The rules

| Rule | Detail |
|---|---|
| Functions take keyword arguments | `args` of `extension_call` become the keyword arguments. Use defaults for optional ones. Only public functions can be called: a name that starts with `_` is refused. |
| Return plain data | dicts, lists, strings, numbers, booleans, `None`. Anything else does not cross the sandbox. |
| `rpc(op, **kwargs)` runs an operation at once | It returns the operation's reply, `{"ok": true, ...}` or `{"ok": false, "error": ...}`; check `ok`. It cannot wait: an operation that calls another canister (a ledger, another realm) fails with "a query cannot call other canisters". |
| `reply = yield wait(op, **kwargs)` waits for one | The function becomes a generator: the realm runs the operation, awaits the other canister, and resumes the function with the reply. What the function did before the wait is kept, as a message keeps what it did before an await. At most 4 calls wait at once in a realm (the canister has 8 interpreters). |
| Only declared operations | `rpc` on an operation not in the manifest's `operations` raises `PermissionError` inside the function, and so does `wait`. |
| As the caller | Each call runs with the caller's permissions. Above, `quorum` works for an admin and fails for Alice with `governance_update needs governance.update`. |
| No state between calls | Each call starts a fresh interpreter. Keep state in the realm, through operations. |
| Budget | 10 million bytecode instructions per call, for the function's own Python; the operations it calls are the realm's work, outside the meter. |
| Deterministic | No clock, randomness, network or files. Built-in modules only. |

If the function raises before it changed anything, the call replies `{"ok": false, "error": "<extension>.<function> failed: ..."}`. If it raises after an operation that changed the realm, the call traps, and the realm rolls back what the message did: a function with several `rpc` calls is all-or-nothing, up to its first `wait`.

## As a query: `extension_read`

`extension_read` runs the same function as a query: free, and faster. Its `rpc` reaches only the read operations the
extension declares, and it cannot `wait`. An empty function costs about 73 M instructions as a query and 97 M as an
update (of the 5 B a query and the 40 B an update may run); one that imports `ggg_sdk` loads it first, about 46 M more.

## Where it fits

A screen reaches the realm only through its extension's backend: in the sandboxed iframe its `read(fn, args)` runs
`fn` through `extension_read` and its `run(fn, args)` through `extension_call` ([Frontend](frontend.md)). So the
operations a screen uses are the backend's, the realm enforces the manifest's list, and the audit log names the
extension. A backend for a screen that only calls operations is one function per operation, by its name:

```python
def treasury_view(**args):
    return rpc("treasury_view", **args)


def transfer_create(**args):
    return (yield wait("transfer_create", **args))
```

`scripts/extension_backend.py <extension dir>` writes that file from the manifest, `rpc` for the read operations and
`wait` for the rest; functions of the extension's own (several operations for one action, a summary computed on the
realm's side) go below them.

Who can call: anyone who holds `extension.call` (every member does) and to whom the extension is available
(`extension_set_access`).
