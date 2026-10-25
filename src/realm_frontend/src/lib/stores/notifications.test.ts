import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const { extensionSyncCall, extensionCall, manifest } = vi.hoisted(() => ({
	extensionSyncCall: vi.fn(),
	extensionCall: vi.fn(),
	manifest: { current: null as null | { query_functions: string[] } },
}));
vi.mock('$lib/canisters', () => ({
	backend: { extension_sync_call: extensionSyncCall, extension_call: extensionCall },
}));
vi.mock('../utils/extension-manifest', () => ({ peekExtensionManifest: () => manifest.current }));

import { loadNotifications, notifications, unreadCount } from './notifications';

const payload = (n: number) => ({
	success: true,
	response: JSON.stringify({
		notifications: Array.from({ length: n }, (_, i) => ({ id: `n${i}`, read: false })),
		unread_count: n,
	}),
});

describe('loadNotifications', () => {
	beforeEach(() => {
		extensionSyncCall.mockReset();
		extensionCall.mockReset();
		manifest.current = null;
	});

	it('shares one get_notifications call between concurrent loads', async () => {
		extensionSyncCall.mockResolvedValue(payload(2));
		await Promise.all([loadNotifications(), loadNotifications()]);
		expect(extensionSyncCall).toHaveBeenCalledTimes(1);
		expect(get(unreadCount)).toBe(2);
		expect(get(notifications)).toHaveLength(2);
	});

	it('reloads on the next navigation once the previous load settled', async () => {
		extensionSyncCall.mockResolvedValueOnce(payload(1)).mockResolvedValueOnce(payload(3));
		await loadNotifications();
		await loadNotifications();
		expect(extensionSyncCall).toHaveBeenCalledTimes(2);
		expect(get(unreadCount)).toBe(3);
	});

	it('uses the query endpoint once the manifest declares get_notifications read-only', async () => {
		manifest.current = { query_functions: ['get_notifications'] };
		extensionCall.mockResolvedValue(payload(4));
		await loadNotifications();
		expect(extensionCall).toHaveBeenCalledWith('notifications', 'get_notifications', '{}');
		expect(extensionSyncCall).not.toHaveBeenCalled();
		expect(get(unreadCount)).toBe(4);
	});
});
