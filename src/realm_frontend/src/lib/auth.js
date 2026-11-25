// src/lib/auth.js
import { AuthClient } from '@icp-sdk/auth/client';
import { Principal } from '@icp-sdk/core/principal';
import { getTestModeIIBypass } from '$lib/config.js';
import { isEmbeddedInPortal, getPortalDelegationIdentity } from '$lib/portal-bridge.ts';
import { normalizePortalRedirectPath } from '$lib/portal-redirect-path.ts';
import {
  resolveEffectiveAuthChannel,
  shouldRestoreTestModeSession,
  shouldLoginWithTestIdentity,
  resolveTestIdentityIndex,
} from '$lib/auth-precedence.ts';

const II_URL = globalThis.__CANISTER_IDS?.internet_identity || 'https://identity.ic0.app';
console.log(`Using Identity Provider: ${II_URL}`);

// Canonical Internet Identity derivation origin. II derives a *different*
// principal per frontend origin unless every frontend logs in with the same
// `derivationOrigin` (and that origin serves the realm frontend's origin in its
// `/.well-known/ii-alternative-origins`). Injected at deploy time via
// canister_ids.js so the whole federation (every realm + the registry) resolves
// to ONE principal per human. When unset, login falls back to per-origin
// principals (legacy behaviour). See issue #233 / docs/project/quarters-design.md.
const DERIVATION_ORIGIN = globalThis.__CANISTER_IDS?.derivation_origin || '';
if (DERIVATION_ORIGIN) {
  console.log(`Using II derivationOrigin: ${DERIVATION_ORIGIN}`);
}

/** How long an Internet Identity sign-in lasts before the visitor signs in again. */
const SESSION_TTL_NS = BigInt(7 * 24 * 60 * 60 * 1_000_000_000); // 7 days

// Internet Identity's backend canister has this id on mainnet and on icp-cli's
// local network (`ii: true`) alike.
const II_BACKEND_CANISTER_ID = 'rdmx6-jaaaa-aaaaa-aaadq-cai';

/**
 * Where @icp-sdk/auth signs in. Every mainnet II domain is served by the
 * library's default (id.ai); a local II renders its sign-in at `/authorize` and
 * mints through the local replica, whose root key must be fetched.
 */
function identityProviderOptions(iiUrl) {
  const host = new URL(iiUrl).hostname;
  if (!host.endsWith('localhost') && host !== '127.0.0.1') return {};
  return {
    identityProvider: {
      authorizeUrl: new URL('/authorize', iiUrl).href,
      canisterId: II_BACKEND_CANISTER_ID
    },
    agentOptions: { host: window.location.origin, shouldFetchRootKey: true }
  };
}

// Canonical portal page for this realm (e.g. https://staging.gos.earth/r/<slug>).
// Injected at deploy time via canister_ids.js. When a visitor lands on the raw
// icp0.io origin and tries to sign in, we redirect them here: the realm origin
// is not in the registry's /.well-known/ii-alternative-origins (II caps that
// list at ~10 entries; wizard realms are unbounded), so a direct II login would
// fail with "Unverified origin". On the portal the login is handled once, on
// the canonical origin, and bridged into the embedded realm.
const PORTAL_URL = (globalThis.__CANISTER_IDS?.portal_url || '').replace(/\/+$/, '');

/** Portal URL for the current in-app path, or '' when no redirect should happen. */
export function getPortalRedirectUrl() {
  if (!PORTAL_URL || typeof window === 'undefined') return '';
  // Inside the portal iframe the bridge handles auth — nothing to redirect.
  if (isEmbeddedInPortal()) return '';
  // Never redirect from inside any iframe: window.location targets the iframe
  // document, which would load the portal origin inside the frame (blocked by
  // frame-ancestors 'none') instead of navigating the top-level browser tab.
  if (window.self !== window.top) return '';
  // Skip for local dev, where the portal doesn't exist.
  const host = window.location.hostname;
  if (host.includes('localhost') || host.includes('127.0.0.1')) return '';
  // Already on the portal origin.
  if (window.location.href.startsWith(PORTAL_URL)) return '';
  // Escape hatch for tests/ops: ?standalone=1 pins this browser session to the
  // raw canister origin (sticky via sessionStorage so SPA navigation keeps it).
  const params = new URLSearchParams(window.location.search);
  if (params.get('standalone') === '1') {
    try {
      sessionStorage.setItem('realms:standalone', '1');
    } catch {
      // storage unavailable — the query param alone still skips this load
    }
    return '';
  }
  try {
    if (sessionStorage.getItem('realms:standalone') === '1') return '';
  } catch {
    // ignore
  }
  // The portal's /r/[slug]/[...path] route mirrors user-facing in-realm paths.
  const path = normalizePortalRedirectPath(window.location.pathname);
  const normalizedPath = path === '/' ? '' : path;
  return `${PORTAL_URL}${normalizedPath}${window.location.search}${window.location.hash}`;
}

