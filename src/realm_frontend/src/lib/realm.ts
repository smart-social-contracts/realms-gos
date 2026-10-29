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
import { Actor, pollForResponse, requestIdOf } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';
// @ts-expect-error canisters.js has no type declarations
import { backend, quarterBackend } from '$lib/canisters';

export type Reply = { ok: true; [key: string]: any } | { ok: false; error: string };

/** A refusal from the realm, thrown by ``must``. */
export class RealmError extends Error {}

const STILL_PROCESSING = 'Call was returned undefined';

/**
 * The reply of an update call the agent gave up on. A boundary node can answer a long call (an extension install
 * copying its bundle) with a certificate that still says "processing"; @dfinity/agent 3.4 only polls after a 202
 * and reports "Call was returned undefined" instead. Poll for the reply as it would.
 */
export async function stillProcessing(actor: any, error: any): Promise<string> {
	const details = error?.cause?.code?.callContext?.httpDetails?.requestDetails;
	if (!details || !String(error?.message ?? '').includes(STILL_PROCESSING)) throw error;
	const { reply } = await pollForResponse(Actor.agentOf(actor) as never, Actor.canisterIdOf(actor), requestIdOf(details));
	return IDL.decode([IDL.Text], reply)[0] as string;
}

async function call(actor: any, method: 'read' | 'run', name: string, args: object): Promise<Reply> {
	const out = await actor[method](name, JSON.stringify(args ?? {})).catch((e: unknown) =>
		method === 'run' ? stillProcessing(actor, e) : Promise.reject(e)
	);
	return JSON.parse(out);
}

export const read = (name: string, args: object = {}) => call(backend, 'read', name, args);
export const run = (name: string, args: object = {}) => call(backend, 'run', name, args);

export const on = (actor: any) => ({
	read: (name: string, args: object = {}) => call(actor, 'read', name, args),
	run: (name: string, args: object = {}) => call(actor, 'run', name, args)
});

// realm.ts and canisters.js import each other (through the stores): read quarterBackend only when called.
export const quarter = {
	read: (name: string, args: object = {}) => call(quarterBackend, 'read', name, args),
	run: (name: string, args: object = {}) => call(quarterBackend, 'run', name, args)
};

/** The reply of a call that must succeed; throws ``RealmError`` with the realm's message otherwise. */
export async function must(reply: Reply | Promise<Reply>): Promise<{ [key: string]: any }> {
	const out = await reply;
	if (!out.ok) throw new RealmError(out.error);
	return out;
}
