import { writable, get } from 'svelte/store';
import type { SidebarCategory, SidebarConfig, SidebarManifest, SidebarNavItem } from '$lib/config/sidebar';
import { quarter, must } from '$lib/realm';
import { settleAuthSession } from '$lib/auth.js';
import { isAuthenticated } from '$lib/stores/auth.js';
import { resolveMemberHomeHref } from '../extension-home';

export const sidebarConfig = writable<SidebarConfig | null>(null);
export const sidebarLoading = writable(false);

const CACHE_KEY_PREFIX = 'sidebar_cache';

const CATEGORIES: [string, string][] = [
	['home', 'Home'],
	['public_services', 'Public Services'],
	['land_territory', 'Territory'],
	['governance', 'Governance'],
	['people_access', 'People & Access'],
	['finances', 'Finances'],
	['realm_management', 'Realm Management'],
	['administration', 'Administration'],
	['settings', 'Settings'],
	['other', 'Other']
];

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

function labelText(label: unknown, locale: string): string {
	if (typeof label === 'string') return label;
	const map = (label || {}) as Record<string, string>;
	return map[locale] || map.en || '';
}

/** The menu rows of the extensions available to the caller, from every page of ``extension_list``. */
async function sidebarRows() {
	const rows: { id: string; sidebar: Record<string, any> }[] = [];
	let from: number | null = 1;
	while (from) {
		const page = await must(quarter.read('extension_list', { from, count: 100 }));
		rows.push(...page.extensions.filter((e: { available: boolean }) => e.available));
		from = page.next;
	}
	return rows;
}

/**
 * Build the sidebar from the extensions installed here: each extension's ``sidebar`` settings
 * (its manifest's, merged with sidebar_configure) give its label, icon, category and order.
 * It asks the active quarter (or the realm), the actor callers pass as ``_backend``.
 */
export async function loadSidebar(_backend: unknown, locale: string = 'en'): Promise<void> {
	const cached = readCache(locale);
	if (cached) {
		sidebarConfig.set(cached);
	}

	sidebarLoading.set(true);
	try {
		await settleAuthSession();
		// extension_list needs a signed-in caller; visitors get the public menu.
		if (!get(isAuthenticated)) return;
		const rows = await sidebarRows();
		const manifests: SidebarManifest[] = [];
		const welcomeItems: SidebarNavItem[] = [];
		const mundusItems: SidebarNavItem[] = [];
		const grouped = new Map<string, { item: SidebarNavItem; order: number }[]>();

		for (const { id, sidebar } of rows) {
			const show = sidebar.show ?? sidebar.show_in_sidebar ?? true;
			const category: string = sidebar.category || sidebar.categories?.[0] || 'other';
			const label = labelText(sidebar.label ?? sidebar.sidebar_label, locale);
			manifests.push({
				id,
				name: id,
				sidebar_label: sidebar.label ?? sidebar.sidebar_label,
				is_default: sidebar.is_default === true,
				show_in_sidebar: show,
				categories: [category]
			});
			if (!show) continue;
			const item: SidebarNavItem = {
				label: label || id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
				icon: `ti-${sidebar.icon || 'layout-dashboard'}`,
				extension_id: id,
				href: `/extensions/${id}`
			};
			if (sidebar.is_default === true || label.toLowerCase() === 'my dashboard') {
				welcomeItems.push(item);
			} else if (category === 'mundus') {
				mundusItems.push(item);
			} else {
				grouped.set(category, [...(grouped.get(category) || []), { item, order: sidebar.order ?? 0 }]);
			}
		}

		const known = new Map(CATEGORIES);
		const categories: SidebarCategory[] = [
			...CATEGORIES.map(([id]) => id).filter((id) => grouped.has(id)),
			...[...grouped.keys()].filter((id) => !known.has(id)).sort()
		].map((id) => ({
			id,
			label: known.get(id) || id.replace(/_/g, ' '),
			items: grouped
				.get(id)!
				.sort((a, b) => a.order - b.order || a.item.label.localeCompare(b.item.label))
				.map((entry) => entry.item)
		}));

		const config: SidebarConfig = {
			welcomeItems,
			mundusItems,
			categories,
			manifests,
			defaultPath: resolveMemberHomeHref({ manifests, welcomeItems, locale }),
			extensionOverrides: {}
		};

		sidebarConfig.set(config);
		writeCache(locale, config);
	} catch (e) {
		console.error('Failed to load sidebar:', e);
		if (!get(sidebarConfig) && cached) {
			sidebarConfig.set(cached);
		}
	} finally {
		sidebarLoading.set(false);
	}
}
