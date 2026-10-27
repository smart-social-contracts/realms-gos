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
unhealthy, with the error, until a hook succeeds again.

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
| `codex_status` | read | The active codex, its health, hooks, effective configuration, and the install history. |

`codex_install` installs only the codex. Its `dependencies` (extensions) are installed separately;
`realms local setup --codex <id>` does both.
