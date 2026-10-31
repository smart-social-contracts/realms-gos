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
    return rpc("governance_update", quorum_percent=percent)
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
| Functions take keyword arguments | `args` of `extension_call` become the keyword arguments. Use defaults for optional ones. |
| Return plain data | dicts, lists, strings, numbers, booleans, `None`. Anything else does not cross the sandbox. |
| `rpc(op, **kwargs)` is the only way out | It runs a realm operation and returns its reply, `{"ok": true, ...}` or `{"ok": false, "error": ...}`. It does not raise: check `ok`. |
| Only declared operations | `rpc` on an operation not in the manifest's `operations` returns `{"ok": false, "error": "... is not declared by this extension"}`. |
| As the caller | Each `rpc` runs with the caller's permissions. Above, `quorum` works for an admin and fails for Alice with `governance_update needs governance.update`. |
| No state between calls | Each call starts a fresh interpreter. Keep state in the realm, through operations. |
| Budget | 10 million bytecode instructions per call. |
| Deterministic | No clock, randomness, network or files. Built-in modules only. |

If the function raises an exception, the whole `extension_call` fails and the realm rolls back every
operation it ran. That makes a function with several `rpc` calls all-or-nothing.

## Where it fits

Use a backend when one user action needs several operations, or when the logic should not live
in the browser. For a single operation, call it from the UI with `realm.run(...)` and skip the
backend.

Who can call: anyone who holds `extension.call` (every member does) and to whom the extension is
available (`extension_set_access`). The UI calls backend functions with
`realm.callExtension("cast_vote", {...})`, which also needs the `call_extension` capability and the
function in `entry_access.functions` (see [Manifest](manifest.md)).
