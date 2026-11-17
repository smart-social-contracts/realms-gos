/** An event of the realm's audit log, as `delegation_activity` returns it. */
export type ActivityEvent = {
	id: string;
	op: string;
	origin: string;
	args: Record<string, unknown>;
	result: Record<string, unknown>;
	at: number;
};

export type ActivityRow = {
	act: string;
	outcome: 'done' | 'waiting' | 'approved' | 'collected';
	/** The record the act concerns, if the realm kept one a member can open. */
	reference: string;
	href: string;
};

const link = (prefix: string, id: unknown) => (typeof id === 'string' && id ? `${prefix}${id}` : '');

/** What a delegate's event says: the act, how it went, and where to see what it made. */
export function describeEvent(event: ActivityEvent): ActivityRow {
	const { pending, collected, proposal } = event.result;
	if (pending) return { act: event.op, outcome: 'waiting', reference: String(pending), href: link('/delegations/confirm/', pending) };
	if (collected) return { act: event.op, outcome: 'collected', reference: String(collected), href: '' };
	const made = proposal ?? event.args.proposal;
	return {
		act: event.op,
		outcome: event.origin === 'confirmed' ? 'approved' : 'done',
		reference: typeof made === 'string' ? made : '',
		href: link('/proposal/', made)
	};
}
