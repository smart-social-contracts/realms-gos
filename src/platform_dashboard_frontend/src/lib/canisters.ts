import { building } from '$app/environment';
import { HttpAgent } from '@icp-sdk/core/agent';
import { getIdentity } from './auth';
import { CONFIG } from './config';

const buildingOrTesting =
  building || (typeof process !== 'undefined' && process.env?.NODE_ENV === 'test');

function dummyActor(): any {
  return new Proxy(
    {},
    {
      get() {
        throw new Error('Canister invoked during build');
      },
    },
  );
}

function isLocalDevelopment(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    window.location.hostname.includes('localhost') ||
    window.location.hostname.includes('127.0.0.1')
  );
}

async function makeAgent(): Promise<HttpAgent> {
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
  return agent;
}

type ActorCache = { promise: Promise<any> | null; identityPrincipal: string | null };

function makeLazyProxy(
  importFn: () => Promise<{ createActor: any; canisterId: any }>,
): { proxy: any; invalidate: () => void } {
  const cache: ActorCache = { promise: null, identityPrincipal: null };

  async function getActor() {
    const id = await getIdentity();
    const idStr = id ? id.getPrincipal().toText() : null;
    if (cache.promise && cache.identityPrincipal === idStr) return cache.promise;
    cache.identityPrincipal = idStr;
    cache.promise = (async () => {
      if (buildingOrTesting) return dummyActor();
      const { createActor, canisterId } = await importFn();
      const agent = await makeAgent();
      return createActor(canisterId, { agent });
    })();
    return cache.promise;
  }

  const proxy = new Proxy({} as any, {
    get(_target, prop: string) {
      return async (...args: unknown[]) => {
        const actor = await getActor();
        const fn = (actor as any)[prop];
        if (typeof fn !== 'function') {
          throw new Error(`Actor has no method '${prop}'`);
        }
        return fn(...args);
      };
    },
  });

  return {
    proxy,
    invalidate() {
      cache.promise = null;
      cache.identityPrincipal = null;
    },
  };
}

const registry = makeLazyProxy(() => import('declarations/realm_registry_backend'));
const installer = makeLazyProxy(() => import('declarations/realm_installer'));
const marketplace = makeLazyProxy(() => import('declarations/marketplace_backend'));
const fileRegistry = makeLazyProxy(() => import('declarations/file_registry'));

export const registryActor = registry.proxy;
export const installerActor = installer.proxy;
export const marketplaceActor = marketplace.proxy;
export const fileRegistryActor = fileRegistry.proxy;

export function invalidateActors() {
  registry.invalidate();
  installer.invalidate();
  marketplace.invalidate();
  fileRegistry.invalidate();
}

/**
 * Create an ad-hoc actor for a specific realm_backend canister by ID.
 * Not cached -- each call creates a fresh actor with current identity.
 */
export async function createRealmBackendActor(canisterId: string): Promise<any> {
  if (buildingOrTesting) return dummyActor();
  const { createActor } = await import('declarations/realm_backend');
  const agent = await makeAgent();
  return createActor(canisterId, { agent });
}
