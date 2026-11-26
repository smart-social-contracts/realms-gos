/**
 * Deterministic test identities for II-bypass mode.
 *
 * Seed layout (32 bytes): [0xED, 0x57, index₀, index₁, index₂, index₃, 0, …]
 *   - The identity index is encoded little-endian across 4 bytes (seed[2..5]),
 *     supporting ~4.3 billion distinct identities for large-scale E2E load
 *     tests. Indices 0–255 produce byte-identical seeds to the original
 *     1-byte layout, so existing rosters/principals are unaffected.
 *   - Index 0 = Identity 1 (Creator); indices 1…3 = Identity 2–4.
 *
 * Optional deploy-time overrides (via /canister_ids.js):
 *   - globalThis.__TEST_IDENTITY_PEms = string[] — PEM strings (or base64) used
 *     for login at that index instead of the deterministic seed.
 *
 * Reference roster (indices 0–9): config/deterministic-test-identity-principals.json
 *
 * The Ed25519 seed machinery is compiled out of production bundles
 * (__REALMS_TEST_BUILD__ === false).
 */

import { Ed25519KeyIdentity } from '@icp-sdk/core/identity';
import { Secp256k1KeyIdentity } from '@icp-sdk/core/identity/secp256k1';

/** Scanned by scripts/check_frontend_variant.py — test builds only. */
export const TEST_BUILD_AUTH_SENTINEL = __REALMS_TEST_BUILD__
  ? 'REALMS_TEST_BUILD_AUTH_BYPASS'
  : '';

export const TEST_IDENTITY_MAGIC = __REALMS_TEST_BUILD__ ? [0xed, 0x57] : [];
/** Indices 0–1 shown as fixed cards in the join-page picker (Identity 1–2). */
export const TEST_IDENTITY_FIXED_PICKER_MAX_INDEX = 1;
/** @deprecated alias for fixed picker cards */
export const TEST_IDENTITY_PICKER_MAX_INDEX = TEST_IDENTITY_FIXED_PICKER_MAX_INDEX;
/** Highest index for programmatic use (E2E may use more). */
export const DEFAULT_TEST_IDENTITY_MAX_INDEX = 8;
/** Highest supported identity index (human Identity number = index + 1). */
export const TEST_IDENTITY_MAX_INDEX = 0xffffffff;

/** Human-facing identity number (1-based). Index 0 → Identity 1. */
export function testIdentityNumber(index) {
  return index + 1;
}

/** Picker label for internal index (0-based). */
export function testIdentityLabel(index) {
  if (index === 0) return 'Identity 1 (Creator)';
  return `Identity ${index + 1}`;
}

/** @param {number} index */
export function testIdentitySeed(index) {
  if (!__REALMS_TEST_BUILD__) return new Uint8Array(32);
  const seed = new Uint8Array(32);
  seed[0] = TEST_IDENTITY_MAGIC[0];
  seed[1] = TEST_IDENTITY_MAGIC[1];
  // 4-byte little-endian index (backward compatible: 0–255 only sets seed[2]).
  seed[2] = index & 0xff;
  seed[3] = (index >>> 8) & 0xff;
  seed[4] = (index >>> 16) & 0xff;
  seed[5] = (index >>> 24) & 0xff;
  return seed;
}

/** @param {string} raw */
function decodePem(raw) {
  const trimmed = String(raw || '').trim();
  if (!trimmed) return '';
  if (trimmed.includes('BEGIN')) return trimmed;
  try {
    return atob(trimmed.replace(/-/g, '+').replace(/_/g, '/'));
  } catch {
    return trimmed;
  }
}

function expectedPrincipal(index) {
  const principals = globalThis.__TEST_IDENTITY_PRINCIPALS;
  if (!Array.isArray(principals) || !principals[index]) return '';
  return String(principals[index]);
}

/**
 * Create the identity used for login at `index`.
 * Uses __TEST_IDENTITY_PEms[index] when set, otherwise the deterministic Ed25519 seed.
 * @param {number} index
 */
