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

// realm operations, as the user, if declared in the manifest's "operations"
const { proposals } = await realm.read('proposal_list', { status: 'voting' });
const reply = await realm.run('proposal_vote', { proposal: 'P-7', choice: 'yes' });
if (!reply.ok) realm.notify('error', reply.error);

// your own backend (capability "call_extension", function listed in entry_access.functions)
const result = await realm.callExtension('cast_vote', { proposal: 'P-7', choice: 'yes' });
```

`read` and `run` resolve to the realm's reply, `{ ok: true, ... }` or `{ ok: false, error }`. A
refused bridge request (undeclared operation, rate limit, no handshake) rejects with an `Error`
that has a `code`.

## The client

| Member | Use |
|---|---|
| `read(name, args)` | A read operation (query). |
| `run(name, args)` | An operation that changes the realm (update). |
| `callExtension(fn, args)` | A function of the extension's backend. |
| `callExtensionAsync(fn, args, { timeoutMs })` | The same, for long calls: the host runs it in the background and pushes the result (default timeout 60 s). |
| `onStateChange(listener)` | Host state: `principal`, `locale`, `theme` (`light`/`dark`), `realmInfo`. Returns an unsubscribe function. |
| `notify(level, message)` | A toast: `info`, `success` or `error`. |
| `openModal({ title, body, actions })` | A host dialog; resolves to `{ actionId }`. |
| `navigate(path)` | Move the realm UI to another route. |
| `reportHeight(px)` | Resize the iframe to fit the content. |
| `destroy()` | Detach. |
| `extensionId`, `capabilities` | From the handshake. |

The host applies its CSS variables to your document, so the realm's theme colours are available
as `var(--…)`.

## Keeping the iframe the right size

```ts
const observer = new ResizeObserver(() => realm.reportHeight(document.body.scrollHeight));
observer.observe(document.body);
```

## Limits

- 30 calls, 10 notifications, 10 navigations, 30 resizes and 5 modals per 10 seconds.
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
