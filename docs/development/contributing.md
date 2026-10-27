# Contributing

## Where things are

| Path | What |
|---|---|
| [`src/realm_backend/`](../../src/realm_backend) | The realm: `core/` (dispatch, Cedar, sandbox, SDK, chain), `api/` (operations, one module per area), `ggg/` (entities) |
| [`src/realm_frontend/`](../../src/realm_frontend) | The realm's web app (SvelteKit) |
| [`src/`](../../src), other folders | The marketplace, installer, registry and dashboard canisters ([Architecture](../architecture/README.md)) |
| [`packages/`](../../packages) | npm packages for extension UIs: `extension-bridge`, `extension-ui`, `create-extension` |
| [`cli/`](../../cli) | The `realms` CLI |
| [`tests/ggg/`](../../tests/ggg) | The backend's tests |
| [`docs/`](..) | This documentation; [`docs/ggg/spec.md`](../ggg/spec.md) is the specification |
| `extensions/`, `codices/` | Submodules: [realms-extensions](https://github.com/smart-social-contracts/realms-extensions), [realms-codices](https://github.com/smart-social-contracts/realms-codices) |

Work happens on branches and reaches `main` through pull requests. Changes to the submodules are
committed in their own repositories first, then the submodule reference is updated here.

## The spec comes first

[GGG](../ggg/README.md) is the contract between the realm, extensions, codices and agents. A
change to what the realm stores or does starts with the spec:

1. Add or change the row in [`spec.md`](../ggg/spec.md):

   ```markdown
   | `token_force_transfer`, `token_freeze` | `token.force_transfer`, `token.freeze` | signing, governed | Court-ordered restitution: … |
   ```

2. Write the operation to match: same name, permission and flags (`read`, `delegable`, `governed`,
   `signing`, or `(public)`). See [Operations](../architecture/operations.md).
3. Run `pytest tests/ggg/test_spec.py`. It parses the tables and fails on any operation that is in
   one but not the other, or whose permission or flags differ.

## Rules for the backend

The canister runs CPython on [Basilisk](https://github.com/smart-social-contracts/basilisk), and
every line costs review time and cycles. These rules keep it small and checkable.

**Size.** `src/realm_backend` has a hard budget of 10,000 lines (about 6,600 today). Check it with:

```bash
python scripts/loc_budget.py      # every module's size, then the total; fails over budget
```

**Style** (from the [refactor plan](../project/refactor-plan.md#style-rules)):

- Domain modules ≤ 600 lines; short functions; names that say what they do.
- Comments only where the code cannot say it.
- Imports at the top. A lazy import only where Basilisk needs it, with a one-line reason.
- No defensive code for impossible states. Validate input once, at the operation (`find`, `args[...]`).
- No demo or test code in the canister.

**Enforced by [`test_canister_rules.py`](../../tests/ggg/test_canister_rules.py):**

| Rule | Why |
|---|---|
| Every operation has a docstring that names each argument it reads, as ``` ``arg`` ``` | `ops_list` serves these docstrings: they are how agents and developers learn the API |
| No `import re` | The canister's `re` has no `compile` |
| No read loads a whole collection (`instances()`, `load_some(1, max_id())`) | It works until the collection grows, then fails for good. Page with `from`/`count`/`next`. |
| `core/` does not import `api/` (except `op.py`) | `core` is the kernel; the operations sit on top |
| Every module in `api/` is in `op.MODULES` | The canister loads them lazily, by name |
| Only `op.py` and `main.py` read `OPS`; others use `op.ops()` | `OPS` may be partly loaded |

**Authorization** is only Cedar. An operation declares its permission and flags; it never checks
roles itself. See [Authorization](../architecture/authorization.md).

## Documentation

- A change to behaviour updates the page that describes it, in the same pull request.
- Plain Markdown that reads well on GitHub: relative links, fenced code, Mermaid for diagrams.
- Link to code by path (`../../src/realm_backend/core/auth.py`), not by line.
- Snippets must be real: copied from the code or tests, or run before committing.

## Pull requests

- [ ] `pytest tests/ggg` passes ([Testing](testing.md))
- [ ] `python scripts/loc_budget.py` is under budget
- [ ] The spec and the docs match the change
- [ ] Frontend changes: `npm run check` and `npm run test:unit` in `src/realm_frontend`
