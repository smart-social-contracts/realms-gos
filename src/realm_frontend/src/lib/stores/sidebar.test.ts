import { describe, expect, it, vi } from 'vitest';

const quarterRead = vi.fn();
vi.mock('$lib/realm', () => ({
	quarter: { read: (...args: unknown[]) => quarterRead(...args) },
	must: async (reply: unknown) => reply,
	RealmError: class extends Error {}
}));
vi.mock('$lib/auth.js', () => ({ settleAuthSession: vi.fn() }));
vi.mock('$lib/stores/auth.js', async () => ({ isAuthenticated: (await import('svelte/store')).writable(false) }));

import { sidebarCacheKey } from '../stores/sidebar';

describe('sidebarCacheKey', () => {
	it('scopes the cache key by locale', () => {
		expect(sidebarCacheKey('en')).toBe('sidebar_cache:en');
		expect(sidebarCacheKey('es')).toBe('sidebar_cache:es');
		expect(sidebarCacheKey('ca-valencia')).toBe('sidebar_cache:ca-valencia');
	});
});

describe('loadSidebar', () => {
	function deferred<T>() {
		let resolve!: (v: T) => void;
		const promise = new Promise<T>((r) => (resolve = r));
		return { promise, resolve };
	}
	const extensionPage = (id: string) => ({
		ok: true,
		extensions: [{ id, available: true, sidebar: { category: 'governance' } }],
		next: null
	});

	it('keeps the newest actor result when an older call resolves last', async () => {
		const { loadSidebar, sidebarConfig } = await import('../stores/sidebar');
		const { isAuthenticated } = await import('$lib/stores/auth.js');
		const { get } = await import('svelte/store');
		(isAuthenticated as any).set(true);
		const capital = deferred<unknown>();
		const quarter = deferred<unknown>();
		quarterRead.mockReturnValueOnce(capital.promise).mockReturnValueOnce(quarter.promise);
		const first = loadSidebar(null);
		const second = loadSidebar(null);
		quarter.resolve(extensionPage('member_dashboard'));
		await second;
		capital.resolve(extensionPage('guest_page'));
		await first;
		expect(get(sidebarConfig)?.manifests?.[0]?.id).toBe('member_dashboard');
	});
});
