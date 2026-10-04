# REPL and UI must share the same host surface

> **Status:** Closed — host verbs share the UI surface; member-shaped deny/allow tested
> **Issue:** [realms#313](https://github.com/smart-social-contracts/realms-gos/issues/313)
> **App:** `src/realm_backend/core/repl_host.py`, `__shell__`
> **Repo:** smart-social-contracts/realms-gos

The REPL is another **client of the same host surface as the UI**, not a second ORM back door.

---

## Why this exists

Today `__shell__` opens a sandboxed SecureORM REPL. Entity stubs mutate storage through Cedar with `context.repl`. That is a *different* permissioning path from the browser:

| UI (Candid / SPA) | REPL (SecureORM stubs) |
|---|---|
| `ic.caller()` → `@require` / `_check_access` | `SHELL_EXECUTE` to enter, then Cedar `context.repl` |
| Product actions go through `extension_sync_call` → `gate_extension_call` → extension sandbox with `context.extension` | Direct entity create/update/delete, skipping extension `entry_access` |
| Lifecycle / setup / governed-action gates on the host method | Not applied |
| Same denial the member would see in the UI | Different allow/deny (and `from ggg import …` is not a real sandbox import) |

Controllers can enter `__shell__` because `_check_access` bypasses IC controllers. Cedar on ORM RPCs has **no** controller bypass. Stock builds often `fail_open` Cedar, so the REPL is then a controller-shaped hole around RBAC.

That is not an E2E stand-in for permissioning, and it is not a member-equivalent client.

---

## Product invariant

For principal **P**, every UI action (core Candid method, extension, API) has a REPL form that:

1. Runs **as P** (`ic.caller()` of the `__shell__` ingress). No extra privilege. No impersonation. `__shell__` ignores PoA.
2. Hits the **same verbs and gates** as the UI: `@require`, `entry_access` / `gate_extension_call`, Cedar origin like UI (extension origin for extension work; **not** `context.repl` for host verbs), setup / lifecycle / governed-action.
3. Leaves the **same state** or the **same denial**.

`SHELL_EXECUTE` means “may open the REPL client.” It is **not** a superuser bit on the verbs inside.

Denial tests must use **non-controller** identities. Controller PEM is not a founder II session.

---

## REPL namespace (product surface)

Inject source-level stubs that RPC out. Do **not** inject live `ggg` / `core` objects (subinterpreter `sys.path = []`; objects cannot cross interpreters).

```python
api.call("create_profile", "Founder")
api.call("create_profile", name="Founder")
api.methods()                          # candid allowlist minus blocked names

ext.call("voting", "cast_vote", {"proposal_id": "..."})
ext.call_async("notifications", "get_notifications", {})
```

`ext.call` is sugar for `api.call("extension_sync_call", extension, function, args_json)`.

SecureORM entity stubs may remain as an **optional** Cedar-gated debug surface. They are not the product REPL.

---

## Dispatch

`HostSecureORM` wraps toolkit `SecureORM`:

| RPC | Host |
|---|---|
| `host.call` | Look up the Candid method on `main`, allowlist check, `fn(*args, **kwargs)` |
| `host.ext_sync` | `extension_sync_call` |
| `host.ext_async` | `extension_async_call` (drive generators when they do not yield IC calls) |
| `host.list_methods` | Sorted allowlist |

Allowlist = quoted methods in `realm_backend.did` `service : { … }`.

**Blocked** (recursion / HTTP / Candid hack):

- `__shell__`
- `http_request`
- `http_transform`
- `__get_candid_interface_tmp_hack`

C sandbox cap: **32** actions. Six `orm.*` + four `host.*` = 10.

Host RPCs **must not** run Cedar with `context.repl`. `_call_host` wraps the
Candid function in `call_origin.host_call()` (empty origin, same as a browser
ingress). Extension work still goes through `extension_sync_call` so G1/G2 see
`context.extension`. `AccessDenied` is a `PermissionError` so Candid and
`api.call` deny with the same class. Do not unwrap `@require`.

---

## Honest non-equivalents

| Difference | Why it stays |
|---|---|
| II login / asset UX | Browser only |
| `__shell__` is always an **update** | Query Candid from REPL still runs the Python body in an update message |
| `SHELL_EXECUTE` required to open the client | Same as “must load the SPA”; not a verb bypass |
| Async methods that **yield IC calls** | REPL cannot currently resume inter-canister yields; raise a clear error |
| PoA `on_behalf_of` | `__shell__` does not impersonate; pass the same field the UI would, on the host method |

---

## Tests

- DID allowlist includes `extension_sync_call` / `status`; blocked names are rejected.
- `host.call` invokes the host function with positional and keyword args.
- `host.ext_sync` JSON-encodes dict args the same way the SPA does (`JSON.stringify`).
- `__shell__` cannot be called through `host.call`.
- `AccessDenied` from a host method surfaces as `PermissionError`.
- `actions()` includes `host.*` and `orm.*`, length ≤ 32.
- Stub source defines `api` / `ext` on `__builtins__` (and wraps `eval_repl`) so they work even when basilisk binds the first `eval_repl` and `rpc` is a builtin.
- Same principal + args → host dispatch vs direct Candid function → identical result (unit: fake `main` module).
- Member-shaped (non-controller) principal: `api.call` allow matches the decorated Candid method and writes the same state (`tests/backend/test_repl_ui_parity.py`).
- Same principal, insufficient permission: `api.call` and Candid raise the same `AccessDenied` (`PermissionError` subclass); `SHELL_EXECUTE` does not succeed the verb.
- `ext.call` matches `extension_sync_call` / `gate_extension_call` for a real extension verb (`role_manager.generate_registration_url`).

---

## Out of scope for this change

- Changing `ic-basilisk-toolkit` SecureORM internals (wrap in Realms).
- A new in-realm REPL UI.
- Making `__shell__` honour PoA.
- Driving full IC async call tuples from the sandbox.
