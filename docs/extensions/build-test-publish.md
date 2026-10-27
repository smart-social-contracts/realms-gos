# Build, test and publish an extension

From an empty folder to an extension installed in a realm. You need Python 3.10+, Node 20+ and the
CLI (`pip install realms-gos`).

## 1. Scaffold

```bash
realms new extension hello            # or: npm create realms-extension hello
cd hello
```

You get `manifest.json`, `backend/entry.py` and a Svelte `frontend/`. The scaffold still writes the
previous contract (see [Status of the contract](README.md#status-of-the-contract)); for the current
one, make the files look like this:

```json
{
  "id": "hello",
  "name": "hello",
  "version": "1.0.0",
  "description": "Says hello",
  "profiles": ["member", "admin"],
  "show_in_sidebar": true,
  "sidebar_label": { "en": "Hello" },
  "categories": ["other"],
  "icon": "wand",
  "operations": ["me"],
  "backend": true,
  "runtime": "sandboxed",
  "sdk_version": "1",
  "capabilities": ["call_extension", "notify"],
  "entry_access": { "functions": { "greet": "member" } }
}
```

```python
# backend/entry.py
def greet(name=""):
    if not name:
        me = rpc("me")                    # {"ok": true, "user": {"nickname": ...}, ...}
        name = me["user"].get("nickname") if me["ok"] else ""
    return {"greeting": "Hello, %s!" % (name or "World")}
```

```ts
// in frontend/src/App.svelte
const realm = await createExtensionClient();
const { greeting } = await realm.callExtension('greet', {});
```

## 2. Build the UI

```bash
npm install
npm run build                         # writes frontend/dist/
```

## 3. Test the backend

A backend function is plain Python with one free name, `rpc`. Test it by giving it a fake:

```python
# tests/test_entry.py
import pathlib

def load(replies):
    calls = []
    def rpc(op, **kw):
        calls.append((op, kw))
        return replies[op]
    ns = {"rpc": rpc}
    exec(pathlib.Path("backend/entry.py").read_text(), ns)
    return ns, calls

def test_greet_uses_the_member_nickname():
    ns, calls = load({"me": {"ok": True, "user": {"nickname": "Ana"}}})
    assert ns["greet"]() == {"greeting": "Hello, Ana!"}
    assert [op for op, _ in calls] == ["me"]

def test_greet_takes_a_name():
    ns, calls = load({})
    assert ns["greet"](name="Bob") == {"greeting": "Hello, Bob!"} and calls == []
```

```bash
pytest tests
```

Also check that every operation the backend calls is in `operations`: an undeclared one returns
`{"ok": false, ...}` in a realm.

To test against the real realm logic (Cedar, permissions, sandbox), write a test in realms-gos'
`tests/ggg` the way [`test_extensions.py`](../../tests/ggg/test_extensions.py) does: it serves the
package from a fake registry, installs it and calls it as different members. See
[Testing](../development/testing.md).

## 4. Try it in a local realm

```bash
realms local start                    # once: a local realm, registry and UI
realms local publish ./hello --install
realms local call hello greet '{"name": "Ana"}'
realms local status                   # prints the UI URL; open /extensions/hello
```

`publish` uploads the package to the local file registry; `--install` runs `extension_install`.
The local realm trusts any code, so no review is needed. Publish again after each change, with a
new `version`. See [Getting started](../getting-started.md).

> **Note.** `realms local start` deploys the **released** realm that matches your CLI version
> (`--version` picks another tag). No release has this contract yet (the latest, v0.8.0-rc2,
> predates it), so until the next release the local realm runs the
> [previous contract](README.md#status-of-the-contract). Until then, step 3's realm tests are
> the way to check an extension against the current backend.

## 5. Publish for real realms

Production realms install only code that a trusted approver (by default the marketplace) has
approved, or that they pinned by checksum. See
[Trust, the file registry and the marketplace](../architecture/trust-and-registry.md).

1. **Get a developer licence** and **create the listing** in the marketplace UI.
2. **Upload the files** to `ext/hello/1.0.0` in the file registry. The marketplace UI does this
   (its `prepare_upload` makes you a publisher of the namespace). From the CLI:

   ```bash
   realms extension publish --source-dir ./hello --registry <file_registry id> --network ic --identity <you>
   ```

3. **Review.** A marketplace reviewer approves the listing (`review_listing`), which stamps the
   approval onto the registry namespace.
4. **Install.** A realm runs `extension_install {"extension": "hello", "version": "1.0.0"}`. It is
   governed: in a realm with a root policy, this becomes a vote.

A new version is a new namespace (`ext/hello/1.0.1`) and needs its own approval.

## Checklist

- [ ] `operations` lists exactly what the UI and backend call, nothing more.
- [ ] Backend functions take keyword arguments and return plain data.
- [ ] `entry_access.functions` lists every function the UI calls.
- [ ] `frontend/dist/index.html` is built, with relative asset paths.
- [ ] `version` is bumped for every publish.
