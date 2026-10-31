# Extension manifest

`manifest.json` sits at the root of the package. The realm stores it when it installs the
extension and reads it on every call.

## Example

```json
{
  "id": "voting",
  "name": "voting",
  "version": "2.1.0",
  "description": "Vote on open proposals",
  "author": "Realms",

  "profiles": ["member"],
  "show_in_sidebar": true,
  "sidebar_label": { "en": "Voting" },
  "categories": ["governance"],
  "icon": "ballot",

  "operations": ["proposal_list", "proposal_get", "proposal_vote"],
  "backend": true,

  "runtime": "sandboxed",
  "sdk_version": "1",
  "capabilities": ["call_extension", "notify"],
  "entry_access": { "functions": { "cast_vote": "member" } }
}
```

## Fields

### Identity

| Field | Required | Meaning |
|---|---|---|
| `id` | yes* | The extension id: lowercase, `_` allowed. It names the registry namespace `ext/<id>/<version>`. |
| `name` | yes* | Older manifests carry the id here. Publishing uses `id`, then `name`, then the folder name. |
| `version` | yes | Semantic version. Each version is a separate namespace. |
| `description`, `author` | no | Shown in the marketplace and to the host. |

### Access and menu

| Field | Default | Meaning |
|---|---|---|
| `profiles` | `["member"]` | Profiles that get the extension at install. Admins change it later with `extension_set_access` (which can add departments). |
| `show_in_sidebar` | | Show it in the realm's menu. |
| `sidebar_label` | | Menu label per locale: `{"en": "Voting", "es": "Votación"}`. |
| `categories` | | Menu group, e.g. `governance`, `finance`, `other`. |
| `icon` | | Icon name for the menu. |

These are copied at install. Admins change them with `sidebar_configure`.

### What it may call

| Field | Default | Meaning |
|---|---|---|
| `operations` | `[]` | The realm operations the UI and the backend may call, by name. **The only ones.** |
| `backend` | `false` | `true` ships `backend/entry.py`, installed with the extension. |

Declare the fewest operations that work. `ops_list` (`realms local call ops_list`) lists them all
with their permissions.

### UI hosting

| Field | Meaning |
|---|---|
| `runtime` | `"sandboxed"`: the UI runs in an iframe and talks over the bridge. Anything else is mounted in-process, which only privileged first-party extensions may do. |
| `sdk_version` | The bridge protocol version the UI was built for (`"1"`). |
| `capabilities` | Bridge features the UI may use. `call_extension` is needed to call the extension's own backend. |
| `entry_access.functions` | The backend functions the UI may call, as `{"function": "profile"}`. With `call_extension` but no list, every call is refused. |

The realm passes `description`, `runtime`, `sdk_version`, `capabilities`, `entry_access` and
`backend` to the UI host in `extension_list`. Other fields stay on the backend.

## Checksum

The realm trusts the package by its checksum:

```python
sha256(json.dumps(manifest, sort_keys=True) + "\n" + source)    # source: backend/entry.py, or ""
```

Any change to the manifest is a new package and needs a new approval (or pin). See
[Trust](../architecture/trust-and-registry.md).
