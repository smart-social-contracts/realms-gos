import { afterEach, describe, expect, it, vi } from 'vitest';
import { getFileRegistryCanisterIds } from './file-registry-client';

// The module imports the extension loader only to build URLs; this unit does not use it.
vi.mock('$lib/extension-loader', () => ({ fileRegistryBaseUrlFor: vi.fn() }));

describe('getFileRegistryCanisterIds', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		delete (globalThis as { __CANISTER_IDS?: unknown }).__CANISTER_IDS;
	});

	it('takes the build-time id before the realm canister_ids.js', () => {
		vi.stubEnv('CANISTER_ID_FILE_REGISTRY', 'built-in-id');
		(globalThis as { __CANISTER_IDS?: unknown }).__CANISTER_IDS = { file_registry: 'runtime-id' };
		expect(getFileRegistryCanisterIds()).toEqual(['built-in-id']);
	});

	it('falls back to the file_registry of the realm canister_ids.js', () => {
		vi.stubEnv('CANISTER_ID_FILE_REGISTRY', '');
		(globalThis as { __CANISTER_IDS?: unknown }).__CANISTER_IDS = { file_registry: 'runtime-id' };
		expect(getFileRegistryCanisterIds()).toEqual(['runtime-id']);
	});

	it('is empty when neither names a registry', () => {
		vi.stubEnv('CANISTER_ID_FILE_REGISTRY', '');
		(globalThis as { __CANISTER_IDS?: unknown }).__CANISTER_IDS = {};
		expect(getFileRegistryCanisterIds()).toEqual([]);
	});
});
