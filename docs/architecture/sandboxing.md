# Sandboxing

A realm runs three kinds of third-party code: **codex hooks** (a legacy codex), the **chapters** of a layered
codex (the realm's legislation) and **extension backends** (features). None runs in the realm's own
interpreter. A hook or an extension call starts a fresh Python **subinterpreter**, gives it a tiny API, meters
it, and throws it away; a layered codex keeps one open across calls (below).

Extension **frontends** are contained separately, in the browser (see the
[end of this page](#extension-frontends)).

The code is [`core/sandbox.py`](../../src/realm_backend/core/sandbox.py), about 110 lines.

## What the sandbox guarantees

| Guarantee | How |
|---|---|
| No access to realm data | The subinterpreter has its own modules and globals. It never sees the database, `ggg` or `core`. |
| Only declared operations | The only way out is `rpc(action, **kw)`, and the host passes an allow-list of actions (at most 32). |
| Bounded cost | A budget of 10,000,000 bytecode instructions per call. Metering counts instructions, not time, so every replica gets the same result. |
| Only plain data crosses | Arguments and results go through `json.loads(json.dumps(...))`. No objects, functions or references leak either way. |
| Only approved code runs | The code's hash is approved just before the spawn and revoked right after. |
| No state between calls | The subinterpreter is closed after the call. |

## One call, step by step

```python
def execute(source, fn, kwargs, rpc, allowed=()):
    text = LOADER + source                       # LOADER installs ggg_sdk first
    digest = _basilisk_sandbox.sha256(text)
    _basilisk_sandbox.approve_hash(digest)
    handle = _basilisk_sandbox.spawn_subinterpreter(text, digest, "", tuple(allowed), handler, BUDGET)
    out = _basilisk_sandbox.call_in_subinterpreter(handle, fn, plain(kwargs))
    _basilisk_sandbox.close_subinterpreter(handle)
    _basilisk_sandbox.revoke_hash(digest)
    return plain(out)
```

`_basilisk_sandbox` is a module built into the
[Basilisk](https://github.com/smart-social-contracts/basilisk) runtime. In unit tests it does not
exist, and `local()` runs the same code in a fresh namespace with the same `ggg_sdk` and `rpc`, so
tests exercise the real hook code.

## Codex hooks

A codex hook does not change the realm directly. It **reads** and **returns effects**:

```mermaid
sequenceDiagram
    participant Op as operation (e.g. realm_join)
    participant S as sandbox
    participant H as hook (subinterpreter)
    Op->>S: codex.hook("on_user_register", {user})
    S->>H: hook(args, context)
    H->>S: rpc("user_list", …)  (read ops only)
    S-->>H: rows
    H-->>S: {ok, result, effects: [[op, args], …]}
    loop each effect
        S->>Op: dispatch(op, args, origin="codex")
    end
```

1. The realm gathers the **context**: the codex's configuration (its manifest with the realm's
   overrides), `now`, and a few realm fields (name, status, currency, is it a capital…).
2. The hook runs. It may call the **read** operations it declared, through `realm.call(...)`.
3. It returns **effects**: a list of `[operation, args]` to apply.
4. After the subinterpreter is gone, the realm applies each effect through `dispatch`, with origin
   `codex` and the realm as signer. Cedar checks each one: it must be declared, and the guardrails
   apply.
5. An effect can refer to the result of an earlier one: `realm.invoice(...)["invoice"]` is the
   placeholder `$eff:0:invoice`, which the realm replaces with the real id.

Why effects and not direct calls? A hook's writes then go through the exact path a user's do:
the same checks, the same audit `Event`, the same governance. And the hook's run is pure, so it
is easy to test (see [codices](../codices/build-test-publish.md)).

A failing hook never fails the operation that fired it. The realm records the problem, and
`codex_status` reports the codex as unhealthy until a hook succeeds again.

## Layered codex chapters

The chapters of a [layered codex](../codices/layered-codex.md) run in a subinterpreter too, but one that stays
open: `core/legislation.py` spawns it once per active codex (after an upgrade, or on first use) with the program
`core/legislation_sandbox.py` plus the sources of the library `realms.legislation`, which the subinterpreter
imports from text, since it has no path to import from. The program loads every chapter top to bottom (the
library's loader checks the readable subset and applies the marks), then answers each call: the guards before a
GGG operation, the hooks after it, a `compute_` during it. `core/sandbox.py`'s `Resident` is the handle; it is
closed at a switch of codex, and a call that exhausts the budget closes it too, after which the codex is loaded
again and asked once more.

| Guarantee | How |
|---|---|
| No access to realm data | The same subinterpreter isolation as above. Chapters import a `ggg` module the program builds: one read-only view per entity, whose `get`, `each` and `find` go out through `rpc("ggg_read", ...)` and come back as plain data. The realm answers for the entities and fields of an allowlist only (`VIEWS` in `core/legislation.py`): what legislation needs to decide, never a key, a code, a person's private data, a sealed bid, who voted what, a grant, a payment account or what one member was told. The arguments and results of an operation reach a chapter without its key material and credentials (`secret=` on the operation), and so do the replies of its effects; the personal data the audit keeps out (`private=`) still reaches the legislation that decides on it; no read is answered while the codex loads. |
| Two ways out | The allow-list is `ggg_read` and `local_day` (the realm's local day of a timestamp). Writes are effects: `self.ggg.<op>(...)` queues `[op, args]`, returned to the realm and applied in order through `dispatch` with origin `codex`, stopping at the first that fails. A governed operation is never run, whatever its department's policy: it becomes a proposal the realm files; a layered codex holds no permission to hand out (a legacy codex's effects carry a mark only the legacy hook path sets, and keep acting for the realm). A reserved operation, anything about a proposal or a federal vote on one, and a time zone are refused; whatever else an effect fails with counts against the chapter, never the caller. |
| Bounded cost | Two meters. The sandbox's bytecode budget is per spawn and spans every call on the handle; a spent interpreter is replaced and the codex loaded again. The realm meters the reads a call makes on its own side (`READS` instructions, 2 B) and refuses the rest, so a chapter that loops on reads costs the caller that much and the call fails. The hooks of an operation run one domain per call, so either failure costs that domain its call and no other domain its effects, never the operation that fired them. |
| Only plain data crosses | Between host and sandbox, the marshaller; between one domain's chapter and another's inside the sandbox, the library's `Public` view (public members only, results that are plain data or the view). |

What the sandbox does not do is keep one chapter from another: the chapters of one codex share the
interpreter, so the marks, the frozen settings and events and the sealed SDK classes are the library's job
(realms-gos#512 reviews it).

## Extension backends

An extension backend is a Python module with plain functions. `extension_call` runs one:

```python
@op("extension.call")
def extension_call(c, args):
    ext = find(Extension, args["extension"])
    if not ext.source:
        raise Fail(f"{ext.id} has no backend")
    if not available(ext, holds(c.user)):
        raise Forbidden(f"{ext.id} is not available to you")

    def rpc(name, payload):
        return json.loads(run(dispatch(name, json.dumps(payload), origin="extension", signer=c.user,
                                       declared=ext.operations)))

    return {"result": sandbox.execute(ext.source, args["function"], args.get("args") or {}, rpc, ext.operations)}
```

Unlike a codex, an extension acts **as the user** who called it. Each `rpc(...)` is a full
operation call: Cedar checks that the user holds the permission and that the extension declared
the operation. An extension can do less than its user, never more.

## Determinism

Every replica of the canister must compute the same result. So the sandbox has no clock (use
`realm.now`), no randomness, no network and no file system. Python's `hashlib` is not in the
canister, and `re` has no `compile`. Write plain Python over plain data.

## Extension frontends

Extension UIs run in the browser, inside the realm's frontend:

- **Sandboxed** (`"runtime": "sandboxed"` in the manifest): an `<iframe sandbox="allow-scripts
  allow-modals">`. It has an opaque origin, so it cannot read the realm's cookies, storage or
  identity. It talks to the host only through a `postMessage` bridge, which allows only the
  manifest's operations, with rate limits.
- **In-process**: an ES module mounted into the page, with full access to it. Meant for
  first-party extensions. A build sets the allow-list in `VITE_PRIVILEGED_EXTENSIONS`; any other
  extension must be sandboxed. When the variable is unset, every extension may run in-process.

Details are in [Frontend](frontend.md).
