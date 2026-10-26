import { describe, expect, it, vi } from 'vitest';

vi.mock('$lib/realm', () => ({ quarter: {}, must: vi.fn() }));

import { sidebarCacheKey } from '../stores/sidebar';

describe('sidebarCacheKey', () => {
	it('scopes the cache key by locale', () => {
		expect(sidebarCacheKey('en')).toBe('sidebar_cache:en');
		expect(sidebarCacheKey('es')).toBe('sidebar_cache:es');
		expect(sidebarCacheKey('ca-valencia')).toBe('sidebar_cache:ca-valencia');
	});
});
