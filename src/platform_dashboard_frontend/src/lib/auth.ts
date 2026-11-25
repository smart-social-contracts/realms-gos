import { AuthClient, type AuthClientCreateOptions } from '@icp-sdk/auth/client';
import type { Identity } from '@icp-sdk/core/agent';
import type { Principal } from '@icp-sdk/core/principal';
import { writable, type Writable } from 'svelte/store';

import { CONFIG, TEST_MODE_II_BYPASS } from './config';

// Internet Identity's backend canister has this id on mainnet and on icp-cli's
// local network (`ii: true`) alike.
const II_BACKEND_CANISTER_ID = 'rdmx6-jaaaa-aaaaa-aaadq-cai';

// The session lengths @dfinity/auth-client applied by default, kept so the move
// to @icp-sdk/auth (whose defaults are 30 days and 7 days) changes neither.
const SESSION_TTL_NS = BigInt(8 * 60 * 60 * 1_000_000_000); // 8 hours
const SESSION_IDLE_NS = BigInt(10 * 60 * 1_000_000_000); // 10 minutes

/**
 * Where @icp-sdk/auth signs in. Every mainnet II domain is served by the
 * library's default (id.ai); a local II renders its sign-in at `/authorize` and
 * mints through the local replica, whose root key must be fetched.
 */
function identityProviderOptions(iiUrl: string): AuthClientCreateOptions {
  const host = new URL(iiUrl).hostname;
  if (!host.endsWith('localhost') && host !== '127.0.0.1') return {};
  return {
    identityProvider: { authorizeUrl: new URL('/authorize', iiUrl).href, canisterId: II_BACKEND_CANISTER_ID },
    agentOptions: { host: window.location.origin, shouldFetchRootKey: true },
  };
}

let authClient: AuthClient | null = null;

export const isAuthenticated: Writable<boolean> = writable(false);
export const principalStore: Writable<Principal | null> = writable(null);

// --- Test mode support ---
let _testIdentity: any = null;
let _testLoggedIn = false;

async function _createTestIdentity(): Promise<any> {
  if (_testIdentity) return _testIdentity;
  const { Ed25519KeyIdentity } = await import('@icp-sdk/core/identity');
  const seed = new Uint8Array(32);
  seed[0] = 0xED; seed[1] = 0x57;
  _testIdentity = Ed25519KeyIdentity.generate(seed);
  console.log(`[TEST MODE] Generated test identity: ${_testIdentity.getPrincipal().toText()}`);
  return _testIdentity;
}

function ensureClient(): AuthClient {
  authClient ??= new AuthClient(identityProviderOptions(CONFIG.internet_identity_url));
  return authClient;
}

/** The identity this tab can act as, or null: a stored record alone is not a sign-in. */
async function heldIdentity(): Promise<Identity | null> {
  try {
    const identity = await ensureClient().getIdentity();
    return identity.getPrincipal().isAnonymous() ? null : identity;
  } catch (err) {
    console.warn('Stored session is not usable:', err);
    return null;
  }
}

export async function bootstrapAuth(): Promise<void> {
  if (TEST_MODE_II_BYPASS) {
    console.log('[TEST MODE] bootstrapAuth — II bypass active, auto-login');
    await login();
    return;
  }
  const id = await heldIdentity();
  if (id) {
    isAuthenticated.set(true);
    principalStore.set(id.getPrincipal());
  }
}

export async function login(): Promise<{ identity: Identity | null; principal: Principal | null }> {
  if (TEST_MODE_II_BYPASS) {
    const identity = await _createTestIdentity();
    _testLoggedIn = true;
    const principal = identity.getPrincipal();
    isAuthenticated.set(true);
    principalStore.set(principal);
    console.log(`[TEST MODE] Logged in with principal: ${principal.toText()}`);
    return { identity, principal };
  }

  try {
    const identity = await ensureClient().signIn({ maxTimeToLive: SESSION_TTL_NS, maxTimeToIdle: SESSION_IDLE_NS });
    const principal = identity.getPrincipal();
    isAuthenticated.set(true);
    principalStore.set(principal);
    return { identity, principal };
  } catch (err) {
    console.error('II login failed:', err);
    return { identity: null, principal: null };
  }
}

export async function logout(): Promise<void> {
  if (TEST_MODE_II_BYPASS) {
    _testLoggedIn = false;
    _testIdentity = null;
    isAuthenticated.set(false);
    principalStore.set(null);
    console.log('[TEST MODE] Logged out');
    return;
  }
  try {
    // Ends the session at Internet Identity, not only in this browser.
    await ensureClient().signOut();
  } catch (err) {
    // This browser is signed out either way; the session at II lasts until it expires.
    console.error('Internet Identity did not end the session:', err);
  }
  isAuthenticated.set(false);
  principalStore.set(null);
}

export async function getIdentity(): Promise<Identity | null> {
  if (TEST_MODE_II_BYPASS) {
    return _testLoggedIn && _testIdentity ? _testIdentity : null;
  }
  return heldIdentity();
}
