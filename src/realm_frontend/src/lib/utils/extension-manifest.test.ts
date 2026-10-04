import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
	parseExtensionIdFromPath,
	resolveExtensionRepoUrl,
	type ExtensionManifestInfo,
} from './extension-manifest';

const { listRuntimeExtensions } = vi.hoisted(() => ({
	listRuntimeExtensions: vi.fn(),
}));

vi.mock('$lib/canisters', () => ({
	backend: {
		list_runtime_extensions: listRuntimeExtensions,
	},
}));

describe('fetchExtensionManifests', () => {
	beforeEach(async () => {
		vi.useFakeTimers();
		const { clearExtensionManifestCache } = await import('./extension-manifest');
		clearExtensionManifestCache();
		listRuntimeExtensions.mockReset();
	});

	afterEach(async () => {
		const { clearExtensionManifestCache } = await import('./extension-manifest');
		clearExtensionManifestCache();
		vi.useRealTimers();
	});

	it('retries after a transient failure instead of caching an empty result forever', async () => {
		listRuntimeExtensions
			.mockRejectedValueOnce(new Error('backend unavailable'))
			.mockResolvedValueOnce(
				JSON.stringify({
					all_manifests: {
						member_dashboard: { name: 'member_dashboard', runtime: 'sandboxed' },
					},
				}),
			);

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
		expect(listRuntimeExtensions).toHaveBeenCalledTimes(2);

		const manifest = await getExtensionManifest('member_dashboard');
		expect(manifest?.runtime).toBe('sandboxed');

		clearExtensionManifestCache();
		listRuntimeExtensions
			.mockRejectedValueOnce(new Error('fail 1'))
			.mockRejectedValueOnce(new Error('fail 2'))
			.mockRejectedValueOnce(new Error('fail 3'));

		const exhausted = fetchManifests();
		await vi.advanceTimersByTimeAsync(4000);
		await expect(exhausted).resolves.toEqual({});
		expect(listRuntimeExtensions).toHaveBeenCalledTimes(5);

		listRuntimeExtensions.mockResolvedValueOnce(
			JSON.stringify({
				all_manifests: {
					member_dashboard: { name: 'member_dashboard', runtime: 'sandboxed' },
				},
			}),
		);
		const recovered = fetchManifests();
		await expect(recovered).resolves.toMatchObject({
			member_dashboard: { runtime: 'sandboxed' },
		});
		expect(listRuntimeExtensions).toHaveBeenCalledTimes(6);
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
