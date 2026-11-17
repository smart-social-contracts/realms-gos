# Codices

A **codex** is a realm's laws and settings as code: the fees, the departments and courts it starts
with, what happens when someone joins or when the realm enters a new stage. Each realm runs exactly
one codex. The first-party ones are in
[realms-codices](https://github.com/smart-social-contracts/realms-codices) (the `codices/`
submodule): `agora`, `dominion`, `syntropia`.

| Page | What it covers |
|---|---|
| [SDK](sdk.md) | `ggg_sdk`: `hook`, `realm`, and the helpers |
| [Build, test, publish](build-test-publish.md) | From an empty folder to a codex running in a realm |
| [The layered codex](layered-codex.md) | The next design, not built yet: legislation from several sources as chapters, pinned by checksum and installed by vote |
| [The signed official journal](journal-signature.md) | How each closed issue of a realm's journal is signed by its canister, and how anyone verifies it |

## The package

```text
my_codex/
├── manifest.json      id, version, kind: codex, codex_api_version: 2, hooks, capabilities,
│                      configuration (fees, governance, membership, …) and seed data
└── backend/
    └── hooks.py       the hooks, one self-contained module
```

## A hook

```python
from ggg_sdk import hook, realm


@hook
def on_user_register(args):
    fee = realm.setting("fees.registration", 0)
    if realm.stage in ("beta", "production") and fee > 0:
        invoice = realm.invoice(args["user"], fee, "registration")
        realm.notify(args["user"], "Welcome", "Please settle your registration invoice.")
        return {"invoice": invoice["invoice"]}
    realm.notify(args["user"], "Welcome", "Nothing to pay for now.")
    return {"stage": realm.stage}
```

A hook **reads** (the configuration, the realm's facts, read operations) and **returns effects**:
the operations it wants run. The realm applies them after the hook ends, through the normal
operations, checked by Cedar. See [Sandboxing](../architecture/sandboxing.md#codex-hooks).

## Hooks (codex_api_version 2)

All are optional. The manifest's `hooks` lists the ones the codex implements.

| Hook | Fired by | `args` | Returns |
|---|---|---|---|
| `init` | `codex_install`, after activating the codex | `{}` | anything |
| `on_user_register` | `realm_join`, after someone joins | `{"user"}` | anything |
| `on_stage_change` | a change of the realm's stage | `{"stage"}` | anything |
| `assign_quarter` | `join_targets`, in a federation | `{"user", "quarters": [{"id", "name", "population", …}]}` | `{"quarter": "<id>"}`, one of the offered ids; anything else keeps the default |

Today only `setup_launch` changes the stage (to `alpha`). The later stages of the lifecycle
(`beta`, `production`, …, GGG §10) have no operation yet, so a hook written for them, like
agora's billing at `beta`, does not fire.

`init` should be **idempotent**: it runs again on every install of the codex, including upgrades
and reverts.

A failing hook never fails the operation that fired it. `codex_status` then reports the codex as
unhealthy, with the error, until a hook succeeds again. However the hook fails counts: an error inside it,
a function the code does not define, a signature that does not take what the realm gives it (`init(args,
context)`), a spent budget.

The same holds for `init` when a codex is installed: a failing `init` never undoes the install. The switch
stands, the call (and the proposal that carried it) succeeds with the hook's failure in `init`, and the codex
is unhealthy with the reason until a hook succeeds. A manifest that lists a hook its code does not
define as a function of its own (`def` or `async def`, decorated or not, at the top of the module: not a method, not
nested in another function, not assigned or imported) is refused before anything is installed: such a hook could
never run. It is read from the compiled code, so it comes after the trust policy: `codex_install` checks it once the
code is trusted, and the filing of an install (and a call's own check) only for code the realm takes without asking
anyone (no approval required, or the code's checksum pinned), as it compiles no other. A codex of the file registry
(`codex` and `version`) is fetched by the call, so it is checked there only. What it fetches is held to what a sent
package is: a manifest that is not JSON, is not a record that is not empty, nests lists or records more than 100
levels deep, or whose `id` and `version` are not text or whose `hooks` and `capabilities` are not lists of text, and
code that is not UTF-8 or a file that is not base64, are refused with the file named. So is a reply of the registry
that is not a record, a call to it that fails (it ran out of instructions on a large file, or its reply is over the
limit), and a reply that holds a lone surrogate: the install fails, with the reason recorded, and the vote or the
timer that ran it concludes. An extension's install (`extension_install`) reads its manifest the same way (it nests no deeper than a codex's may), and the frontend it copies from the registry: the listing, each chunk and the store call to the realm's frontend are held to a record or a list, a count and base64, or refused with the file named. The checks
are the same on the canister, whose JSON and base64 are its own and stop at limits of their own: whatever they raise
is caught, and the shapes are tested there (`tests/deployed/test_registry_path.py`).

## Capabilities

`capabilities` in the manifest lists every operation the codex may use, by name:

```json
"capabilities": ["user_list", "department_list", "realm_configure", "department_create",
                 "invoice_create", "notification_send", "payroll_run"]
```

- **Read** operations in the list (`user_list`) can be called during the hook with `realm.call`.
  At most 32.
- The others (`invoice_create`) can only be returned as effects.
- Anything not listed is refused by Cedar's guardrail.

A codex acts as the realm itself, so its capabilities are powerful: this list is what a reviewer
and a voting member should read first.

## Configuration

Every other block of the manifest (`fees`, `governance`, `membership`, `onboarding`, `seed`, …) is
configuration the hooks read with `realm.setting("fees.registration")`. A realm's
`manifest_data.config_overrides` are merged over it, so a realm can change values without changing
the codex (no operation edits them yet).
`parameters` describes the values a founder may tune in the setup wizard:

```json
"parameters": [
  { "path": "governance.voting_window_days", "label": "Voting window (days)",
    "type": "number", "default": 7, "min": 0.0001 }
]
```

## In a realm

| Operation | Governed | Does |
|---|---|---|
| `codex_install {codex, version}` | realm-wide | Fetches `ext/<id>/<version>/` (manifest + `backend/hooks.py`), checks the trust policy, activates it, runs `init`. The replaced codex is kept. |
| `codex_install {manifest, source}` | realm-wide | The same from inline code; its checksum must be pinned in the trust policy. |
| `codex_revert` | realm-wide | Back to the codex it replaced. |
| `codex_uninstall` | realm-wide | Run with no codex. |
| `codex_status` | read | The active codex, its health, hooks, effective configuration, and the install history. A layered codex shows its layers, chapters and `activates_at`; `pending` is the layered codex waiting for its time and `job` the latest install job. |

`codex_install` installs only the codex. Its `dependencies` (extensions) are installed separately;
`realms local setup --codex <id>` does both.