let authClient;
let authClientMode = null;

const AUTH_CHANNEL_KEY = 'realms:auth_channel';

function persistAuthChannel(channel) {
  if (!channel) return;
  try {
    sessionStorage.setItem(AUTH_CHANNEL_KEY, channel);
  } catch {
    // private mode / sandbox
  }
}

function readPersistedAuthChannel() {
  try {
    const raw = sessionStorage.getItem(AUTH_CHANNEL_KEY);
    if (raw === 'portal' || raw === 'ii') return raw;
    if (raw === 'test' && __REALMS_TEST_BUILD__) return raw;
    return null;
  } catch {
    return null;
  }
}

function clearPersistedAuthChannel() {
  try {
    sessionStorage.removeItem(AUTH_CHANNEL_KEY);
  } catch {
    // ignore
  }
}

function getAuthMode() {
  const channel = resolveEffectiveAuthChannel(
    isEmbeddedInPortal(),
    getTestModeIIBypass(),
    readPersistedAuthChannel()
  );
  if (channel === 'portal') return 'portal';
  if (channel === 'test') return 'test';
  return 'ii';
}

function _createPortalAuthClientMock() {
  return {
    isAuthenticated: async () => !!getPortalDelegationIdentity(),
    getIdentity: () => getPortalDelegationIdentity(),
    logout: async () => {},
    login: async () => {
      const { getPortalDelegationIdentity, redirectToPortalLogin } = await import(
        '$lib/portal-bridge.ts'
      );
      const existing = getPortalDelegationIdentity();
      if (existing) return existing;
      const { portalLoginReturnPath } = await import('$lib/portal-redirect-path.ts');
      redirectToPortalLogin(
        portalLoginReturnPath(window.location.pathname, window.location.search)
      );
      return new Promise(() => {});
    }
  };
}

// Internet Identity, behind the same interface as the portal and test clients.
// Signed in means holding an identity: a stored record naming an account this
// tab cannot act for (expired, revoked at II, held only by another tab) is not.
function _createIIAuthClient() {
  const client = new AuthClient({
    ...identityProviderOptions(II_URL),
    // Pin the derivation origin so this realm frontend yields the SAME
    // principal as every other Realms frontend + the registry. Omitted when
    // unset to preserve legacy per-origin behaviour.
    ...(DERIVATION_ORIGIN ? { derivationOrigin: DERIVATION_ORIGIN } : {})
  });
  const heldIdentity = async () => {
    try {
      const identity = await client.getIdentity();
      return identity.getPrincipal().isAnonymous() ? null : identity;
    } catch (error) {
      console.warn('[auth] stored session is not usable:', error);
      return null;
    }
  };
  return {
    isAuthenticated: async () => (await heldIdentity()) !== null,
    getIdentity: heldIdentity,
    login: () => client.signIn({ maxTimeToLive: SESSION_TTL_NS }),
    // Ends the session at Internet Identity, not only in this browser.
    logout: () => client.signOut()
  };
}

// --- Test mode support ---
// In test mode we bypass Internet Identity entirely and use deterministic
// Ed25519 identities (see test-identities.js) so E2E and manual QA can pick
// Creator / Identity 0…N without WebAuthn.
let _testIdentity = null;
let _testLoggedIn = false;
let _testIdentityIndex = null;

const TEST_AUTH_INDEX_KEY = 'realms:test_auth_index';

function persistTestAuthIndex(index) {
  if (index == null || !Number.isFinite(index)) return;
  try {
    sessionStorage.setItem(TEST_AUTH_INDEX_KEY, String(index));
  } catch {
    // private mode / sandbox
  }
}

