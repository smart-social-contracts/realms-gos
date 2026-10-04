import { backend } from '$lib/canisters';

export interface ExtensionManifestInfo {
	name: string;
	version?: string;
	description?: string;
	author?: string;
	doc_url?: string | null;
	repository?: string;
	categories?: string[];
	profiles?: string[];
	runtime?: string;
	sdk_version?: string;
	capabilities?: string[];
	entry_access?: { functions?: Record<string, string> };
	[key: string]: unknown;
}

let cache: Record<string, ExtensionManifestInfo> | null = null;
let loadPromise: Promise<Record<string, ExtensionManifestInfo>> | null = null;

export const MANIFEST_FETCH_RETRY_DELAYS_MS = [1000, 3000] as const;

const LEGACY_REALMS_EXTENSIONS_PREFIX =
	'https://github.com/smart-social-contracts/realms-gos/tree/main/extensions/';
const EXTENSIONS_REPO_PREFIX =
	'https://github.com/smart-social-contracts/realms-extensions/tree/main/extensions/';

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

export function extensionRepoUrl(extensionId: string): string {
	return `${EXTENSIONS_REPO_PREFIX}${extensionId}`;
}

export function resolveExtensionRepoUrl(
	manifest: ExtensionManifestInfo | null,
	extensionId?: string,
): string | null {
	if (!manifest) {
		return extensionId ? extensionRepoUrl(extensionId) : null;
	}

	const doc = typeof manifest.doc_url === 'string' ? manifest.doc_url.trim() : '';
	if (doc) {
		if (doc.startsWith(LEGACY_REALMS_EXTENSIONS_PREFIX)) {
			return doc.replace(LEGACY_REALMS_EXTENSIONS_PREFIX, EXTENSIONS_REPO_PREFIX);
		}
		const legacyBareMatch = doc.match(
			/^https:\/\/github\.com\/smart-social-contracts\/extensions\/([^/]+)$/,
		);
		if (legacyBareMatch) {
			return extensionRepoUrl(legacyBareMatch[1]);
		}
		return doc;
	}

	const repo = typeof manifest.repository === 'string' ? manifest.repository.trim() : '';
	if (repo) return repo;

	return extensionId ? extensionRepoUrl(extensionId) : null;
}

export function parseExtensionIdFromPath(pathname: string): string | null {
	const match = pathname.match(/\/extensions\/([^/]+)/);
	return match?.[1] ?? null;
}

async function fetchExtensionManifestsOnce(): Promise<Record<string, ExtensionManifestInfo>> {
	const raw = await backend.list_runtime_extensions();
	const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
	return parsed?.all_manifests ?? {};
}

export async function fetchExtensionManifests(): Promise<Record<string, ExtensionManifestInfo>> {
	if (cache) return cache;
	if (loadPromise) return loadPromise;

	loadPromise = (async () => {
		const maxAttempts = 1 + MANIFEST_FETCH_RETRY_DELAYS_MS.length;
		let lastError: unknown;

		for (let attempt = 0; attempt < maxAttempts; attempt++) {
			try {
				const manifests = await fetchExtensionManifestsOnce();
				cache = manifests;
				return cache;
			} catch (e) {
				lastError = e;
				console.warn(
					`[extension-manifest] failed to load manifests (attempt ${attempt + 1}/${maxAttempts}):`,
					e,
				);
				if (attempt < MANIFEST_FETCH_RETRY_DELAYS_MS.length) {
					await sleep(MANIFEST_FETCH_RETRY_DELAYS_MS[attempt]);
				}
			}
		}

		loadPromise = null;
		cache = null;
		console.warn('[extension-manifest] exhausted manifest fetch retries:', lastError);
		return {};
	})();

	return loadPromise;
}

export async function getExtensionManifest(
	extId: string,
): Promise<ExtensionManifestInfo | null> {
	const manifests = await fetchExtensionManifests();
	return manifests[extId] ?? null;
}

export async function getExtensionManifestWithRetry(
	extId: string,
): Promise<ExtensionManifestInfo | null> {
	const attempts = 1 + MANIFEST_FETCH_RETRY_DELAYS_MS.length;

	for (let attempt = 0; attempt < attempts; attempt++) {
		if (attempt > 0) {
			clearExtensionManifestCache();
			await sleep(MANIFEST_FETCH_RETRY_DELAYS_MS[attempt - 1]);
		}

		const manifest = await getExtensionManifest(extId);
		if (manifest) return manifest;
	}

	return null;
}

export function clearExtensionManifestCache(): void {
	cache = null;
	loadPromise = null;
}
