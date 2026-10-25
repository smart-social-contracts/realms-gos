import { describe, expect, it, vi } from 'vitest';
import { singleFlight } from './singleFlight';

function deferred<T>() {
	let resolve!: (v: T) => void;
	let reject!: (e: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

describe('singleFlight', () => {
	it('shares one in-flight call between concurrent callers', async () => {
		const d = deferred<number>();
		const fn = vi.fn(() => d.promise);
		const call = singleFlight(fn);
		const a = call();
		const b = call();
		expect(fn).toHaveBeenCalledTimes(1);
		d.resolve(7);
		await expect(Promise.all([a, b])).resolves.toEqual([7, 7]);
	});

	it('reuses a settled result within ttl, then refetches', async () => {
		let t = 0;
		const fn = vi.fn(async () => t);
		const call = singleFlight(fn, { ttlMs: 100, now: () => t });
		await call();
		t = 50;
		await expect(call()).resolves.toBe(0);
		expect(fn).toHaveBeenCalledTimes(1);
		t = 150;
		await expect(call()).resolves.toBe(150);
		expect(fn).toHaveBeenCalledTimes(2);
	});

	it('force bypasses both in-flight sharing and the ttl', async () => {
		const fn = vi.fn(async () => 1);
		const call = singleFlight(fn, { ttlMs: 10_000 });
		await call();
		await call({ force: true });
		expect(fn).toHaveBeenCalledTimes(2);
	});

	it('does not cache rejections', async () => {
		const fn = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue(2);
		const call = singleFlight(fn, { ttlMs: 10_000 });
		await expect(call()).rejects.toThrow('boom');
		await expect(call()).resolves.toBe(2);
		expect(fn).toHaveBeenCalledTimes(2);
	});

	it('does not reuse results rejected by cacheIf', async () => {
		const fn = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
		const call = singleFlight(fn, { ttlMs: 10_000, cacheIf: (ok) => ok });
		await expect(call()).resolves.toBe(false);
		await expect(call()).resolves.toBe(true);
		await expect(call()).resolves.toBe(true);
		expect(fn).toHaveBeenCalledTimes(2);
	});

	it('invalidate drops the cached result', async () => {
		const fn = vi.fn(async () => 1);
		const call = singleFlight(fn, { ttlMs: 10_000 });
		await call();
		call.invalidate();
		await call();
		expect(fn).toHaveBeenCalledTimes(2);
	});
});
