# Extension frontend

An extension's UI is a small web app. The realm shows it in a sandboxed iframe on the page
`/extensions/<id>`, and the two talk through
[`@realmsgos/extension-bridge`](../../packages/extension-bridge).

## The shape

- Any framework, or none. The scaffold uses Svelte 5, Vite and Tailwind.
- Build output in `frontend/dist/`, with `index.html` as the entry. Use relative asset paths
  (Vite: `base: './'`), because the app is served from `/ext/<id>/<version>/frontend/dist/`.
- Manifest: `"runtime": "sandboxed"`, `"sdk_version": "1"`.

## Talking to the realm

```ts
import { createExtensionClient } from '@realmsgos/extension-bridge';

const realm = await createExtensionClient();          // waits for the host handshake

// functions of the extension's own backend: the screen has no other way to the realm
const { proposals } = await realm.read('proposal_list', { status: 'voting' });   // a query: free, reads only
const reply = await realm.run('proposal_vote', { proposal: 'P-7', choice: 'yes' }); // an update
if (!reply.ok) realm.notify('error', reply.error);
```

`read(fn, args)` runs `fn` of the extension's backend as a query (`extension_read`), `run(fn, args)` as an update
(`extension_call`); each resolves to what the function returns. The backend runs as the user, and calls only the
operations the manifest declares ([Backend](backend.md)): a backend of one function per operation, by its name, as
`scripts/extension_backend.py` writes it, makes `read('proposal_list')` read the proposals. A call the realm refuses
(the backend failed, the extension is not available to the user) or the bridge refuses (rate limit, no handshake)
rejects with an `Error` that has a `code`.

An extension whose screen ran in the realm's page mounts the same component in its iframe with
`_shared/frontend/sandbox.ts` (realms-extensions): `ctx.read`, `ctx.run`, `notify`, `navigate`, `openModal`, and the
member's `principal`, `locale` and `userProfiles` as stores.

## The client

| Member | Use |
|---|---|
| `read(fn, args)` | A function of the backend, as a query (`extension_read`). |
| `run(fn, args)` | A function of the backend, as an update (`extension_call`). |
| `callExtension(fn, args)` | The same as `run`. |
| `callExtensionAsync(fn, args, { timeoutMs })` | The same, for long calls: the host runs it in the background and pushes the result (default timeout 60 s). |
| `onStateChange(listener)` | Host state: `principal`, `locale`, `theme` (`light`/`dark`), `realmInfo`. Returns an unsubscribe function. |
| `notify(level, message)` | A toast: `info`, `success` or `error`. |
| `openModal({ title, body, actions })` | A host dialog; resolves to `{ actionId }`. |
| `navigate(path)` | Move the realm UI to another route. |
| `vault.seal(recipients, data)` | Capability `vault`: `data` encrypted with a fresh key, wrapped for each principal in `recipients` with the realm's vetKeys; resolves to `{ ciphertext, keys }` for the backend to store. |
| `vault.open(wrappedKey, ciphertext)` | Capability `vault`: a record sealed for the member, opened with the key wrapped for them; `null` when it cannot be. |
| `saveFile(name, content, { mimeType, base64 })` | Capability `download`: the member saves a file, once the backend allows it (below). Resolves to `{ sha256, size }`. |
| `reportHeight(px)` | Resize the iframe to fit the content. |
| `destroy()` | Detach. |
| `extensionId`, `capabilities` | From the handshake. |

The host applies its CSS variables to your document, so the realm's theme colours are available
as `var(--…)`.

## Encrypted records and files

A sandboxed screen holds no key. With the capability `vault`, the host seals and opens records for it: the member's
vetKey stays in the realm's page, and the frame receives only what it sealed or asked to open. A record is opened with
the key wrapped for the member, which the screen gets from its backend (a document's key envelope, for instance), so a
screen opens only records its backend can read.

The iframe may not start a download. With the capability `download`, the host saves a file for it, but first asks the
extension's backend, as a query, `download_allowed(name, sha256, size)` over the bytes it is about to save, and saves
them only when the function returns `{ "ok": true, "allowed": true }`:

```python
def download_allowed(name, sha256, size):
    # a payment file leaves only once the realm recorded its hash
    sent = rpc("dispatch_list", to="External:bank")
    known = any(f"sha256:{sha256}" in d.get("title", "") for d in sent.get("dispatches", []))
    return {"ok": True, "allowed": known, "reason": "" if known else "no dispatch records this file"}
```

An extension that has nothing to check returns `allowed: True`; one without the function cannot hand out files. Files
are text, or base64 with `{ base64: true }`, of the types `application/xml`, `text/xml`, `text/plain`, `text/csv`,
`application/json` and `application/pdf`, and within the bridge's 256 KB message size.

## Keeping the iframe the right size

```ts
const observer = new ResizeObserver(() => realm.reportHeight(document.body.scrollHeight));
observer.observe(document.body);
```

## Limits

- 30 calls, 10 notifications, 10 navigations, 30 resizes, 5 modals, 60 seals or opens and 5 files per 10 seconds.
- At most 10 calls in flight; messages up to 256 KB.
- The iframe has an opaque origin: no cookies, no `localStorage` shared with the realm, no access to
  the parent page. It never holds the user's signing identity.

## Developing

```bash
cd frontend
npm install
npm run dev        # standalone; the bridge handshake needs the realm host
npm run build      # writes frontend/dist/
```

To see it inside a realm, publish it to a local realm (see
[Build, test, publish](build-test-publish.md)).
