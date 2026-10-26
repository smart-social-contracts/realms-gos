// Runtime configuration — test mode flags.
//
// Canister ids are NOT baked in here: the realm backend reports its own
// (status().canisters) and the tokens it offers (token_catalog).
//
// Test mode flags are read from the backend status() response at runtime,
// NOT baked in at build time. This means self-upgrades and environment changes
// preserve the correct flags without rebuilding the frontend.
//
// The backend enforces a hard gate: test flags cannot be set on mainnet (network=ic).

export const CONFIG = {};

// --- Test mode flags ---
// These are read from the realmInfo store (populated by backend status()).
// For plain JS files (auth.js, canisters.js), use the getter functions.
// For Svelte components, import the derived stores from realmInfo.ts directly.

import { get } from 'svelte/store';
import { realmInfo } from '$lib/stores/realmInfo';

export function getTestMode() { return get(realmInfo).testMode; }
export function getTestModeIIBypass() {
  if (!__REALMS_TEST_BUILD__) return false;
  if (get(realmInfo).testModeIIBypass) return true;
  // Sync hint from /canister_ids.js before backend status() loads (portal iframe boot).
  return !!globalThis.__CANISTER_IDS?.test_mode_ii_bypass;
}
export function getTestModeUserSelfRegistration() { return get(realmInfo).testModeUserSelfRegistration; }
export function getTestModeDemoData() { return get(realmInfo).testModeDemoData; }
export function getTestModeSkipTerms() { return get(realmInfo).testModeSkipTerms; }

// Legacy constant exports for backward compatibility during migration.
// These evaluate once at import time (before status loads) so they're always false.
// New code should use the getter functions or the derived stores from realmInfo.ts.
export const TEST_MODE = false;
export const TEST_MODE_II_BYPASS = false;
export const TEST_MODE_USER_SELF_REGISTRATION = false;
export const TEST_MODE_DEMO_DATA = false;
export const TEST_MODE_SKIP_TERMS = false;
export const TEST_MODE_SKIP_PASSPORT_ZKPROOF = false;

export const DEV_PORT = 8000;