function readPersistedTestAuthIndex() {
  try {
    const raw = sessionStorage.getItem(TEST_AUTH_INDEX_KEY);
    if (raw == null || raw === '') return null;
    const index = Number(raw);
    return Number.isFinite(index) ? index : null;
  } catch {
    return null;
  }
}

function clearPersistedTestAuthIndex() {
  try {
    sessionStorage.removeItem(TEST_AUTH_INDEX_KEY);
  } catch {
    // ignore
  }
}

async function hydrateTestAuthStores(identity) {
  await initializeAuthClient();
  const { isAuthenticated: isAuthenticatedStore, userIdentity, principal } = await import(
    '$lib/stores/auth.js'
  );
  const principalText = identity.getPrincipal().toText();
  isAuthenticatedStore.set(true);
  userIdentity.set(principalText);
  principal.set(principalText);
  try {
    const { initBackendWithIdentity } = await import('$lib/canisters.js');
    await initBackendWithIdentity(identity);
    const { loadUserProfiles } = await import('$lib/stores/profiles.js');
    await loadUserProfiles();
  } catch (e) {
    console.warn('[TEST MODE] backend init deferred:', e);
  }
  return { authenticated: true, principal: principalText };
}

async function restoreTestAuthSession() {
  if (!__REALMS_TEST_BUILD__) return null;
  if (!shouldRestoreTestModeSession(getTestModeIIBypass(), readPersistedAuthChannel())) return null;

  if (!_testLoggedIn || !_testIdentity) {
    const persistedIndex = readPersistedTestAuthIndex();
    if (persistedIndex == null) return null;
    _testIdentity = await _createTestIdentity({ identityIndex: persistedIndex });
    _testLoggedIn = true;
    _testIdentityIndex = persistedIndex;
    authClient = _createTestAuthClientMock();
    authClientMode = 'test';
    persistAuthChannel('test');
    console.log(`[TEST MODE] Restored session from storage: index ${persistedIndex}`);
  }

  void publishTestIdentity();
  return hydrateTestAuthStores(_testIdentity);
}

async function _createTestIdentity({ random = false, identityIndex = null } = {}) {
  if (!__REALMS_TEST_BUILD__) {
    throw new Error('Test identities are not available in production builds');
  }
  const { createTestIdentityFromIndex, TEST_BUILD_AUTH_SENTINEL } = await import(
    '$lib/test-identities.js'
  );
  // Anchors the sentinel to the code path it certifies: it reaches the bundle
  // only if this function survives, which is what the variant check measures.
  globalThis.__REALMS_TEST_AUTH__ = TEST_BUILD_AUTH_SENTINEL;
  // createTestIdentityFromIndex is sync; dynamic import keeps the initial bundle small.
  if (identityIndex != null && Number.isFinite(identityIndex)) {
    const { normalizeTestIdentityIndex, testIdentityLabel } = await import('$lib/test-identities.js');
    const idx = normalizeTestIdentityIndex(identityIndex);
    _testIdentity = createTestIdentityFromIndex(idx);
    _testIdentityIndex = idx;
    console.log(
      `[TEST MODE] ${testIdentityLabel(idx)}: ${_testIdentity.getPrincipal().toText()}`,
    );
    return _testIdentity;
  }
  if (_testIdentity && !random) return _testIdentity;
  const { Ed25519KeyIdentity } = await import('@icp-sdk/core/identity');
  const seed = new Uint8Array(32);
  if (random) {
    crypto.getRandomValues(seed);
    _testIdentityIndex = null;
  } else {
    seed[0] = 0xED;
    seed[1] = 0x57;
    _testIdentityIndex = 0;
  }
  _testIdentity = Ed25519KeyIdentity.generate(seed);
  console.log(
    `[TEST MODE] Generated ${random ? 'random' : 'default'} test identity: ${_testIdentity.getPrincipal().toText()}`,
  );
  return _testIdentity;
}

// A minimal mock that satisfies the AuthClient interface used by canisters.js
function _createTestAuthClientMock() {
  return {
    isAuthenticated: async () => _testLoggedIn,
    getIdentity: () => _testIdentity,
    logout: async () => {
      _testLoggedIn = false;
      _testIdentity = null;
      _testIdentityIndex = null;
      void publishTestIdentity();
    },
  };
}

// Export the authClient instance for reuse
export { authClient };

