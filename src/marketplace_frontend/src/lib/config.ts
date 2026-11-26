import { accountPagesAlpha } from './accountAlpha.ts';

// Build-time + runtime configuration for the marketplace frontend.
//
// Values are injected by Vite at build time from the dfx-generated
// .env file (see vite.config.js: the ``CANISTER_*`` and ``DFX_*`` env
// vars are exposed via vite-plugin-environment).

const env = (typeof process !== 'undefined' && (process as any).env) || ({} as Record<string, string | undefined>);

// NOTE: in the browser bundle `process` is undefined, so `pick()` below only
// works in SSR/build contexts. Vite replaces `import.meta.env` with a real
// object literal containing all VITE_* vars from the build environment, so
// browser-visible config must read from `_viteEnv` (direct property access
// also works, but the object form keeps dynamic key helpers possible).
const _viteEnv: Record<string, string | undefined> =
  typeof import.meta !== 'undefined' && (import.meta as any).env ? (import.meta as any).env : {};

function pick(...keys: string[]): string {
  for (const k of keys) {
    const v = _viteEnv[k] ?? env[k];
    if (v && String(v).trim() !== '') return String(v);
  }
  return '';
}

/**
 * A setting written at deploy time into ``/canister_ids.js``
 * (``globalThis.__CANISTER_IDS``, loaded synchronously by app.html before the
 * bundle): a canister id, or the forum's URL. One dist serves every
 * environment, so this wins over whatever the build had in its env — which,
 * under Casals, is nothing.
 */
export function runtimeSetting(key: string, ids: unknown = (globalThis as any).__CANISTER_IDS): string {
  const v = ids && typeof ids === 'object' ? (ids as Record<string, unknown>)[key] : undefined;
  return typeof v === 'string' && v.trim() !== '' ? v.trim() : '';
}

function canisterId(runtimeKey: string, ...envKeys: string[]): string {
  return runtimeSetting(runtimeKey) || pick(...envKeys);
}

/**
 * Query the marketplace backend for the Realms GOS Casals frontend principal.
 * Set by the sheet's ``set_casals_frontend_canister_id`` config row — not baked into this SPA.
 * Empty string if unset or fetch fails.
 */
export async function resolveCasalsUrlLive(): Promise<string> {
  const marketplace = canisterId(
    'marketplace_backend',
    'VITE_CANISTER_ID_MARKETPLACE_BACKEND',
    'CANISTER_ID_MARKETPLACE_BACKEND'
  );
  if (!marketplace || typeof window === 'undefined') return '';
  try {
    const { Actor, HttpAgent } = await import('@dfinity/agent');
    const agent = new HttpAgent({ host: 'https://icp-api.io' });
    const actor = Actor.createActor(
      ({ IDL }) =>
        IDL.Service({
          get_casals_frontend_canister_id_q: IDL.Func([], [IDL.Text], ['query']),
        }),
      { agent, canisterId: marketplace }
    ) as { get_casals_frontend_canister_id_q: () => Promise<string> };
    const id = String(await actor.get_casals_frontend_canister_id_q() || '').trim();
    return id ? `https://${id}.icp0.io` : '';
  } catch (err) {
    console.warn('live Casals URL fetch failed:', err);
    return '';
  }
}

export const CONFIG = {
  // Internet Identity URL used by @dfinity/auth-client.
  // Defaults to mainnet II; local dev points the env var at the local II canister.
  internet_identity_url:
    pick('VITE_INTERNET_IDENTITY_URL') ||
    (pick('CANISTER_ID_INTERNET_IDENTITY')
      ? `http://${pick('CANISTER_ID_INTERNET_IDENTITY')}.localhost:4943`
      : 'https://identity.ic0.app'),

  // Marketplace + file_registry canister ids — primarily resolved via the
  // generated declarations module (declarations/marketplace_backend), but
  // exposed here for convenience (e.g. constructing file URLs).
  marketplace_canister_id: canisterId('marketplace_backend', 'VITE_CANISTER_ID_MARKETPLACE_BACKEND', 'CANISTER_ID_MARKETPLACE_BACKEND'),
  file_registry_canister_id: canisterId('file_registry', 'VITE_CANISTER_ID_FILE_REGISTRY', 'CANISTER_ID_FILE_REGISTRY'),
  internet_identity_canister_id: pick('VITE_CANISTER_ID_INTERNET_IDENTITY', 'CANISTER_ID_INTERNET_IDENTITY'),

  // Off-chain billing service that handles credit-card → Stripe → license payment.
  // Plan §4.3.1 names this BILLING_SERVICE_URL; we also accept the
  // VITE_-prefixed form so it can be set per Vite's standard env-loader
  // convention without needing the dfx-style CANISTER_ prefix.
  billing_service_url:
    pick('BILLING_SERVICE_URL', 'VITE_BILLING_SERVICE_URL') || 'https://billing.realmsgos.dev',

  // Per-environment landing page metadata (set at build time for demo/staging/test).
  env_name: pick('VITE_ENV_NAME'),
  portal_url: pick('VITE_PORTAL_URL'),
  realms_version: pick('VITE_REALMS_VERSION'),

  // The community forum (forum.realmsgos.org), linked from the nav only when the
  // sheet's canister_ids.js names it: the forum is deployed on its own schedule,
  // and a link to an origin that is not live yet is a broken link.
  forum_url: runtimeSetting('forum_url'),
};

/** Account pages stay visible but inert. On for staging/demo/test unless overridden. */
export const ACCOUNT_ALPHA: boolean = accountPagesAlpha({
  accountAlpha: pick('VITE_ACCOUNT_ALPHA'),
  envName: pick('VITE_ENV_NAME'),
});

// --- TEST_MODE umbrella and sub-flags ---
// Activation: URL param (?testmode=1), sessionStorage, or VITE_TEST_MODE env var.

function _readFlag(envKey: string, urlParam: string): boolean {
  if (_viteEnv[envKey] === 'true') return true;
  if (typeof window !== 'undefined') {
    const params = new URLSearchParams(window.location.search);
    if (params.get(urlParam) === '1') {
      sessionStorage.setItem(urlParam, '1');
      return true;
    }
    if (sessionStorage.getItem(urlParam) === '1') return true;
  }
  return false;
}

export const TEST_MODE: boolean = _readFlag('VITE_TEST_MODE', 'testmode');

function _testFlag(envKey: string, urlParam: string): boolean {
  if (!TEST_MODE) return false;
  return _readFlag(envKey, urlParam);
}

export const TEST_MODE_II_BYPASS: boolean = _testFlag('VITE_TEST_MODE_II_BYPASS', 'ii_bypass');
