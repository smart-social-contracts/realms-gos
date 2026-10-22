/**
 * Federation portal iframe bridge (realm frontend side).
 */
import { DelegationIdentity, DelegationChain, Ed25519KeyIdentity } from '@dfinity/identity';
import { portalLoginHref, markPortalSigningIn } from '$lib/portal-redirect-path.ts';

const BRIDGE_VERSION = '1';
const PORTAL_SESSION_KEY = 'realms:portal-embed';
let port: MessagePort | null = null;
let portalConfig: {
  slug?: string;
  backendCanisterId?: string;
  frontendCanisterId?: string;
  env?: string;
  path?: string;
  origin?: string;
} | null = null;
/** Last embedded path the portal host reported (`/extensions/…`, `/join`, …). */
let portalHostEmbeddedPath: string | null = null;
let pendingDelegationRequest = false;
let pendingNavPush: { path: string; replace: boolean } | null = null;
let pendingFocusPush: { source: string; uri: string; label?: string } | null | undefined = undefined;
let pendingAssistantOpen: boolean = false;
let pendingUiReady: boolean = false;
let pendingPortalLogin: { returnPath: string } | null = null;
let pendingPortalLogout = false;
let uiReadySent: boolean = false;
let sessionIdentity: Ed25519KeyIdentity | null = null;
let delegationIdentity: DelegationIdentity | null = null;
let delegationExpiresAt: number | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

/** Refresh 5 minutes before expiry to avoid edge-of-expiry failures. */
const REFRESH_BUFFER_MS = 5 * 60 * 1000;

function markPortalEmbedded() {
  if (typeof window === 'undefined') return;
  try {
    sessionStorage.setItem(PORTAL_SESSION_KEY, '1');
  } catch {
    // private mode / sandbox without storage
  }
}

function isPortalEmbedded() {
  if (typeof window === 'undefined') return false;
  if (new URLSearchParams(window.location.search).get('portal') === '1') {
    markPortalEmbedded();
    return true;
  }
  if (port) return true;
  try {
    return sessionStorage.getItem(PORTAL_SESSION_KEY) === '1';
  } catch {
    return false;
  }
}

function post(msg: Record<string, unknown>) {
  if (!port) return;
  port.postMessage(msg);
}

function ensureSessionIdentity() {
  if (!sessionIdentity) {
    sessionIdentity = Ed25519KeyIdentity.generate();
  }
  return sessionIdentity;
}

function onPortMessage(event: MessageEvent) {
  const msg = event.data;
  if (!msg || typeof msg.type !== 'string') return;

  switch (msg.type) {
    case 'config:realm':
      portalConfig = msg.payload || null;
      portalHostEmbeddedPath =
        typeof portalConfig?.path === 'string' && portalConfig.path
          ? portalConfig.path
          : null;
      window.dispatchEvent(new CustomEvent('portal:config', { detail: portalConfig }));
      break;
    case 'auth:delegation':
      applyDelegation(msg.payload).catch((e) => {
        console.error('[portal-bridge] delegation apply failed:', e);
      });
      break;
    case 'auth:logout':
      delegationIdentity = null;
      delegationExpiresAt = null;
      if (refreshTimer) {
        clearTimeout(refreshTimer);
        refreshTimer = null;
      }
      window.dispatchEvent(new CustomEvent('portal:logout'));
      break;
    case 'auth:pending':
      // Host has no session. A user who clicked Sign in has already been sent
      // to the portal login page; this reply is only for a silent probe.
      window.dispatchEvent(
        new CustomEvent('portal:auth-pending', { detail: { error: msg.error || '' } })
      );
      break;
    case 'auth:error':
      window.dispatchEvent(
        new CustomEvent('portal:auth-error', { detail: { error: msg.error || 'auth failed' } })
      );
      break;
    case 'nav:sync': {
      const path = msg.payload?.path || '/';
      if (typeof path === 'string' && path) {
        portalHostEmbeddedPath = path;
      }
      window.dispatchEvent(new CustomEvent('portal:nav-sync', { detail: { path } }));
      break;
    }
    default:
      break;
  }
}

async function applyDelegation(payload: { delegation?: unknown; backendCanisterId?: string; expiresAt?: number }) {
  if (!payload?.delegation) return;
  const json =
    typeof payload.delegation === 'string'
      ? payload.delegation
      : JSON.stringify(payload.delegation);
  const session = ensureSessionIdentity();
  const chain = DelegationChain.fromJSON(json);
  const next = DelegationIdentity.fromDelegation(session, chain);
  // Silent auth probes re-deliver a freshly signed delegation for the SAME
  // principal every few seconds. Absorb it (fresher expiry) but don't
  // re-dispatch portal:auth — each dispatch makes listeners reset and
  // re-restore session state, visible as a periodic UI flicker on /join.
  const unchanged =
    !!delegationIdentity &&
    delegationIdentity.getPrincipal().toText() === next.getPrincipal().toText();
  delegationIdentity = next;
  delegationExpiresAt = payload.expiresAt || null;
  scheduleRefresh();
  if (unchanged) return;
  window.dispatchEvent(
    new CustomEvent('portal:auth', {
      detail: {
        identity: delegationIdentity,
        backendCanisterId: payload.backendCanisterId,
        expiresAt: payload.expiresAt
      }
    })
  );
}

function scheduleRefresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
  if (!delegationExpiresAt) return;
  const now = Date.now();
  const refreshAt = delegationExpiresAt - REFRESH_BUFFER_MS;
  const delay = refreshAt - now;
  if (delay <= 0) {
    // Already within buffer — refresh immediately
    requestSilentAuthProbe();
    return;
  }
  refreshTimer = setTimeout(() => {
    console.log('[portal-bridge] delegation expiring soon, requesting refresh');
    requestSilentAuthProbe();
  }, delay);
}

function clearPortalDelegation() {
  delegationIdentity = null;
  delegationExpiresAt = null;
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

/**
 * Ask the portal to drop its Internet Identity session, then clear the
 * delegation held in this iframe. Resolves when the portal confirms, or
 * after a short wait if the bridge never answers.
 */
export function requestPortalLogout(): Promise<void> {
  clearPortalDelegation();
  return new Promise((resolve) => {
    if (typeof window === 'undefined') {
      resolve();
      return;
    }
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener('portal:logout', done);
      resolve();
    };
    const timer = setTimeout(done, 8000);
    window.addEventListener('portal:logout', done);
    if (!isEmbeddedInPortal()) {
      done();
      return;
    }
    if (!port) {
      pendingPortalLogout = true;
      return;
    }
    post({ type: 'auth:logout' });
  });
}

function requestDelegation(interactive = false) {
  if (!port) {
    pendingDelegationRequest = true;
    return;
  }
  pendingDelegationRequest = false;
  const session = ensureSessionIdentity();
  post({
    type: 'auth:request-delegation',
    payload: {
      sessionPublicKeyDer: Array.from(new Uint8Array(session.getPublicKey().toDer())),
      // Silent probes (bridge init) must not make the host pop its sign-in
      // overlay — only user-initiated logins should.
      interactive
    }
  });
}

export function getPortalDelegationIdentity() {
  return delegationIdentity;
}

export function getPortalConfig() {
  return portalConfig;
}

export function getPortalHostEmbeddedPath(): string | null {
  return portalHostEmbeddedPath;
}

export function isEmbeddedInPortal() {
  return isPortalEmbedded();
}

export function initPortalBridge() {
  if (!isPortalEmbedded()) return () => {};
  if (typeof window === 'undefined') return () => {};

  markPortalEmbedded();

  const onWindowMessage = (event: MessageEvent) => {
    if (event.data?.type !== 'bridge:init' || !event.ports?.[0]) return;
    port?.close?.();
    port = event.ports[0];
    if (port) {
      port.onmessage = onPortMessage;
    }
    post({ type: 'bridge:ready', payload: { version: BRIDGE_VERSION } });
    requestDelegation(false); // silent probe: reuse an existing host session if any
    // Flush any navigation that fired before the MessagePort was ready
    // (common: afterNavigate during hydration races the handshake).
    if (pendingNavPush) {
      const queued = pendingNavPush;
      pendingNavPush = null;
      post({
        type: 'nav:push',
        payload: { path: queued.path, replace: queued.replace }
      });
    }
    if (pendingFocusPush !== undefined) {
      const queuedFocus = pendingFocusPush;
      pendingFocusPush = undefined;
      post({ type: 'focus:push', payload: queuedFocus });
    }
    if (pendingAssistantOpen) {
      pendingAssistantOpen = false;
      post({ type: 'assistant:open' });
    }
    if (pendingUiReady && !uiReadySent) {
      uiReadySent = true;
      pendingUiReady = false;
      post({ type: 'ui:ready' });
    }
    if (pendingPortalLogin) {
      const queued = pendingPortalLogin;
      pendingPortalLogin = null;
      post({ type: 'auth:open-login', payload: queued });
    }
    if (pendingPortalLogout) {
      pendingPortalLogout = false;
      post({ type: 'auth:logout' });
    }
  };

  window.addEventListener('message', onWindowMessage);

  // The host sends bridge:init on our document's load event, which fires long
  // before this listener exists (we attach during late hydration) — that first
  // message and its transferred port are lost. Announce ourselves so the host
  // re-handshakes with a fresh channel; retry until a port arrives.
  const sayHello = () => {
    try {
      window.parent?.postMessage({ type: 'bridge:hello', version: BRIDGE_VERSION }, '*');
    } catch {
      // parent may be inaccessible outside a real embed
    }
  };
  let helloAttempts = 0;
  const helloTimer = setInterval(() => {
    if (port || ++helloAttempts > 40) {
      clearInterval(helloTimer);
      return;
    }
    sayHello();
  }, 250);
  sayHello();

  return () => {
    clearInterval(helloTimer);
    if (refreshTimer) {
      clearTimeout(refreshTimer);
      refreshTimer = null;
    }
    window.removeEventListener('message', onWindowMessage);
    port = null;
  };
}

/**
 * Mirror an in-realm path onto the portal address bar (`/r/<slug><path>`).
 * @param {string} path
 * @param {{ replace?: boolean }} [opts]  replace=true uses history.replaceState
 *   (auth redirects / initial sync); default pushState for real navigations.
 */
