import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
	parseExtensionIdFromPath,
	resolveExtensionRepoUrl,
	type ExtensionManifestInfo,
} from './extension-manifest';

const { realmRead, publishedManifest } = vi.hoisted(() => ({
	realmRead: vi.fn(),
	publishedManifest: vi.fn(),
}));

vi.mock('$lib/realm', () => ({
	read: realmRead,
	must: async (reply: Promise<Record<string, unknown>>) => {
		const out = await reply;
		if (!out.ok) throw new Error(String(out.error));
		return out;
	},
}));

vi.mock('$lib/file-registry-client', () => ({
	getExtensionManifest: publishedManifest,
	getFileRegistryCanisterIds: () => ['registry'],
}));

const DASHBOARD_PAGE = {
	ok: true,
	extensions: [
		{ id: 'member_dashboard', version: '1.0.0', sidebar: {}, access: { profiles: ['member'] } },
	],
	next: null,
};

describe('fetchExtensionManifests', () => {
	beforeEach(async () => {
		vi.useFakeTimers();
		const { clearExtensionManifestCache } = await import('./extension-manifest');
		clearExtensionManifestCache();
		realmRead.mockReset();
		publishedManifest.mockReset();
		publishedManifest.mockResolvedValue({ name: 'Member dashboard', runtime: 'sandboxed' });
	});

	afterEach(async () => {
		const { clearExtensionManifestCache } = await import('./extension-manifest');
		clearExtensionManifestCache();
		vi.useRealTimers();
	});

	it('retries after a transient failure instead of caching an empty result forever', async () => {
		realmRead
			.mockRejectedValueOnce(new Error('backend unavailable'))
			.mockResolvedValueOnce(DASHBOARD_PAGE);

		const {
			fetchExtensionManifests: fetchManifests,
			getExtensionManifest,
			clearExtensionManifestCache,
		} = await import('./extension-manifest');

		const first = fetchManifests();
		await vi.advanceTimersByTimeAsync(1000);
		await expect(first).resolves.toMatchObject({
			member_dashboard: { runtime: 'sandboxed' },
		});
		expect(realmRead).toHaveBeenCalledTimes(2);

		const manifest = await getExtensionManifest('member_dashboard');
		expect(manifest).toMatchObject({
			name: 'Member dashboard',
			runtime: 'sandboxed',
			version: '1.0.0',
			profiles: ['member'],
		});
		expect(publishedManifest).toHaveBeenCalledWith('registry', 'member_dashboard', '1.0.0');

		clearExtensionManifestCache();
		realmRead
			.mockRejectedValueOnce(new Error('fail 1'))
			.mockRejectedValueOnce(new Error('fail 2'))
			.mockRejectedValueOnce(new Error('fail 3'));

		const exhausted = fetchManifests();
		await vi.advanceTimersByTimeAsync(4000);
		await expect(exhausted).resolves.toEqual({});
		expect(realmRead).toHaveBeenCalledTimes(5);

		realmRead.mockResolvedValueOnce(DASHBOARD_PAGE);
		const recovered = fetchManifests();
		await expect(recovered).resolves.toMatchObject({
			member_dashboard: { runtime: 'sandboxed' },
		});
		expect(realmRead).toHaveBeenCalledTimes(6);
	});
});