export async function initializeAuthClient() {
  const mode = getAuthMode();
  if (authClient && authClientMode !== mode) {
    authClient = null;
  }
  authClientMode = mode;

  if (mode === 'portal') {
    if (!authClient) {
      authClient = _createPortalAuthClientMock();
    }
    return authClient;
  }
  if (mode === 'test') {
    if (!authClient) {
      authClient = _createTestAuthClientMock();
      console.log('[TEST MODE] Auth client initialized (mock)');
    }
    return authClient;
  }
  if (!authClient) {
    authClient = _createIIAuthClient();
    console.log('Auth client initialized');
  }
  return authClient;
}

export async function login({ random = false, identityIndex = null, preferTestMode = false } = {}) {
  const testBypass = getTestModeIIBypass();
  let resolvedIndex = identityIndex;
  if ((resolvedIndex == null || !Number.isFinite(Number(resolvedIndex))) && typeof window !== 'undefined') {
    const { parseTestIdentitySearch } = await import('$lib/test-identity-query.ts');
    const fromUrl = parseTestIdentitySearch(window.location.search).identityIndex;
    if (fromUrl != null) resolvedIndex = fromUrl;
  }
  const useTestAuth = shouldLoginWithTestIdentity({
    identityIndex: resolvedIndex,
    preferTestMode,
    testModeIIBypass: testBypass,
    embeddedInPortal: isEmbeddedInPortal(),
  });

  if (useTestAuth && __REALMS_TEST_BUILD__) {
    const urlParams = new URLSearchParams(window.location.search);
    const asParam = urlParams.get('as');
    const pemParam = urlParams.get('pem');

    let identity;
    if (asParam && pemParam) {
      const { Secp256k1KeyIdentity } = await import('@icp-sdk/core/identity/secp256k1');
      const decodedPem = decodeURIComponent(pemParam);
      identity = Secp256k1KeyIdentity.fromPem(decodedPem);
      _testIdentity = identity;
      _testIdentityIndex = null;
      console.log(`[TEST MODE] Logged in as ${asParam} with Secp256k1 PEM: ${identity.getPrincipal().toText()}`);
    } else {
      if (asParam) {
        console.warn(`[TEST MODE] ?as=${asParam} present but ?pem= missing — using test identity index`);
      }
      identity = await _createTestIdentity({
        random,
        identityIndex: resolveTestIdentityIndex(resolvedIndex, readPersistedTestAuthIndex(), random),
      });
    }
    _testLoggedIn = true;
    authClient = _createTestAuthClientMock();
    authClientMode = 'test';
    persistAuthChannel('test');
    if (_testIdentityIndex != null) {
      persistTestAuthIndex(_testIdentityIndex);
    }
    void publishTestIdentity();
    const principal = identity.getPrincipal();
    console.log(`[TEST MODE] Logged in with principal: ${principal.toText()}`);
    return { identity, principal };
  }

  // Federation portal embed: authenticate via the host's scoped delegation.
  // Never open Internet Identity from inside the iframe — the per-realm icp0.io
  // origin is not in the canonical /.well-known/ii-alternative-origins.
  if (isEmbeddedInPortal()) {
    let identity = getPortalDelegationIdentity();
    if (!identity) {
      const client = await initializeAuthClient();
      identity = await client.getIdentity();
    }
    if (!identity) {
      const { redirectToPortalLogin } = await import('$lib/portal-bridge.ts');
      const { portalLoginReturnPath } = await import('$lib/portal-redirect-path.ts');
      // Internet Identity opens on the portal origin. Leave this iframe for
      // the portal login page; it returns here with a session (issue #409).
      const started = redirectToPortalLogin(
        portalLoginReturnPath(window.location.pathname, window.location.search)
      );
      if (started) return new Promise(() => {});
      console.warn('[portal] No portal login URL — user must sign in on the portal origin');
      return { identity: null, principal: null };
    }
    if (identity) {
      authClient = _createPortalAuthClientMock();
      authClientMode = 'portal';
      persistAuthChannel('portal');
      const principal = identity.getPrincipal();
      console.log(`[portal] Authenticated via delegation: ${principal.toText()}`);
      return { identity, principal };
    }
    console.warn('[portal] No delegation from host — user must sign in on the portal origin');
    return { identity: null, principal: null };
  }

  const client = await initializeAuthClient();

  // Reuse an existing IC session — critical in portal iframes where auth stores
  // do not persist across remounts but AuthClient/IndexedDB still holds II.
  const existing = await client.getIdentity();
  if (existing) {
    const principal = existing.getPrincipal();
    console.log(`Reusing existing IC session: ${principal.toText()}`);
    return { identity: existing, principal };
  }

  // Direct visit to the raw canister origin: signing in here would fail
  // ("Unverified origin") or mint a different principal. Hand the user to the
  // portal, where one II login covers the whole federation and is bridged back
  // into this realm's iframe. Never resolves — we're navigating away.
  const portalRedirect = getPortalRedirectUrl();
  if (portalRedirect) {
    console.log(`[portal] Redirecting sign-in to the portal origin: ${portalRedirect}`);
    window.location.assign(portalRedirect);
    return new Promise(() => {});
  }

  try {
    const identity = await client.login();
    const principal = identity.getPrincipal();
    console.log(`Logged in with principal: ${principal.toText()}`);
    return { identity, principal };
  } catch (error) {
    console.error('Login failed:', error);
    return { identity: null, principal: null };
  }
}