export function createTestIdentityFromIndex(index) {
  if (!__REALMS_TEST_BUILD__) {
    throw new Error('Test identities are not available in production builds');
  }
  const pems = globalThis.__TEST_IDENTITY_PEms;
  const pemRaw = Array.isArray(pems) && pems[index] ? String(pems[index]) : '';
  if (pemRaw) {
    const pem = decodePem(pemRaw);
    const identity = Secp256k1KeyIdentity.fromPem(pem);
    const expected = expectedPrincipal(index);
    if (expected && identity.getPrincipal().toText() !== expected) {
      throw new Error(
        `${testIdentityLabel(index)} PEM principal ${identity.getPrincipal().toText()} does not match configured ${expected}`,
      );
    }
    return identity;
  }
  return Ed25519KeyIdentity.generate(testIdentitySeed(index));
}

/** @param {number} index */
export function testIdentityPrincipal(index) {
  if (!__REALMS_TEST_BUILD__) return '';
  return createTestIdentityFromIndex(index).getPrincipal().toText();
}

/** @param {string} principal */
export function shortPrincipal(principal) {
  if (!principal || principal.length < 12) return principal || '';
  return `${principal.slice(0, 8)}…${principal.slice(-8)}`;
}

/** @param {number} index */
export function normalizeTestIdentityIndex(index) {
  const parsed = Math.floor(Number(index));
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(TEST_IDENTITY_MAX_INDEX, parsed));
}

/** @param {number} identityNumber 1-based human identity number */
export function identityNumberToIndex(identityNumber) {
  return normalizeTestIdentityIndex(Number(identityNumber) - 1);
}

/** @param {number} identityNumber 1-based human identity number */
export function isValidCustomIdentityNumber(identityNumber) {
  const parsed = Math.floor(Number(identityNumber));
  if (!Number.isFinite(parsed)) return false;
  const minCustom = testIdentityNumber(TEST_IDENTITY_FIXED_PICKER_MAX_INDEX + 1);
  const maxCustom = testIdentityNumber(TEST_IDENTITY_MAX_INDEX);
  return parsed >= minCustom && parsed <= maxCustom;
}

/**
 * @param {number} index
 * @returns {{ index: number, label: string, principal: string, loginPrincipal: string, description: string, hasPem: boolean }}
 */
export function getTestIdentityPersona(index) {
  const normalized = normalizeTestIdentityIndex(index);
  if (!__REALMS_TEST_BUILD__) {
    return {
      index: normalized,
      label: testIdentityLabel(normalized),
      principal: '',
      loginPrincipal: '',
      hasPem: false,
      description: '',
    };
  }
  const pems = globalThis.__TEST_IDENTITY_PEms;
  const hasPem = Array.isArray(pems) && !!pems[normalized];
  const configured = expectedPrincipal(normalized);
  let loginPrincipal = '';
  try {
    loginPrincipal = testIdentityPrincipal(normalized);
  } catch {
    loginPrincipal = '';
  }
  return {
    index: normalized,
    label: testIdentityLabel(normalized),
    principal: configured || loginPrincipal,
    loginPrincipal,
    hasPem,
    description:
      normalized === 0
        ? 'Realm founder persona — use admin invite code "admin" if not yet registered'
        : `Deterministic test member ${testIdentityNumber(normalized)}`,
  };
}

/**
 * Fixed picker cards (Identity 1–2 by default).
 * @returns {{ index: number, label: string, principal: string, loginPrincipal: string, description: string, hasPem: boolean }[]}
 */
export function listTestIdentities(maxIndex = TEST_IDENTITY_FIXED_PICKER_MAX_INDEX) {
  if (!__REALMS_TEST_BUILD__) return [];
  const items = [];
  for (let index = 0; index <= maxIndex; index++) {
    items.push(getTestIdentityPersona(index));
  }
  return items;
}
