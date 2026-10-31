/**
 * Lightweight HTTP client for talking to a `file_registry` canister from
 * the realm_frontend.
 *
 * Why HTTP and not candid? The file_registry canister exposes both:
 *   - candid query methods: list_extensions / list_codices / latest_version /
 *     get_extension_manifest (see src/file_registry/main.py)
 *   - HTTP routes: GET /api/extensions, GET /api/codices, GET /{namespace}/{path}
 *     (handled by `_handle_http` in the same file)
 *
 * The HTTP route is preferred here because:
 *   1. It works for any registry the realm is linked to without the
 *      realm_frontend having to bake every registry's candid actor into
 *      its bundle (the realm_frontend only ships the realm_backend actor).
 *   2. It re-uses the same URL scheme as `extension-loader.ts` —
 *      one helper (`fileRegistryBaseUrlFor`) covers both runtime bundle
 *      loading and registry browsing.
 *   3. The registry already adds permissive CORS headers in
 *      `_http_response`, so the browser can read the response directly.
 */
import { fileRegistryBaseUrlFor } from '$lib/extension-loader';

export interface RegistryExtension {
  ext_id: string;
  versions: string[];
  latest: string;
  manifest: Record<string, unknown> | null;
}

export interface RegistryCodex {
  codex_id: string;
  versions: string[];
  latest: string;
}

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} from ${url}: ${await resp.text().catch(() => '')}`);
  }
  return (await resp.json()) as T;
}

/**
 * List every extension published in the given file_registry canister.
 * Returns the same shape as the candid `list_extensions` query.
 */
export async function listRegistryExtensions(
  registryCanisterId: string,
): Promise<RegistryExtension[]> {
  const base = fileRegistryBaseUrlFor(registryCanisterId);
  return fetchJson<RegistryExtension[]>(`${base}/api/extensions`);
}

/**
 * List every codex package published in the given file_registry canister.
 */
export async function listRegistryCodices(
  registryCanisterId: string,
): Promise<RegistryCodex[]> {
  const base = fileRegistryBaseUrlFor(registryCanisterId);
  return fetchJson<RegistryCodex[]>(`${base}/api/codices`);
}

/**
 * Compare two semver-ish version strings ("1.2.3", "0.1.0", "1.2.3-rc1").
 * Returns a negative number if a < b, positive if a > b, 0 if equal.
 *
 * This is a deliberately simple comparator (matching `_parse_semver` on the
 * registry side); pre-release tags compare lexically, which is enough for
 * the package-manager UI's "is the installed version older than the
 * registry's latest?" check.
 */
export function compareVersions(a: string, b: string): number {
  const pa = a.split('-', 1)[0].split('.').map((n) => parseInt(n, 10) || 0);
  const pb = b.split('-', 1)[0].split('.').map((n) => parseInt(n, 10) || 0);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x !== y) return x - y;
  }
  // Same numeric prefix → fall back to lexical comparison of full string
  // so "1.0.0-rc1" < "1.0.0".
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/**
 * Fetch a specific version's manifest from the file_registry over HTTP.
 *
 * Returns null if the manifest is missing (404), unparseable, or the
 * fetch itself fails. Callers should treat null as "no manifest
 * available", not as an error to surface to the user.
 *
 * The URL convention matches `extension-loader.ts`:
 *   {base}/ext/{extId}/{version}/manifest.json
 *
 * Useful for the package_manager UI when it wants to show richer
 * metadata (description, icon, categories, sidebar_label) for a
 * version that the listing endpoint summarises but does not include
 * a manifest for.
 */
export async function getExtensionManifest(
  registryCanisterId: string,
  extId: string,
  version: string,
): Promise<Record<string, unknown> | null> {
  const base = fileRegistryBaseUrlFor(registryCanisterId);
  const url = `${base}/ext/${extId}/${version}/manifest.json`;
  try {
    const resp = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!resp.ok) return null;
    return (await resp.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * Look up the latest published version of a single extension or codex
 * in the given file_registry canister.
 *
 * `category` must be either `'ext'` (extension) or `'codex'`.
 *
 * Implementation note: this delegates to the same HTTP listing
 * endpoints (`/api/extensions`, `/api/codices`) used by
 * `listRegistryExtensions` / `listRegistryCodices`, rather than
 * constructing a separate `@dfinity/agent` Actor for the registry
 * canister's candid `latest_version` query. The HTTP response already
 * carries `latest`; using it here keeps the file_registry client free
 * of a per-canister Actor (matching the rationale in this file's
 * top-level docstring) and avoids bundling a second IDL into
 * realm_frontend just to read a single field.
 *
 * Returns undefined if the item is not present in the registry.
 */
export async function latestVersion(
  registryCanisterId: string,
  category: 'ext' | 'codex',
  itemId: string,
): Promise<string | undefined> {
  if (category === 'ext') {
    const exts = await listRegistryExtensions(registryCanisterId);
    return exts.find((e) => e.ext_id === itemId)?.latest;
  }
  const codices = await listRegistryCodices(registryCanisterId);
  return codices.find((c) => c.codex_id === itemId)?.latest;
}

/**
 * Return the list of file_registry canister IDs this frontend can browse.
 *
 * The build-time `CANISTER_ID_FILE_REGISTRY` env var (injected by `dfx deploy` /
 * vite-plugin-environment) comes first, then the `file_registry` the realm's
 * `canister_ids.js` names at runtime, which a build without the env var relies on.  In the future
 * the realm backend could expose file_registry IDs alongside the existing
 * realm_registry IDs in the status response, at which point this function
 * should merge both sources.
 *
 * NOTE: `$realmInfo.registries` contains *realm_registry_backend* canister
 * IDs (the realm directory), NOT file_registry IDs.  The two canisters serve
 * completely different purposes — see Issue #168.
 */
export function getFileRegistryCanisterIds(): string[] {
  const id: string | undefined =
    process.env.CANISTER_ID_FILE_REGISTRY ||
    (import.meta as any).env?.VITE_FILE_REGISTRY_CANISTER_ID ||
    (globalThis as any).__CANISTER_IDS?.file_registry;
  return id ? [id] : [];
}

export const _internal = { fetchJson };
