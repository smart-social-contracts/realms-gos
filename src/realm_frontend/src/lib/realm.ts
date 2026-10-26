/**
 * The realm backend: every operation is called by name, ``read`` for queries and ``run`` for changes.
 *
 *     const me = await read('me');                       // { ok: true, user: …, … } or { ok: false, error }
 *     await run('proposal_vote', { proposal: 'P-7', choice: 'yes' });
 *
 * ``read('ops_list')`` lists every operation with its permission, flags and description.
 * ``quarter.read`` / ``quarter.run`` call the active quarter instead (see setActiveQuarter);
 * ``on(actor)`` calls the realm canister behind any other actor (e.g. a join target).
 */
// @ts-expect-error canisters.js has no type declarations
import { backend, quarterBackend } from '$lib/canisters';

export type Reply = { ok: true; [key: string]: any } | { ok: false; error: string };

/** A refusal from the realm, thrown by ``must``. */
export class RealmError extends Error {}

async function call(actor: any, method: 'read' | 'run', name: string, args: object): Promise<Reply> {
	return JSON.parse(await actor[method](name, JSON.stringify(args ?? {})));
}

export const read = (name: string, args: object = {}) => call(backend, 'read', name, args);
export const run = (name: string, args: object = {}) => call(backend, 'run', name, args);

export const on = (actor: any) => ({
	read: (name: string, args: object = {}) => call(actor, 'read', name, args),
	run: (name: string, args: object = {}) => call(actor, 'run', name, args)
});

export const quarter = on(quarterBackend);

/** The reply of a call that must succeed; throws ``RealmError`` with the realm's message otherwise. */
export async function must(reply: Reply | Promise<Reply>): Promise<{ [key: string]: any }> {
	const out = await reply;
	if (!out.ok) throw new RealmError(out.error);
	return out;
}
