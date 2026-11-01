// The realm's operations as a journey spec uses them: do one and expect it done, do one and expect the refusal,
// and read what the realm holds afterwards. What a step did is read back from the realm, not from a page's text.
import { expect } from '@playwright/test';
import type { Ed25519KeyIdentity } from '@dfinity/identity';
import { founderIdentity, readOp, runOp, type StandaloneRealm } from './realm';

export type Reply = Record<string, unknown>;

/** The realm's rows of `entity`, as an administrator reads them. */
export async function rows<T>(realm: StandaloneRealm, entity: string): Promise<T[]> {
	const out = await readOp(
		realm,
		'realm_export',
		{ entity, from: 1, count: 500 },
		founderIdentity(realm)
	);
	return out.rows as T[];
}

export async function row<T extends { id: string }>(
	realm: StandaloneRealm,
	entity: string,
	id: string
): Promise<T> {
	const found = (await rows<T>(realm, entity)).find((r) => r.id === id);
	expect(found, `${entity} ${id} is in the realm`).toBeDefined();
	return found!;
}

/** Calls `op` as `who` and expects the realm to have done it. */
export async function done<T = Reply>(
	realm: StandaloneRealm,
	op: string,
	args: Record<string, unknown>,
	who: Ed25519KeyIdentity
): Promise<T> {
	const reply: Reply = await runOp(realm, op, args, who);
	expect(reply.ok, `${op}: ${reply.error}`).toBe(true);
	return reply as T;
}

/** Calls `op` as `who` and expects the realm to have refused it with a message that says why. */
export async function refused(
	realm: StandaloneRealm,
	op: string,
	args: Record<string, unknown>,
	who: Ed25519KeyIdentity,
	why: RegExp
) {
	const reply: Reply = await runOp(realm, op, args, who);
	expect(reply.ok, `${op} was refused`).toBe(false);
	expect(reply.error).toMatch(why);
}

/** Runs `act` and expects the realm's rows of `entities` to be what they were: a refusal changes nothing. */
export async function leavesUnchanged(
	realm: StandaloneRealm,
	entities: string[],
	act: () => Promise<void>
) {
	const snapshot = () => Promise.all(entities.map((entity) => rows(realm, entity)));
	const before = await snapshot();
	await act();
	expect(await snapshot(), `${entities.join(', ')} are unchanged`).toEqual(before);
}
