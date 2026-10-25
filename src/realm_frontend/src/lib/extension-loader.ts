/**
 * Runtime extension frontend loader.
 *
 * Loads an extension's compiled JS bundle from the realm's own frontend
 * asset canister at /ext/{id}/{version}/frontend/dist/index.js and mounts
 * it into a DOM target, without rebuilding realm_frontend.
 *
 * Extension bundles are copied to the realm frontend at install time
 * (install_extension_from_registry). There is no runtime fallback to
 * file_registry — if the same-origin bundle is missing, load fails.
 *
 * Extension bundle contract:
 *   The file at `frontend/dist/index.js` MUST be a valid ES module that
 *   exports a default `mount(target: HTMLElement, props: object)` function
 *   returning an optional `unmount()` function.
 */

import type {
  RealmExtensionContext,
  MountResult,
  ExtensionMountFn,
} from './realm-extension-sdk';
import { SandboxBridgeService, type SandboxBridgeDeps } from './extension-bridge-host';

export type { MountResult, ExtensionMountFn as MountFn };

export interface SandboxMountResult {
  unmount: () => void;
  /** Resolves when the iframe bridge handshake completes. */
  ready: Promise<void>;
}

export type SandboxExtensionDeps = Omit<SandboxBridgeDeps, 'extensionId'> & {
  manifest: SandboxBridgeDeps['manifest'];
};

export interface ExtensionManifest {
  id?: string;
  name: string;
  version?: string;
  description?: string;
  [key: string]: unknown;
}

export {
  extensionHref,
  resolveMemberHomeHref,
  type SidebarHomeInput,
  type SidebarManifestRow,
} from './extension-home';

export function fileRegistryBaseUrlFor(canisterId: string): string {
  const host = typeof window !== 'undefined' ? window.location.host : '';
  const isLocal = host.includes('localhost') || host.includes('127.0.0.1');

  if (isLocal) {
    const port = host.split(':')[1] ?? '4943';
    return `http://${canisterId}.localhost:${port}`;
  }
  return `https://${canisterId}.icp0.io`;
}

const HANDSHAKE_TIMEOUT_MS = 30_000;

async function resolveInstalledVersion(
  backend: { get_extension_frontend_info?: (args: string) => Promise<string> },
  extId: string,
  fallbackVersion: string,
): Promise<string> {
  // Manifest version (from list_runtime_extensions) matches the installed
  // backend and the same-origin bundle uploaded at runtime-install time.
  // _source.json can lag after direct runtime-install — prefer manifest.
  if (fallbackVersion) {
    return fallbackVersion;
  }
  if (typeof backend?.get_extension_frontend_info === 'function') {
    try {
      const raw = await backend.get_extension_frontend_info(
        JSON.stringify({ extension_id: extId }),
      );
      const parsed = JSON.parse(raw);
      if (parsed?.success && parsed.version) {
        return parsed.version;
      }
    } catch (e) {
      console.warn('[extension-loader] get_extension_frontend_info failed:', e);
    }
  }
  return fallbackVersion;
}

/**
 * Fetch and dynamically import an extension's compiled frontend bundle,
 * then call its default export to mount it into `target`.
 *
 * The bundle is loaded from the realm's own frontend asset canister at
 * /ext/{id}/{version}/frontend/dist/index.js (same origin, certified).
 */
export async function mountExtension(
  extId: string,
  version: string,
  target: HTMLElement,
  ctx: RealmExtensionContext,
): Promise<MountResult | void> {
  const backend: any = ctx?.backend;
  const ver = await resolveInstalledVersion(backend, extId, version);

  const sameOriginPath = `/ext/${extId}/${ver}/frontend/dist/index.js`;
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  const sameOriginUrl = `${origin}${sameOriginPath}`;

  const mod = await import(/* @vite-ignore */ sameOriginUrl);

  const mount: ExtensionMountFn | undefined = mod?.default ?? mod?.mount;
  if (typeof mount !== 'function') {
    throw new Error(
      `Extension '${extId}@${ver}' bundle does not export a default mount() function`,
    );
  }

  return await mount(target, ctx);
}

