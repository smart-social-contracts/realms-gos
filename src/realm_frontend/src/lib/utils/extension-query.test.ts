import { describe, expect, it, vi } from 'vitest';
import { callExtensionFunction, extensionQueryFunctions } from './extension-query';

const ok = (body: unknown) => ({ success: true, response: JSON.stringify(body) });

function actor(overrides: Partial<Record<'extension_call' | 'extension_sync_call', any>> = {}) {
	return {
		extension_call: vi.fn(async () => ok({ via: 'query' })),
		extension_sync_call: vi.fn(async () => ok({ via: 'update' })),
		...overrides,
	};
}

describe('extensionQueryFunctions', () => {
	it('reads string entries of query_functions', () => {
		expect([...extensionQueryFunctions({ query_functions: ['a', 1, 'b'] })]).toEqual(['a', 'b']);
		expect(extensionQueryFunctions({ query_functions: 'a' }).size).toBe(0);
		expect(extensionQueryFunctions(null).size).toBe(0);
	});
});

describe('callExtensionFunction', () => {
	const reads = new Set(['get_proposals']);

	it('sends declared read-only functions to the query endpoint', async () => {
		const a = actor();
		await expect(callExtensionFunction(a, 'voting', 'get_proposals', '{}', reads)).resolves.toEqual(
			ok({ via: 'query' }),
		);
		expect(a.extension_call).toHaveBeenCalledWith('voting', 'get_proposals', '{}');
		expect(a.extension_sync_call).not.toHaveBeenCalled();
	});

	it('keeps undeclared functions on the update call', async () => {
		const a = actor();
		await callExtensionFunction(a, 'voting', 'cast_vote', '{"v":1}', reads);
		expect(a.extension_call).not.toHaveBeenCalled();
		expect(a.extension_sync_call).toHaveBeenCalledWith('voting', 'cast_vote', '{"v":1}');
	});

	it('falls back to the update call when the query throws', async () => {
		const a = actor({ extension_call: vi.fn().mockRejectedValue(new Error('instruction limit')) });
		await expect(callExtensionFunction(a, 'voting', 'get_proposals', '{}', reads)).resolves.toEqual(
			ok({ via: 'update' }),
		);
	});

	it('falls back to the update call when the query reports failure', async () => {
		const a = actor({ extension_call: vi.fn(async () => ({ success: false, response: 'nope' })) });
		await callExtensionFunction(a, 'voting', 'get_proposals', '{}', reads);
		expect(a.extension_sync_call).toHaveBeenCalledTimes(1);
	});

	it('uses the update call for backends without extension_call (marketplace adapter)', async () => {
		const a = { extension_sync_call: vi.fn(async () => ok({})) };
		await callExtensionFunction(a, 'market_place', 'get_proposals', '{}', reads);
		expect(a.extension_sync_call).toHaveBeenCalledTimes(1);
	});
});