export async function logout() {
  resetAuthSessionRestore();
  clearPersistedAuthChannel();
  if (__REALMS_TEST_BUILD__ && getTestModeIIBypass()) {
    _testLoggedIn = false;
    _testIdentity = null;
    _testIdentityIndex = null;
    clearPersistedTestAuthIndex();
    void publishTestIdentity();
    console.log('[TEST MODE] Logged out');
    return;
  }
  // The portal keeps the Internet Identity session. Clearing only this
  // iframe makes the next silent probe sign the visitor straight back in.
  if (isEmbeddedInPortal()) {
    const { requestPortalLogout } = await import('$lib/portal-bridge.ts');
    await requestPortalLogout();
    return;
  }
  const client = await initializeAuthClient();
  try {
    await client.logout();
  } catch (error) {
    // This browser is signed out either way; the session at Internet Identity
    // was not ended and lasts until it expires.
    console.error('[auth] Internet Identity did not end the session:', error);
  }
}

/** Tell the UI which test identity this tab is signed in as (0 is Identity 1), or null. Test builds only. */
async function publishTestIdentity() {
  const { testIdentityIndex } = await import('$lib/stores/auth.js');
  testIdentityIndex.set(__REALMS_TEST_BUILD__ && _testLoggedIn ? _testIdentityIndex : null);
}

// Invariant: signed in means this tab holds an identity (or `restoreTestAuthSession` is about to give it one), not that
// storage says so. A stored test session that this answers "yes" to must be one `restoreTestAuthSession` restores: both
// ask `shouldRestoreTestModeSession`, so they cannot disagree (realms-gos#737: they did, and every screen read the
// principal of an identity that was never made).
export async function isAuthenticated() {
  if (shouldRestoreTestModeSession(getTestModeIIBypass(), readPersistedAuthChannel())) {
    if (_testLoggedIn || readPersistedTestAuthIndex() != null) {
      return true;
    }
  }
  if (isEmbeddedInPortal()) {
    if (getPortalDelegationIdentity()) return true;
    const client = await initializeAuthClient();
    return client.isAuthenticated();
  }
  if (__REALMS_TEST_BUILD__ && getTestModeIIBypass()) {
    return _testLoggedIn;
  }
  const client = await initializeAuthClient();
  return client.isAuthenticated();
}

let restorePromise = null;
let settlePromise = null;

/** Reset so a later call re-checks IC session (e.g. after logout). */
export function resetAuthSessionRestore() {
  restorePromise = null;
  settlePromise = null;
}

/**
 * Restore the session and, inside the portal, wait for a delegation the portal already has
 * (without asking the visitor to sign in), so later calls are not made anonymously.
 */
export function settleAuthSession() {
  if (!settlePromise) {
    settlePromise = (async () => {
      await restoreAuthSession();
      if (!isEmbeddedInPortal() || getPortalDelegationIdentity()) return;
      const { waitForPortalDelegation } = await import('$lib/portal-bridge.ts');
      if (await waitForPortalDelegation({ timeoutMs: 15_000, interactive: false })) {
        restorePromise = null;
        await restoreAuthSession();
      }
    })().catch((error) => {
      settlePromise = null;
      throw error;
    });
  }
  return settlePromise;
}

