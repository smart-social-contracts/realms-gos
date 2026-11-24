# @realmsgos/extension-bridge

Isomorphic TypeScript bridge for sandboxed Realm extensions. Both the SvelteKit
host and the extension iframe bundle import this package to exchange messages
over `postMessage`.

## Install

```bash
npm install @realmsgos/extension-bridge
```

In the Realms monorepo the host frontend depends on the local package via
`file:../../packages/extension-bridge`.

## Extension side (inside the sandbox iframe)

```ts
import { createExtensionClient } from '@realmsgos/extension-bridge';

const ctx = await createExtensionClient();

console.log(ctx.extensionId, ctx.capabilities);

ctx.onStateChange((state) => {
  document.documentElement.dataset.theme = state.theme;
});

// The screen reaches the realm only through its own backend, which the realm runs in a sandbox as the member and
// lets call only the operations the extension declares.
const treasury = await ctx.read('treasury');        // a query of the backend (extension_read): free, reads only
await ctx.run('pay', { to: 'bob', amount: 10 });    // an update (extension_call): may change the realm and wait
await ctx.callExtensionAsync('check_invoice_payment', { invoice_id: '…' });
ctx.navigate('/extensions/other');
ctx.notify('success', 'Saved');

const { actionId } = await ctx.openModal({
  title: 'Confirm',
  body: 'Delete this item?',
  actions: [
    { id: 'cancel', label: 'Cancel', tone: 'secondary' },
    { id: 'delete', label: 'Delete', tone: 'danger' },
  ],
});

ctx.reportHeight(document.body.scrollHeight);

// Capability `vault`: records sealed with the realm's vetKeys; capability `download`: a file the backend allows.
const sealed = await ctx.vault.seal([treasurer, deputy], { iban: 'CH93 0076 2011 6238 5295 7' });
const register = await ctx.vault.open(myWrappedKey, sealed.ciphertext);
await ctx.saveFile('order.xml', xml, { mimeType: 'application/xml' });
```

## Host side

```ts
import { createBridgeServer, type HostState } from '@realmsgos/extension-bridge';

const iframe = document.querySelector('iframe')!;

const server = createBridgeServer(iframe, {
  extensionId: 'hello_sandboxed',
  requiredSdkVersion: '1',
  capabilities: ['navigate', 'notify', 'modal'],
  getState: (): HostState => ({
    principal: 'aaaaa-aa',
    locale: 'en',
    theme: 'light',
    realmInfo: {
      name: 'My Realm',
      welcomeMessage: 'Welcome',
      manifesto: '',
      isQuarter: false,
      parentRealmCanisterId: '',
    },
  }),
  onCallExtension: async (fn, args, read) => {
    /* the extension's backend: extension_read when ``read``, else extension_call */
    return { ok: true };
  },
  onCallExtensionAsync: async (fn, args) => {
    /* submit async RPC; return taskId, then pushTaskResult when done */
    const taskId = '…';
    void backendCall(fn, args).then(
      (result) => server.pushTaskResult(taskId, { status: 'completed', result }),
      (e) => server.pushTaskResult(taskId, { status: 'failed', error: String(e) }),
    );
    return { taskId };
  },
  onNavigate: (path) => {
    /* SvelteKit goto */
  },
  onNotify: (level, message) => {
    /* host modal via showBridgeNotice / showBridgeAlert */
  },
  onOpenModal: async ({ title, body, actions }) => {
    /* render modal, return chosen action */
    return { actionId: 'cancel' };
  },
  onResize: (height) => {
    iframe.style.height = `${height}px`;
  },
});

// Push state when stores change
server.pushState(nextState);

// Cleanup
server.destroy();
```

See [`docs/extensions/frontend.md`](../../docs/extensions/frontend.md) and [`docs/architecture/frontend.md`](../../docs/architecture/frontend.md) for the bridge and its limits.

## The backend is the only way to the realm

A screen has no call of a realm operation. `call_extension` (and `call_extension_async`) run a function of the
extension's own backend: `read: true` as a query (`extension_read`, which reaches only read operations and cannot
wait), otherwise as an update (`extension_call`). The realm runs the backend as the member, in a sandbox that lets it
call only the operations the extension's installed manifest declares, and logs each call it makes as the extension's.
So what a screen may do is decided by the realm, not by the bridge, which only paces the calls (30 every 10 seconds,
10 at once).

## Capabilities

The host's own services are gated by manifest `capabilities` (closed-world: undeclared capabilities are denied):

| Capability | Operations |
|------------|------------|
| `navigate` | `navigate` |
| `notify` | `notify` |
| `modal` | `open_modal` |
| `vault` | `vault_seal`, `vault_open`: the host's vetKeys seal a record for principals, or open one sealed for the member; the member's key never enters the frame |
| `download` | `save_file`: the host saves a file once the extension's backend allows it (`download_allowed(name, sha256, size)`, as a query) |

`call_extension`, `resize` and `get_state` are not capability-gated.

## Message validation

Inbound extension→host messages are validated per kind before dispatch. Malformed
messages with a response channel (`call_extension`, `open_modal`, `get_state`)
receive `{ code: 'bad_request', message }`; fire-and-forget ops and `hello` are
silently dropped. Validators are exported from this package for host reuse:

```ts
import { validateExtToHostMessage } from '@realmsgos/extension-bridge';
```

## Inbound message size

Serialized inbound messages larger than **256 KiB** (`JSON.stringify` length) are
ignored.

## Rate limiting

Operations exceeding per-bridge sliding-window limits are handled as follows:

| Operation | Limit | On exceed |
|-----------|-------|-----------|
| `notify` | 10 per 10s | silently dropped |
| `navigate` | 10 per 10s | silently dropped |
| `resize` | 30 per 10s | silently dropped |
| `call_extension` | 30 per 10s | `{ code: 'rate_limited' }`; shared with `call_extension_async` |
| `call_extension_async` | (shared) | shares `call_extension` window and 10 concurrent cap |
| `open_modal` | 5 per 10s | `{ code: 'rate_limited' }` |
| `get_state` | 30 per 10s | silently dropped |

Additionally, at most **10** concurrent in-flight `call_extension` and
`call_extension_async` submit requests are allowed per bridge; excess requests
receive `{ code: 'rate_limited' }`.

## Async extension calls (`callExtensionAsync`)

Long-running backend functions (async generators / inter-canister yields) use a
two-phase flow over the bridge:

1. Extension sends `call_extension_async { fn, args }`.
2. Host replies immediately with `call_result { taskId }`.
3. Host runs the backend async call (e.g. `extension_async_call`) and pushes
   `task_result { taskId, status, result?, error? }` when it settles.

The extension client registers a one-shot listener for the matching `taskId`.
Default timeout waiting for `task_result`: **60 s** (override via
`callExtensionAsync(fn, args, { timeoutMs })`). Sync `callExtension` keeps the
**30 s** handshake-aligned request timeout via the pending request map.

The server is transport-only: polling/awaiting the backend belongs in the host
(`onCallExtensionAsync` + `pushTaskResult`).

## Client source verification

The extension client accepts host messages only from `window.parent`, before and
after handshake. This prevents a bundled client from accepting spoofed bridge
traffic when loaded outside its intended embedding host.
