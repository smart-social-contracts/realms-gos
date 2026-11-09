# Stored entity schemas

Each `*.json` file here is the entity schema a realm in the field stores (`_system/_schema`, ic-python-db's
`build_schema` output at its last upgrade). `tests/ggg/test_schema_upgrade.py` checks, for every file, that this
release's entities may replace it: the check a realm runs in `post_upgrade`, which rejects the upgrade when a field
without a default is added to an entity with no `migrate()`.

| File | Taken from |
|---|---|
| `deployed.json` | the wasm staging runs since 8 Nov 2026: realms-gos `b765793f`, wave 1 plus power-of-attorney phase 0 (`local-b765793f`), after an upgrade over itself |

## Why a file

A realm installed fresh stores no schema, so an upgrade test that starts from a previous wasm never meets the check;
only a realm that was upgraded before does, and the staging realm was (realms-gos#620: `Event.grant`, `Land.area` and
`Land.assessed_value` would have made its upgrade fail). CI cannot start from a deployed wasm, so it starts from its
schema.

## Refresh it at each staging or production deploy

The file must be the schema of the oldest build still running anywhere that is upgraded to this branch. After a deploy
succeeds, the realm stores the schema of the build it now runs: replace the file with that (or add a file per
environment, `staging.json`, `production.json`: the test runs on every file).

From the wasm the realm runs, at the root of a worktree (PocketIC, no access to the realm):

```python
import sys
sys.path.insert(0, "tests/deployed")
from pic import Canister

realm = Canister("path/to/realm_backend.wasm", installer="snapshot")
realm.wait(600)
realm.upgrade()  # an upgrade stores the schema; an installed canister has none yet
print(realm.shell("import json\nfrom ic_python_db import Database\n"
                  "print(json.dumps(Database.get_instance().load('_system', '_schema'), sort_keys=True, indent=1))"))
```

Save the printed JSON as the file. The same two lines of Python read the schema straight from a live realm
through `__shell__` (an update call a controller makes; it writes nothing).

Run `tests/ggg/test_schema_upgrade.py` after replacing a file. A file that was refreshed too early (from a build that
already has a new field) hides the problem the next deploy would meet.
