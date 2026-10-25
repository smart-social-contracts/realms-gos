import { describe, expect, it } from 'vitest';
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
	const reply = (label: string) =>
		JSON.stringify({ success: true, categories: [{ id: label, label }] });

	it('keeps the newest actor result when an older call resolves last', async () => {
		const { loadSidebar, sidebarConfig } = await import('../stores/sidebar');
		const { get } = await import('svelte/store');
		const capital = deferred<string>();
		const quarter = deferred<string>();
		const first = loadSidebar({ get_sidebar: () => capital.promise });
		const second = loadSidebar({ get_sidebar: () => quarter.promise });
		quarter.resolve(reply('member'));
		await second;
		capital.resolve(reply('guest'));
		await first;
		expect(get(sidebarConfig)?.categories?.[0]?.id).toBe('member');
	});
});
