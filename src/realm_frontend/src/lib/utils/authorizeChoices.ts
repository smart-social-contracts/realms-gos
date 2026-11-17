/**
 * What a member may grant when they answer a power of attorney request: the request's own groups (or, from a client
 * that predates groups, its permissions), never more, and never longer. "Everything" is on offer only when the
 * request asks for it.
 */

export const EVERYTHING = '*';

export type Scope = { groups?: string[]; operations?: string[] };

/** A group as `delegable_operations` describes it: the realm's own words, the same in every client. */
export type Group = {
	id: string;
	label: string;
	summary: string;
	risk: string;
	longest: number;
	confirms: boolean;
};

export type Choice = {
	id: string;
	label: string;
	summary: string;
	risk: string;
	confirms: boolean;
	/** False when the member holds nothing in it, so cannot grant it. */
	held: boolean;
};

export type Decision =
	| { ok: true; scope: { groups: string[] } | { operations: string[] } }
	| { ok: false; error: 'none_chosen' | 'not_asked' | 'everything_not_asked' };

export const asksEverything = (scope: Scope) => !!scope.groups?.includes(EVERYTHING);

/** A group the member holds something in, as a choice to tick. */
export const choiceOf = (group: Group): Choice => ({
	id: group.id,
	label: group.label,
	summary: group.summary,
	risk: group.risk,
	confirms: group.confirms,
	held: true
});

/** What a grant covers, in words: its groups' labels ("Everything" for all), or the permission names of an old grant. */
export function scopeLabels(scope: Scope | undefined, vocabulary: Group[], everything: string): string[] {
	if (!scope?.groups) return scope?.operations ?? [];
	return scope.groups.map((id) => (id === EVERYTHING ? everything : (vocabulary.find((g) => g.id === id)?.label ?? id)));
}

/** The choices a request leaves the member: its groups, or every group the member holds when it asks for everything. */
export function choicesFor(scope: Scope, vocabulary: Group[], holds: (permission: string) => boolean): Choice[] {
	if (!scope.groups) {
		return (scope.operations ?? []).map((id) => ({
			id,
			label: id,
			summary: '',
			risk: '',
			confirms: false,
			held: holds(id)
		}));
	}
	const asked = asksEverything(scope) ? vocabulary.map((g) => g.id) : scope.groups;
	return asked.map((id) => {
		const group = vocabulary.find((g) => g.id === id);
		return group ? choiceOf(group) : { id, label: id, summary: '', risk: '', confirms: false, held: false };
	});
}

/** The scope to grant: what the member ticked, if it is within what was asked for and held. */
export function decide(scope: Scope, choices: Choice[], ticked: string[], everything: boolean): Decision {
	if (everything) {
		return asksEverything(scope)
			? { ok: true, scope: { groups: [EVERYTHING] } }
			: { ok: false, error: 'everything_not_asked' };
	}
	const grantable = new Set(choices.filter((c) => c.held).map((c) => c.id));
	if (ticked.some((id) => !grantable.has(id))) return { ok: false, error: 'not_asked' };
	if (!ticked.length) return { ok: false, error: 'none_chosen' };
	return { ok: true, scope: scope.groups ? { groups: ticked } : { operations: ticked } };
}

/** Why an expiry cannot be granted (an empty string when it can): it may only be made earlier than asked. */
export function expiryError(
	asked: { starts_at: number; expires_at: number },
	expiresAt: number,
	now: number
): 'too_late' | 'not_future' | '' {
	if (!expiresAt || expiresAt > asked.expires_at) return 'too_late';
	if (expiresAt <= Math.max(asked.starts_at, now)) return 'not_future';
	return '';
}
