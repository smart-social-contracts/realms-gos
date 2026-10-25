import { writable, get } from 'svelte/store';
import type { SidebarConfig, SidebarManifest } from '$lib/config/sidebar';
import { resolveMemberHomeHref } from '../extension-home';

export const sidebarConfig = writable<SidebarConfig | null>(null);
export const sidebarLoading = writable(false);

const CACHE_KEY_PREFIX = 'sidebar_cache';

/** Locale-scoped localStorage key for cached sidebar config. */
export function sidebarCacheKey(locale: string): string {
	return `${CACHE_KEY_PREFIX}:${locale}`;
}

function readCache(locale: string): SidebarConfig | null {
	try {
		const raw = localStorage.getItem(sidebarCacheKey(locale));
		if (!raw) return null;
		return JSON.parse(raw) as SidebarConfig;
	} catch {
		return null;
	}
}

function writeCache(locale: string, config: SidebarConfig): void {
	try {
		localStorage.setItem(sidebarCacheKey(locale), JSON.stringify(config));
	} catch {
		// storage full or unavailable
	}
}

/**
 * Load the sidebar from get_sidebar plus get_sidebar_manifests (the MY REALM
 * / My Dashboard source). Host navigate.home reuses those same rows.
 */
async function loadSidebarManifests(
	backend: { get_sidebar_manifests?: () => Promise<string> },
): Promise<SidebarManifest[]> {
	if (typeof backend.get_sidebar_manifests !== 'function') return [];
	try {
		const raw = await backend.get_sidebar_manifests();
		const parsed = JSON.parse(raw);
		return parsed?.success && Array.isArray(parsed.manifests) ? parsed.manifests : [];
	} catch {
		return [];
	}
}

let latestSidebarRequest = 0;

export async function loadSidebar(
	backend: {
		get_sidebar: (args: string) => Promise<string>;
		get_sidebar_manifests?: () => Promise<string>;
	},
	locale: string = 'en',
): Promise<void> {
	const request = ++latestSidebarRequest;
	const cached = readCache(locale);
	if (cached) {
		sidebarConfig.set(cached);
	}

	sidebarLoading.set(true);
	try {
		const [raw, manifests] = await Promise.all([
			backend.get_sidebar(JSON.stringify({ locale })),
			loadSidebarManifests(backend),
		]);
		// The actor swaps capital → home quarter during boot; the capital's
		// guest menu must not overwrite (or be cached over) the member's.
		if (request !== latestSidebarRequest) return;
		const parsed = JSON.parse(raw);

		if (!parsed?.success) {
			throw new Error(parsed?.error || 'Backend returned failure');
		}

		const welcomeItems = parsed.welcome_items || [];
		const config: SidebarConfig = {
			welcomeItems,
			mundusItems: parsed.mundus_items || [],
			categories: parsed.categories || [],
			manifests,
			defaultPath: resolveMemberHomeHref({ manifests, welcomeItems, locale }),
			extensionOverrides: parsed.extension_overrides || {},
		};

		sidebarConfig.set(config);
		writeCache(locale, config);
	} catch (e) {
		if (request !== latestSidebarRequest) return;
		console.error('Failed to load sidebar:', e);
		if (!get(sidebarConfig) && cached) {
			sidebarConfig.set(cached);
		}
	} finally {
		if (request === latestSidebarRequest) sidebarLoading.set(false);
	}
}
