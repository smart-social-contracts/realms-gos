export interface SingleFlightOptions<T> {
	/** Reuse a successful result for this long after it settles (0 = only share in-flight calls). */
	ttlMs?: number;
	/** Results rejected by this predicate are not reused once settled. */
	cacheIf?: (value: T) => boolean;
	now?: () => number;
}

export interface SingleFlight<T> {
	(opts?: { force?: boolean }): Promise<T>;
	invalidate(): void;
}

/**
 * Collapse concurrent (and, with ttlMs, recent) invocations of `fn` into one.
 * Rejections are never cached; `force` always starts a fresh call.
 */
export function singleFlight<T>(
	fn: () => Promise<T>,
	{ ttlMs = 0, cacheIf = () => true, now = Date.now }: SingleFlightOptions<T> = {},
): SingleFlight<T> {
	let inflight: Promise<T> | null = null;
	let settled: { value: T; at: number } | null = null;

	const call = ((opts?: { force?: boolean }) => {
		if (!opts?.force) {
			if (inflight) return inflight;
			if (settled && now() - settled.at < ttlMs) return Promise.resolve(settled.value);
		}
		const p: Promise<T> = fn().then(
			(value) => {
				if (inflight === p) {
					settled = cacheIf(value) ? { value, at: now() } : null;
					inflight = null;
				}
				return value;
			},
			(err) => {
				if (inflight === p) inflight = null;
				throw err;
			},
		);
		inflight = p;
		return p;
	}) as SingleFlight<T>;

	call.invalidate = () => {
		settled = null;
	};
	return call;
}