export function portalNavPush(path: string, { replace = false }: { replace?: boolean } = {}) {
  if (!isPortalEmbedded()) return false;
  const normalized = path.startsWith('/') ? path : `/${path || ''}`;
  if (!port) {
    // Handshake not ready yet — remember the latest path and flush on connect.
    pendingNavPush = { path: normalized, replace: !!replace };
    return false;
  }
  pendingNavPush = null;
  post({ type: 'nav:push', payload: { path: normalized, replace: !!replace } });
  return true;
}

/**
 * Mirror document focus onto the portal host so RegistryAssistant can include it.
 * @param {{ source: string, uri: string, label?: string } | null} focus
 */
export function portalFocusPush(focus: { source: string; uri: string; label?: string } | null) {
  if (!isPortalEmbedded()) return false;
  if (!port) {
    pendingFocusPush = focus;
    return false;
  }
  pendingFocusPush = undefined;
  post({ type: 'focus:push', payload: focus });
  return true;
}

/**
 * Ask the portal host to open the mundus-level RegistryAssistant.
 * Fire-and-forget; queues briefly if the MessagePort is not ready yet.
 */
export function portalAssistantOpen() {
  if (!isPortalEmbedded()) return false;
  if (!port) {
    pendingAssistantOpen = true;
    return false;
  }
  pendingAssistantOpen = false;
  post({ type: 'assistant:open' });
  return true;
}

/**
 * Tell the portal host the realm has real content on screen (setup gate
 * resolved) so it can drop its own loading overlay. Signalling at mount
 * instead would hand off while the splash/skeleton is still showing.
 * Fire-and-forget; queues briefly if the MessagePort is not ready yet.
 * Fires exactly once per page load.
 */
export function portalUiReady() {
  if (!isEmbeddedInPortal()) return false;
  if (uiReadySent) return true;
  if (!port) {
    pendingUiReady = true;
    return false;
  }
  uiReadySent = true;
  post({ type: 'ui:ready' });
  return true;
}

export function reportResize(height: number) {
  if (!port) return;
  post({ type: 'resize:report', payload: { height } });
}

export function requestAuthRefresh() {
  // User-initiated session refresh. Does not open a sign-in card; a click
  // that needs Internet Identity uses redirectToPortalLogin.
  requestDelegation(true);
}

/**
 * Leave the realm iframe for the portal login page.
 * Assigning `location.href` is allowed across origins. Calling `.assign`
 * throws SecurityError. The portal message is a second path to the same page.
 * Returns true when a navigation was started or queued.
 */
export function redirectToPortalLogin(returnPath = '/join'): boolean {
  if (typeof window === 'undefined' || !isEmbeddedInPortal()) return false;
  const safePath = returnPath.startsWith('/') && !returnPath.startsWith('//') ? returnPath : '/join';
  markPortalSigningIn();
  const origin = (portalConfig?.origin || '').replace(/\/+$/, '');
  const slug = (
    portalConfig?.slug || new URLSearchParams(window.location.search).get('slug') || ''
  ).trim();
  if (origin && slug && window.top && window.top !== window) {
    const href = portalLoginHref({ origin, slug, returnPath: safePath });
    try {
      window.top.location.href = href;
    } catch {
      // The portal message below still moves the top window.
    }
  }
  const payload = { returnPath: safePath };
  if (!port) {
    pendingPortalLogin = payload;
    return true;
  }
  post({ type: 'auth:open-login', payload });
  return true;
}

export function requestSilentAuthProbe() {
  // Periodic non-interactive re-probe while waiting on a host session (e.g.
  // the setup gate): never triggers the host's sign-in overlay. Covers the
  // case where the host's session became available only after the initial
  // probe was answered with auth:pending.
  requestDelegation(false);
}

/**
 * Wait for the portal host to deliver a scoped II delegation.
 *
 * `portal:auth-pending` (host has no session) does NOT settle the promise.
 * `interactive: false` only probes for a session the portal already has.
 * @param {{ timeoutMs?: number, interactive?: boolean }} [opts]
 * @returns {Promise<DelegationIdentity | null>}
 */
export function waitForPortalDelegation({
  timeoutMs = 300_000,
  interactive = true
}: { timeoutMs?: number; interactive?: boolean } = {}) {
  const existing = getPortalDelegationIdentity();
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: DelegationIdentity | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener('portal:auth', onAuth);
      window.removeEventListener('portal:auth-error', onAuthError);
      resolve(value);
    };

    const onAuth = () => {
      const identity = getPortalDelegationIdentity();
      if (identity) finish(identity);
    };

    const onAuthError = (event: Event) => {
      const customEvent = event as CustomEvent;
      console.warn('[portal-bridge] host auth error:', customEvent?.detail?.error);
      finish(null);
    };

    const timer = setTimeout(() => finish(null), timeoutMs);
    window.addEventListener('portal:auth', onAuth);
    window.addEventListener('portal:auth-error', onAuthError);
    if (interactive) requestAuthRefresh();
    else requestSilentAuthProbe();
  });
}