describe('getInstalledExtensionVersion', () => {
	const extensionPage = (version: string, extra: Record<string, unknown>[] = []) => ({
		ok: true,
		extensions: [{ id: 'voting', version, sidebar: {}, access: {} }, ...extra],
		next: null,
	});

	beforeEach(async () => {
		const { clearExtensionManifestCache } = await import('./extension-manifest');
		clearExtensionManifestCache();
		realmRead.mockReset();
		publishedManifest.mockReset();
		publishedManifest.mockResolvedValue(null);
	});

	it('reuses the cached manifests instead of querying per navigation', async () => {
		realmRead.mockResolvedValue(extensionPage('1.4.2'));
		const { getInstalledExtensionVersion, getExtensionManifest } = await import(
			'./extension-manifest'
		);
		await getExtensionManifest('voting');
		await expect(getInstalledExtensionVersion('voting')).resolves.toBe('1.4.2');
		await expect(getInstalledExtensionVersion('voting')).resolves.toBe('1.4.2');
		expect(realmRead).toHaveBeenCalledTimes(1);
	});

	it('refetches once when the extension is missing from the cache', async () => {
		realmRead
			.mockResolvedValueOnce(extensionPage('1.4.2'))
			.mockResolvedValueOnce(
				extensionPage('1.4.2', [{ id: 'vault', version: '0.3.0', sidebar: {}, access: {} }]),
			);
		const { getInstalledExtensionVersion } = await import('./extension-manifest');
		await getInstalledExtensionVersion('voting');
		await expect(getInstalledExtensionVersion('vault')).resolves.toBe('0.3.0');
		expect(realmRead).toHaveBeenCalledTimes(2);
	});

	it('fresh bypasses the cache (upgrade during the session)', async () => {
		realmRead
			.mockResolvedValueOnce(extensionPage('1.4.2'))
			.mockResolvedValueOnce(extensionPage('1.5.0'));
		const { getInstalledExtensionVersion } = await import('./extension-manifest');
		await expect(getInstalledExtensionVersion('voting')).resolves.toBe('1.4.2');
		await expect(getInstalledExtensionVersion('voting', { fresh: true })).resolves.toBe('1.5.0');
		await expect(getInstalledExtensionVersion('voting')).resolves.toBe('1.5.0');
		expect(realmRead).toHaveBeenCalledTimes(2);
	});
});

describe('parseExtensionIdFromPath', () => {
	it('extracts extension id from extension routes', () => {
		expect(parseExtensionIdFromPath('/extensions/voting')).toBe('voting');
		expect(parseExtensionIdFromPath('/extensions/voting/settings')).toBe('voting');
		expect(parseExtensionIdFromPath('/r/agorastaging/extensions/justice_litigation')).toBe(
			'justice_litigation',
		);
	});

	it('returns null for non-extension routes', () => {
		expect(parseExtensionIdFromPath('/settings')).toBeNull();
		expect(parseExtensionIdFromPath('/')).toBeNull();
	});
});

describe('resolveExtensionRepoUrl', () => {
	it('prefers doc_url over repository', () => {
		const manifest: ExtensionManifestInfo = {
			name: 'voting',
			doc_url: 'https://github.com/example/doc',
			repository: 'https://github.com/example/repo',
		};
		expect(resolveExtensionRepoUrl(manifest, 'voting')).toBe('https://github.com/example/doc');
	});

	it('rewrites legacy realms repo paths to realms-extensions', () => {
		const manifest: ExtensionManifestInfo = {
			name: 'voting',
			doc_url: 'https://github.com/smart-social-contracts/realms-gos/tree/main/extensions/voting',
		};
		expect(resolveExtensionRepoUrl(manifest, 'voting')).toBe(
			'https://github.com/smart-social-contracts/realms-extensions/tree/main/extensions/voting',
		);
	});

	it('rewrites legacy bare extensions repo paths', () => {
		const manifest: ExtensionManifestInfo = {
			name: 'market_place',
			doc_url: 'https://github.com/smart-social-contracts/extensions/market_place',
		};
		expect(resolveExtensionRepoUrl(manifest, 'market_place')).toBe(
			'https://github.com/smart-social-contracts/realms-extensions/tree/main/extensions/market_place',
		);
	});

	it('falls back to repository when doc_url is missing', () => {
		const manifest: ExtensionManifestInfo = {
			name: 'vault',
			repository: 'https://github.com/example/vault',
		};
		expect(resolveExtensionRepoUrl(manifest, 'vault')).toBe('https://github.com/example/vault');
	});

	it('falls back to realms-extensions when no link is in manifest', () => {
		expect(resolveExtensionRepoUrl({ name: 'metrics', doc_url: null }, 'metrics')).toBe(
			'https://github.com/smart-social-contracts/realms-extensions/tree/main/extensions/metrics',
		);
		expect(resolveExtensionRepoUrl(null, 'voting')).toBe(
			'https://github.com/smart-social-contracts/realms-extensions/tree/main/extensions/voting',
		);
	});
});
