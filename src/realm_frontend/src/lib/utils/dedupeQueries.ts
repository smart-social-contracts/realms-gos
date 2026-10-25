type Primitive = string | number | bigint | boolean | null | undefined;

function isPrimitive(v: unknown): v is Primitive {
	return v === null || (typeof v !== 'object' && typeof v !== 'function' && typeof v !== 'symbol');
}

/** Key for a call, or null when the args are not plain primitives (never deduped). */
export function concurrentCallKey(method: string, args: readonly unknown[]): string | null {
	if (!args.every(isPrimitive)) return null;
	return JSON.stringify([method, ...args.map((a) => (typeof a === 'bigint' ? `${a}n` : a === undefined ? '__undef__' : a))]);
}

/** Names of the query / composite_query methods of a candid service (Actor.interfaceOf(actor)). */
export function queryMethodNames(service: { _fields?: Array<[string, { annotations?: string[] }]> } | null | undefined): Set<string> {
	const names = new Set<string>();
	for (const [name, func] of service?._fields ?? []) {
		const ann = func?.annotations ?? [];
		if (ann.includes('query') || ann.includes('composite_query')) names.add(name);
	}
	return names;
}

/**
 * Wrap an actor so identical query calls issued while one is still in flight
 * share a single request. Boot fans out the same reads (status,
 * get_runtime_flags, get_my_user_status, ...) from many components; queries are
 * side-effect free, so sharing the pending result is safe. Updates and calls
 * with non-primitive args pass straight through. Callers must not mutate a
 * shared response object.
 */
export function withConcurrentQueryDedupe<T extends object>(actor: T, queryMethods: ReadonlySet<string>): T {
	if (!actor || queryMethods.size === 0) return actor;
	const inflight = new Map<string, Promise<unknown>>();
	const wrappers = new Map<string, (...args: unknown[]) => unknown>();

	return new Proxy(actor, {
		get(target, prop) {
			const value = Reflect.get(target, prop, target);
			if (typeof prop !== 'string' || typeof value !== 'function' || !queryMethods.has(prop)) {
				return value;
			}
			let wrapper = wrappers.get(prop);
			if (!wrapper) {
				wrapper = (...args: unknown[]) => {
					const key = concurrentCallKey(prop, args);
					if (key === null) return value.apply(target, args);
					const pending = inflight.get(key);
					if (pending) return pending;
					const p = Promise.resolve(value.apply(target, args)).finally(() => {
						if (inflight.get(key) === p) inflight.delete(key);
					});
					inflight.set(key, p);
					return p;
				};
				wrappers.set(prop, wrapper);
			}
			return wrapper;
		},
	});
}
