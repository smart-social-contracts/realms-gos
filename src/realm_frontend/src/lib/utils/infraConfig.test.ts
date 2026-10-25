import { beforeEach, describe, expect, it, vi } from 'vitest';

const { status } = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock('$lib/canisters', () => ({ backend: { status } }));

const statusWith = (canisters: Array<{ canister_id: string; canister_type: string }>) => ({
	success: true,
	data: { status: { canisters } },
});

describe('parseInfraConfig', () => {
	it('extracts the file registry and marketplace ids', async () => {
		const { parseInfraConfig } = await import('./infraConfig');
		expect(
			parseInfraConfig(
				statusWith([
					{ canister_id: 'fr-1', canister_type: 'file_registry' },
					{ canister_id: 'mp-1', canister_type: 'marketplace' },
				]),
			),
		).toEqual({ fileRegistryCanisterId: 'fr-1', marketplaceCanisterId: 'mp-1' });
		expect(parseInfraConfig(null)).toEqual({ fileRegistryCanisterId: '', marketplaceCanisterId: '' });
	});
});

describe('getInfraConfig', () => {
	beforeEach(() => {
		vi.resetModules();
		status.mockReset();
	});

	it('queries status() once per session', async () => {
		status.mockResolvedValue(statusWith([{ canister_id: 'fr-1', canister_type: 'file_registry' }]));
		const { getInfraConfig } = await import('./infraConfig');
		await Promise.all([getInfraConfig(), getInfraConfig()]);
		await expect(getInfraConfig()).resolves.toMatchObject({ fileRegistryCanisterId: 'fr-1' });
		expect(status).toHaveBeenCalledTimes(1);
	});

	it('does not cache failures or empty results', async () => {
		status
			.mockRejectedValueOnce(new Error('timeout'))
			.mockResolvedValueOnce(statusWith([]))
			.mockResolvedValue(statusWith([{ canister_id: 'mp-1', canister_type: 'marketplace' }]));
		const { getInfraConfig } = await import('./infraConfig');
		await expect(getInfraConfig()).resolves.toEqual({});
		await expect(getInfraConfig()).resolves.toEqual({
			fileRegistryCanisterId: '',
			marketplaceCanisterId: '',
		});
		await expect(getInfraConfig()).resolves.toMatchObject({ marketplaceCanisterId: 'mp-1' });
		await getInfraConfig();
		expect(status).toHaveBeenCalledTimes(3);
	});
});
