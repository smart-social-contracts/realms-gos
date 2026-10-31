import { describe, expect, it, vi } from 'vitest';
import { concurrentCallKey, queryMethodNames, withConcurrentQueryDedupe } from './dedupeQueries';

function deferred<T>() {
	let resolve!: (v: T) => void;
	let reject!: (e: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

describe('concurrentCallKey', () => {
	it('keys primitive args and distinguishes them', () => {
		expect(concurrentCallKey('status', [])).toBe(concurrentCallKey('status', []));
		expect(concurrentCallKey('get_sidebar', ['{"locale":"en"}'])).not.toBe(
			concurrentCallKey('get_sidebar', ['{"locale":"es"}']),
		);
		expect(concurrentCallKey('f', [1n])).not.toBe(concurrentCallKey('f', ['1']));
		expect(concurrentCallKey('f', [undefined])).not.toBe(concurrentCallKey('f', [null]));
	});

	it('refuses non-primitive args', () => {
		expect(concurrentCallKey('f', [{ a: 1 }])).toBeNull();
		expect(concurrentCallKey('f', [[1]])).toBeNull();
		expect(concurrentCallKey('f', [new Uint8Array(1)])).toBeNull();
	});
});

describe('queryMethodNames', () => {
	it('picks query and composite_query methods only', () => {
		const service = {
			_fields: [
				['status', { annotations: ['query'] }],
				['extension_call', { annotations: ['composite_query'] }],
				['extension_sync_call', { annotations: [] }],
			] as Array<[string, { annotations: string[] }]>,
		};
		expect([...queryMethodNames(service)].sort()).toEqual(['extension_call', 'status']);
		expect(queryMethodNames(undefined).size).toBe(0);
	});
});

describe('withConcurrentQueryDedupe', () => {
	function makeActor() {
		const pending: Array<ReturnType<typeof deferred<string>>> = [];
		const actor = {
			status: vi.fn(() => {
				const d = deferred<string>();
				pending.push(d);
				return d.promise;
			}),
			get_sidebar: vi.fn(async (arg: string) => `sidebar:${arg}`),
			join_realm: vi.fn(async () => 'joined'),
			marker: 42,
		};
		return { actor, pending };
	}

	it('shares one request between identical concurrent queries', async () => {
		const { actor, pending } = makeActor();
		const wrapped = withConcurrentQueryDedupe(actor, new Set(['status', 'get_sidebar']));
		const a = wrapped.status();
		const b = wrapped.status();
		expect(actor.status).toHaveBeenCalledTimes(1);
		pending[0].resolve('ok');
		await expect(Promise.all([a, b])).resolves.toEqual(['ok', 'ok']);
	});

	it('issues a new request once the previous one settled', async () => {
		const { actor, pending } = makeActor();
		const wrapped = withConcurrentQueryDedupe(actor, new Set(['status']));
		const a = wrapped.status();
		pending[0].resolve('first');
		await a;
		const b = wrapped.status();
		expect(actor.status).toHaveBeenCalledTimes(2);
		pending[1].resolve('second');
		await expect(b).resolves.toBe('second');
	});

	it('propagates rejections to every waiter and then forgets them', async () => {
		const { actor, pending } = makeActor();
		const wrapped = withConcurrentQueryDedupe(actor, new Set(['status']));
		const a = wrapped.status();
		const b = wrapped.status();
		pending[0].reject(new Error('down'));
		await expect(a).rejects.toThrow('down');
		await expect(b).rejects.toThrow('down');
		void wrapped.status();
		expect(actor.status).toHaveBeenCalledTimes(2);
	});

	it('does not merge calls with different args', async () => {
		const { actor } = makeActor();
		const wrapped = withConcurrentQueryDedupe(actor, new Set(['get_sidebar']));
		await Promise.all([wrapped.get_sidebar('en'), wrapped.get_sidebar('es')]);
		expect(actor.get_sidebar).toHaveBeenCalledTimes(2);
	});

	it('never dedupes update methods and passes other props through', async () => {
		const { actor } = makeActor();
		const wrapped = withConcurrentQueryDedupe(actor, new Set(['status']));
		await Promise.all([wrapped.join_realm(), wrapped.join_realm()]);
		expect(actor.join_realm).toHaveBeenCalledTimes(2);
		expect(wrapped.marker).toBe(42);
	});

	it('keeps symbol-keyed actor metadata reachable (Actor.agentOf)', () => {
		const meta = Symbol('ic-agent-metadata');
		const actor = { [meta]: { config: { agent: 'A' } }, status: async () => 'x' };
		const wrapped = withConcurrentQueryDedupe(actor, new Set(['status']));
		expect((wrapped as typeof actor)[meta].config.agent).toBe('A');
	});
});
