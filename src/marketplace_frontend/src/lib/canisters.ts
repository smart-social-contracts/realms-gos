// Lazy actor wiring for the marketplace_backend canister.
//
// Mirrors the pattern in src/realm_registry_frontend/src/lib/canisters.js
// — a Proxy that resolves the actor on first method call so we don't
// touch the network during Svelte component init.

import { building } from '$app/environment';

import { getIdentity } from './auth';

function dummyActor() {
  return new Proxy({}, { get() { throw new Error('Canister invoked while building'); } });
}

const buildingOrTesting = building || (typeof process !== 'undefined' && process.env?.NODE_ENV === 'test');

function isLocalDevelopment(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    window.location.hostname.includes('localhost') ||
    window.location.hostname.includes('127.0.0.1')
  );
}

let cachedActorPromise: Promise<any> | null = null;
let cachedIdentityPrincipal: string | null = null;

async function buildActor() {
  if (buildingOrTesting) return dummyActor();

  // Dynamic imports so the build step doesn't crash if declarations
  // haven't been generated yet (e.g. first dev install).
  const { createActor, canisterId } = await import('declarations/marketplace_backend');
  const { HttpAgent } = await import('@icp-sdk/core/agent');

  const identity = await getIdentity();
  const agentOptions: any = identity ? { identity } : {};
  const agent = new HttpAgent(agentOptions);

  if (isLocalDevelopment()) {
    try {
      await agent.fetchRootKey();
    } catch (e) {
      console.warn('fetchRootKey failed (ok in local dev):', e);
    }
  }

  // /canister_ids.js, written by the orchestrator at deploy time, wins over the
  // build-time declaration: one dist serves every environment.
  const runtimeId = (globalThis as any).__CANISTER_IDS?.marketplace_backend;
  return createActor(runtimeId || canisterId, { agent });
}

async function getActor() {
  // If the auth state changes (login/logout), rebuild so the agent picks
  // up the new identity. Cheap to recompute — small object.
  const id = await getIdentity();
  const idStr = id ? id.getPrincipal().toText() : null;
  if (cachedActorPromise && cachedIdentityPrincipal === idStr) return cachedActorPromise;
  cachedIdentityPrincipal = idStr;
  cachedActorPromise = buildActor();
  return cachedActorPromise;
}

/** Force the next actor call to rebuild — call after login/logout. */
export function invalidateActor() {
  cachedActorPromise = null;
  cachedIdentityPrincipal = null;
}

export const marketplace = new Proxy({} as any, {
  get(_target, prop: string) {
    return async (...args: unknown[]) => {
      const actor = await getActor();
      const fn = (actor as any)[prop];
      if (typeof fn !== 'function') {
        throw new Error(`marketplace_backend has no method '${prop}'`);
      }
      return fn(...args);
    };
  },
});
