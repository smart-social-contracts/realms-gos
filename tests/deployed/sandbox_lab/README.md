# Sandbox lab: an extension at its limits

A small extension you can edit and run in a real realm's wasm on PocketIC. Each run starts a fresh realm, installs the
extension, calls one of its functions for each `n` you give, and says whether the call ran (with the instructions it
spent) or what stopped it.

```
tests/deployed/sandbox_lab/run.sh
```

```
count(n=1,666,633)       runs       2,811,904,455 instructions (7.0% of a message)   result: 1388831945028
count(n=1,666,634)       STOPPED  the sandbox's budget of 10,000,000 bytecode instructions ran out (core/sandbox.py BUDGET)
```

The first run takes about half a minute: it makes a Python environment in `.cache/venv` and packs the realm's wasm into
`.cache/`. After that a run takes about ten seconds.

## What to change

- **`backend/entry.py`**: the extension's code. Any function in it can be called: `./run.sh <function> <n> ...` calls
  `<function>(n=<n>)` once for each `n`. Inside, the extension reaches the realm only through
  `rpc("<operation>", **arguments)`, and only for the operations the manifest declares.
- **`manifest.json`**: `operations` is what the extension may call. An `rpc` to anything else raises `PermissionError`
  inside the extension.

Neither needs a new wasm: the extension is installed when the run starts. `--repack` rebuilds the wasm, which is only
needed after a change under `src/`.

## Commands

| Command | What it does |
|---|---|
| `./run.sh` | `count` for the two `n` either side of the limit |
| `./run.sh count 1000000 2000000` | `count(n=1000000)`, then `count(n=2000000)` |
| `./run.sh rename 1000 1600` | 1,000 then 1,600 calls of the realm operation `profile_update` |
| `./run.sh --find count` | searches for the largest `n` that runs (doubling, then halving the gap) and shows that `n + 1` is stopped |
| `./run.sh --repack ...` | rebuilds the wasm first |

Calls for several `n` run one after another on the same realm, so what an earlier call wrote is still there. A call that
is stopped writes nothing: the whole message is rolled back.

## The two limits

| Limit | What reaches it | Where |
|---|---|---|
| 10,000,000 bytecode instructions per call | the extension's own Python: `count` stops after 1,666,633 turns of its loop, about 2.8 B instructions | `src/realm_backend/core/sandbox.py`, `BUDGET` |
| 40,000,000,000 instructions per message | calls to the realm: the realm's work for an `rpc` runs outside the sandbox's meter, so a loop of them (`rename`, `read`) goes on until the whole message reaches the Internet Computer's limit | the IC's resource limits |

The sandbox's meter is exact, so the same code stops at the same `n` every time. The instruction count can differ by a
few hundred thousand between runs: it includes the canister's own work around the call.

Other things to try:

- An extension that raises an exception, or a function name that does not exist: the message traps.
- An extension that declares more than 32 operations: every call traps.
- Make `entry.py` larger: every call compiles it again, at about 7,000 instructions per byte.

## How it runs

`run.py` calls `extension_call` as the member Alice from inside the canister, through the controller's shell, so that it
can count with `ic.performance_counter`. A message Alice sends from her browser runs the same code, plus the message's
own decoding and reply. The extension is installed by writing the row an install from the file registry would write.
The benchmark behind the numbers is `tests/deployed/test_extension_sandbox_cost.py`, and the report is
`.cto/reports/extension-grant-cost-2026-11-22.md` in the workspace.