/**
 * Mount a sandboxed extension inside an iframe and bind the postMessage bridge.
 *
 * The iframe loads `/ext/{id}/{version}/frontend/dist/index.html` with
 * `sandbox="allow-scripts allow-modals"` (opaque origin). `allow-modals` is
 * required so the extension can show `confirm` / `prompt`; without it those
 * calls return immediately and the click does nothing. Returns `{ unmount, ready }` where
 * `ready` resolves after hello_ack or rejects on hello_nack (e.g. sdk_version mismatch).
 * On handshake failure the iframe is torn down before `ready` rejects.
 */
export async function mountSandboxedExtension(
  extId: string,
  version: string,
  container: HTMLElement,
  deps: SandboxExtensionDeps,
): Promise<SandboxMountResult> {
  const ver = version;

  const iframe = document.createElement('iframe');
  iframe.setAttribute('sandbox', 'allow-scripts allow-modals');
  iframe.setAttribute('referrerpolicy', 'no-referrer');
  iframe.setAttribute('title', `Extension ${extId}`);
  iframe.src = `/ext/${extId}/${ver}/frontend/dist/index.html`;
  iframe.style.width = '100%';
  iframe.style.border = 'none';
  iframe.style.display = 'block';
  iframe.style.minHeight = '200px';
  iframe.style.position = 'relative';
  iframe.style.zIndex = '1';
  iframe.style.pointerEvents = 'auto';

  container.innerHTML = '';
  container.appendChild(iframe);

  let bridge: SandboxBridgeService | null = null;
  let tornDown = false;
  let initialHandshakeDone = false;
  let handshakeTimer: ReturnType<typeof setTimeout> | undefined;
  let iframeLoadCount = 0;

  let resolveReady!: () => void;
  let rejectReady!: (err: Error) => void;
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });

  function clearHandshakeTimer(): void {
    if (handshakeTimer !== undefined) {
      clearTimeout(handshakeTimer);
      handshakeTimer = undefined;
    }
  }

  function teardown(reason?: string): void {
    if (tornDown) return;
    tornDown = true;
    clearHandshakeTimer();
    iframe.removeEventListener('load', onIframeLoad);
    bridge?.destroy();
    bridge = null;
    iframe.remove();
    container.innerHTML = '';
    if (reason) {
      console.warn('[extension-loader] sandbox teardown:', reason);
    }
  }

  function handleHandshakeFailure(reason: string, isRehandshake: boolean): void {
    clearHandshakeTimer();
    teardown(reason);
    deps.onHandshakeFailed?.(reason);
    if (!isRehandshake) {
      rejectReady(new Error(reason));
    }
  }

  function startHandshakeTimer(isRehandshake: boolean): void {
    clearHandshakeTimer();
    handshakeTimer = setTimeout(() => {
      handleHandshakeFailure('Handshake timeout', isRehandshake);
    }, HANDSHAKE_TIMEOUT_MS);
  }

  function bindBridge(isRehandshake: boolean): SandboxBridgeService {
    startHandshakeTimer(isRehandshake);
    return new SandboxBridgeService(iframe, {
      extensionId: extId,
      manifest: deps.manifest,
      callSync: deps.callSync,
      callAsync: deps.callAsync,
      navigate: deps.navigate,
      getHostState: deps.getHostState,
      subscribeHostState: deps.subscribeHostState,
      onHandshakeComplete: () => {
        clearHandshakeTimer();
        if (!initialHandshakeDone) {
          initialHandshakeDone = true;
          deps.onHandshakeComplete?.();
          resolveReady();
        } else {
          deps.onHandshakeComplete?.();
        }
      },
      onHandshakeFailed: (reason) => {
        handleHandshakeFailure(reason, isRehandshake);
      },
    });
  }

  function onIframeLoad(): void {
    iframeLoadCount += 1;
    // First load is the expected extension bundle; bridge is already bound.
    if (iframeLoadCount === 1) return;

    // Subsequent load = document navigated — treat as new untrusted context.
    bridge?.destroy();
    bridge = null;
    if (tornDown) return;
    bridge = bindBridge(true);
  }

  iframe.addEventListener('load', onIframeLoad);
  bridge = bindBridge(false);

  return {
    unmount: () => {
      teardown();
    },
    ready,
  };
}
