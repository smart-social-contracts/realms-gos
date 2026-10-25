/**
 * Read-only extension functions declared in a manifest's `query_functions`.
 *
 * `extension_sync_call` is an update call: it goes through consensus (~1-2 s on
 * mainnet) even for pure reads. Functions an extension declares side-effect
 * free are sent to the `extension_call` query instead, which runs the same
 * per-function access gate. Any failure falls back to the update call, so a
 * wrong declaration or a query hitting the instruction limit only costs an
 * extra round trip.
 */

export interface ExtensionCallActor {
	extension_sync_call: (ext: string, fn: string, args: string) => Promise<unknown>;
	extension_call?: (ext: string, fn: string, args: string) => Promise<unknown>;
}

export function extensionQueryFunctions(
	manifest: { query_functions?: unknown } | null | undefined,
): ReadonlySet<string> {
	const list = manifest?.query_functions;
	return new Set(Array.isArray(list) ? list.filter((f): f is string => typeof f === 'string') : []);
}

function isSuccess(raw: unknown): boolean {
	const res = typeof raw === 'string' ? safeParse(raw) : raw;
	return Boolean(res) && (res as { success?: unknown }).success !== false;
}

function safeParse(raw: string): unknown {
	try {
		return JSON.parse(raw);
	} catch {
		return null;
	}
}

export async function callExtensionFunction(
	actor: ExtensionCallActor,
	extensionId: string,
	fn: string,
	argsJson: string,
	queryFunctions: ReadonlySet<string>,
): Promise<unknown> {
	if (queryFunctions.has(fn) && typeof actor.extension_call === 'function') {
		try {
			const raw = await actor.extension_call(extensionId, fn, argsJson);
			if (isSuccess(raw)) return raw;
		} catch {
			// fall through to the update call
		}
	}
	return actor.extension_sync_call(extensionId, fn, argsJson);
}
