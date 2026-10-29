import { get } from 'svelte/store';
import { describe, expect, it, vi } from 'vitest';

const read = vi.fn();
vi.mock('$lib/realm', async () => {
	class RealmError extends Error {}
	return {
		RealmError,
		read: (...args: unknown[]) => read(...args),
		run: vi.fn(),
		must: async (reply: any) => {
			const out = await reply;
			if (!out.ok) throw new RealmError(out.error);
			return out;
		}
	};
});
vi.mock('$lib/auth.js', () => ({ settleAuthSession: vi.fn() }));
vi.mock('$lib/stores/auth.js', async () => ({ isAuthenticated: (await import('svelte/store')).writable(true) }));

import { loadNotifications, notifications, unreadCount } from './notifications';

describe('loadNotifications', () => {
	it('waits for the signed actor instead of logging its sign in first', async () => {
		vi.useFakeTimers();
		const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
		read.mockResolvedValueOnce({ ok: false, error: 'sign in first' }).mockResolvedValueOnce({
			ok: true,
			notifications: [{ id: 'N-1', title: 'Hi', body: 'Welcome', at: 1, read: false }],
			next: null
		});
		const loading = loadNotifications();
		await vi.runAllTimersAsync();
		await loading;
		expect(get(notifications).map((n) => n.id)).toEqual(['N-1']);
		expect(get(unreadCount)).toBe(1);
		expect(logged).not.toHaveBeenCalled();
		vi.useRealTimers();
	});
});
