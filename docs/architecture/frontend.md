# Frontend

The realm UI is a [SvelteKit](https://kit.svelte.dev) app in
[`src/realm_frontend`](../../src/realm_frontend), served from an asset canister. It holds no
business rules: every screen reads and changes the realm through operations.

## Calling the realm

All backend calls go through [`src/lib/realm.ts`](../../src/realm_frontend/src/lib/realm.ts):

```ts
import { read, run, quarter, must } from '$lib/realm';

const me = await must(read('me'));                     // throws RealmError when ok is false
const reply = await quarter.run('proposal_vote', { proposal: 'P-7', choice: 'yes' });
if (!reply.ok) alert(reply.error);
```

| Function | Calls |
|---|---|
| `read(name, args)` / `run(name, args)` | the realm this UI belongs to (the capital in a federation) |
| `quarter.read` / `quarter.run` | the active quarter: the user's home quarter (`setActiveQuarter` in `canisters.js`) |
| `on(actor).read` / `.run` | any other realm canister, e.g. a join target |
| `must(reply)` | returns the reply, or throws `RealmError` with the realm's message |

The UI discovers what exists with `read('ops_list')`. Show the realm's error text to the user: the
messages are written for people (`proposal_vote needs proposal.vote`).

## Layout

```text
src/realm_frontend/src/
├── routes/
│   ├── (no-sidebar)/join, setup     joining a realm, the founder's setup wizard
│   ├── (sidebar)/                   dashboard, proposals, delegations, organization, admin, …
│   │   └── extensions/[id]          the page that hosts any extension
│   └── welcome/
└── lib/
    ├── realm.ts                     read / run / quarter
    ├── canisters.js                 actors, identity, active quarter
    ├── extension-loader.ts          mounts extension UIs
    ├── extension-bridge-host.ts     host side of the iframe bridge
    ├── extension-privileged.ts      the in-process allow-list
    └── realm-extension-sdk.ts       the context given to in-process extensions
```

## Hosting extension UIs

`ExtensionRuntimeHost.svelte` reads the extension's installed manifest (from `extension_list`) and
mounts it one of two ways:

| `runtime` in the manifest | How it runs | Loaded from |
|---|---|---|
| `"sandboxed"` | `<iframe sandbox="allow-scripts allow-modals">`, opaque origin, talks over the bridge | `/ext/<id>/<version>/frontend/dist/index.html` |
| anything else | ES module in the page, `export default function mount(target, ctx)` | `/ext/<id>/<version>/frontend/dist/index.js` |

In-process mounting is only for extensions on the `VITE_PRIVILEGED_EXTENSIONS` allow-list (a
comma-separated build variable). When it is unset, all are allowed. Third-party extensions should
always be sandboxed.

The bundles are served by the realm's own asset canister, at the same origin as the UI.

> **Known gap.** The released backend copies an extension's `frontend/dist` from the file registry
> into the asset canister when it installs the extension (`realms local start` grants it `Commit`
> on the asset canister for this). The slim backend's `extension_install` does not copy files yet,
> so a slim realm needs the bundle uploaded to `/ext/<id>/<version>/frontend/dist/` separately.

## The extension bridge

Sandboxed extensions use [`@realmsgos/extension-bridge`](../../packages/extension-bridge), a
`postMessage` protocol. The host side (`createBridgeServer`) enforces the rules; the extension side
is a small client:

```ts
import { createExtensionClient } from '@realmsgos/extension-bridge';

const realm = await createExtensionClient();          // handshake with the host
const list = await realm.read('proposal_list', { status: 'voting' });
await realm.run('proposal_vote', { proposal: 'P-7', choice: 'yes' });
realm.notify('success', 'Vote cast');
realm.reportHeight(document.body.scrollHeight);
```

What the host enforces:

- Nothing works before the handshake.
- `read` / `run` reach only the operations in the manifest's `operations`, and run **as the user**
  (so Cedar checks the user's permissions too).
- `callExtension(fn, args)` (the extension's own backend) needs the `call_extension` capability and
  `fn` listed in `entry_access.functions`. Without that list, every function is refused.
- Limits: 30 calls, 10 notifications, 10 navigations, 30 resizes and 5 modals per 10 seconds; at
  most 10 calls in flight; messages up to 256 KB.
- The iframe learns the user's principal (it is public), never their signing identity: every call
  is signed by the host.

The full client API: `read`, `run`, `callExtension`, `callExtensionAsync`, `navigate`, `notify`,
`openModal`, `onStateChange` (principal, locale, theme and realm info; the host's CSS variables are applied for you),
`reportHeight` and `destroy`. See [Extension frontends](../extensions/frontend.md).

## Commands

```bash
cd src/realm_frontend
npm install
npm run dev            # vite dev server
npm run check          # svelte-check
npm run test:unit      # vitest
npm run test:e2e       # playwright
npm run build
```
