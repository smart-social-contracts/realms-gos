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

await ctx.callExtension('greet', { name: 'Ada' });
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
```

## Host side

```ts
import { createBridgeServer, type HostState } from '@realmsgos/extension-bridge';

const iframe = document.querySelector('iframe')!;

const server = createBridgeServer(iframe, {
  extensionId: 'hello_sandboxed',
  requiredSdkVersion: '1',
  capabilities: ['call_extension', 'navigate', 'notify', 'modal'],
  entryAccessFunctions: { greet: 'member' },
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
  onCallExtension: async (fn, args) => {
    /* host sync RPC (extension_sync_call) */
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

## Capabilities

Bridge operations are gated by manifest `capabilities` (closed-world: undeclared
capabilities are denied):

| Capability | Operations |
|------------|------------|
| `call_extension` | `call_extension` |
| `call_extension` | `call_extension_async` (same capability + allowlist) |
| `navigate` | `navigate` |
| `notify` | `notify` |
| `modal` | `open_modal` |

`resize` and `get_state` are not capability-gated.

## `entry_access.functions` (fail-closed)

When the host grants `call_extension`, every `call_extension` request is checked
against `entryAccessFunctions` (the manifest `entry_access.functions` map). If
that allowlist is **absent**, **all** function calls are denied (fail-closed) and
the bridge logs a console warning — the manifest must declare
`entry_access.functions` explicitly. This prevents accidental open RPC when the
host omits the allowlist.

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