/**
 * Restore auth from the IC AuthClient (shared across tabs) and hydrate app stores.
 * Safe to call from multiple components; runs once until reset.
 */
export async function restoreAuthSession() {
  if (!restorePromise) {
    restorePromise = _restoreAuthSession().catch((error) => {
      restorePromise = null;
      throw error;
    });
  }
  return restorePromise;
}

async function _restoreAuthSession() {
  const restorePortal = async () => {
    if (!isEmbeddedInPortal()) return null;
    const portalId = getPortalDelegationIdentity();
    if (!portalId) return null;
    persistAuthChannel('portal');
    // Swap the shared authClient to the portal mock so every later
    // isAuthenticated()/getIdentity() consumer sees the bridged session.
    await initializeAuthClient();
    const { isAuthenticated: isAuthenticatedStore, userIdentity, principal } = await import(
      '$lib/stores/auth.js'
    );
    const principalText = portalId.getPrincipal().toText();
    isAuthenticatedStore.set(true);
    userIdentity.set(principalText);
    principal.set(principalText);
    console.log(`[portal] Authenticated via delegation: ${principalText}`);
    try {
      const { initBackendWithIdentity } = await import('$lib/canisters.js');
      await initBackendWithIdentity(portalId);
      const { loadUserProfiles } = await import('$lib/stores/profiles.js');
      await loadUserProfiles();
    } catch (e) {
      console.warn('[portal] backend init deferred:', e);
    }
    return { authenticated: true, principal: principalText };
  };

  const viaPortal = await restorePortal();
  if (viaPortal) return viaPortal;

  const { backendReady } = await import('$lib/canisters.js');
  await backendReady;

  const viaTest = await restoreTestAuthSession();
  if (viaTest) return viaTest;

  const authenticated = await isAuthenticated();
  const { isAuthenticated: isAuthenticatedStore, userIdentity, principal } = await import(
    '$lib/stores/auth.js'
  );

  if (!authenticated) {
    // A portal delegation may have landed while we awaited the AuthClient;
    // never stomp a fresher portal session with "logged out".
    const latePortal = await restorePortal();
    if (latePortal) return latePortal;
    isAuthenticatedStore.set(false);
    return { authenticated: false, principal: '' };
  }

  const client = await initializeAuthClient();
  const identity = await client.getIdentity();
  const principalText = identity.getPrincipal().toText();

  isAuthenticatedStore.set(true);
  userIdentity.set(principalText);
  principal.set(principalText);

  const { initBackendWithIdentity } = await import('$lib/canisters.js');
  await initBackendWithIdentity();

  try {
    const { loadUserProfiles } = await import('$lib/stores/profiles.js');
    await loadUserProfiles();
  } catch (e) {
    console.warn('[auth] profile load deferred:', e);
  }

  return { authenticated: true, principal: principalText };
}

/** Recover the identity for an established session (pinned test, portal, or II). */
export async function getEstablishedIdentity() {
  const pinned = readPersistedAuthChannel();
  if (__REALMS_TEST_BUILD__ && pinned === 'test') {
    if (_testLoggedIn && _testIdentity) return _testIdentity;
    const persistedIndex = readPersistedTestAuthIndex();
    if (persistedIndex != null) {
      await restoreTestAuthSession();
      if (_testIdentity) return _testIdentity;
    }
  }
  if (pinned === 'portal' || isEmbeddedInPortal()) {
    const portalId = getPortalDelegationIdentity();
    if (portalId) return portalId;
  }
  const client = authClient || (await initializeAuthClient());
  if (await client.isAuthenticated()) {
    return client.getIdentity();
  }
  return null;
}

/** Clear auth stores when the UI claims a session but no identity is available. */
export async function resetAuthStoresIfDesynced() {
  const { isAuthenticated: isAuthenticatedStore, userIdentity, principal } = await import(
    '$lib/stores/auth.js'
  );
  const { get } = await import('svelte/store');
  if (get(isAuthenticatedStore)) {
    console.error(
      '[auth] Identity desync: authenticated UI state but anonymous backend actor — resetting auth stores'
    );
    isAuthenticatedStore.set(false);
    userIdentity.set(null);
    principal.set('');
  }
}
