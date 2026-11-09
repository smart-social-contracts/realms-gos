/**
 * Pending actions: a call a delegate prepared that asks the member each time, as the member reads and decides it.
 * The realm writes everything on a card except `note` and the values of the call's arguments (the delegate's own).
 * Deciding sends only what the card showed: its id and, to approve, its hash.
 */
import { must } from '$lib/realm';
import { resolveDelegationRealm } from '$lib/stores/delegation.js';

export type ActionStatus =
	| 'pending'
	| 'approved'
	| 'approving'
	| 'done'
	| 'failed'
	| 'declined'
	| 'withdrawn'
	| 'expired'
	| 'void';

export type SummaryLine = { label: string; value: string };

export type ActionCardData = {
	id: string;
	grantor: string;
	delegate: string;
	/** A member of the realm, as against an AI connection. */
	delegate_is_member: boolean;
	grant: string;
	method: string;
	args: Record<string, unknown>;
	title: string;
	summary: SummaryLine[];
	risk: string;
	note: string;
	status: ActionStatus;
	hash: string;
	filed_at: number;
	expires_at: number;
	decided_at: number | null;
	result: Record<string, unknown> | null;
};

/** The card of `id`, or null when there is none or it is not the caller's. */
export async function loadAction(id: string): Promise<ActionCardData | null> {
	const { action } = await must((await resolveDelegationRealm()).read('action_get', { action: id }));
	return action;
}

/** The caller's actions that wait for their decision, as cards. */
export async function loadWaiting(): Promise<ActionCardData[]> {
	const { read } = await resolveDelegationRealm();
	const waiting: ActionCardData[] = [];
	for (let from: number | null = 1; from; ) {
		const page = await must(read('action_list', { status: 'pending', from, count: 500 }));
		waiting.push(...page.actions);
		from = page.next;
	}
	return waiting;
}

/** Approve exactly the record shown: the realm refuses a hash that is not the card's, and takes nothing else. */
export async function approveAction(card: ActionCardData): Promise<ActionCardData> {
	const { run } = await resolveDelegationRealm();
	return (await must(run('action_approve', { action: card.id, hash: card.hash }))).action;
}

export async function declineAction(card: ActionCardData): Promise<ActionCardData> {
	const { run } = await resolveDelegationRealm();
	return (await must(run('action_decline', { action: card.id }))).action;
}

/**
 * Text the delegate wrote, with its control and bidirectional characters shown as escapes (`\u{202e}`): left as they
 * are, they could reorder or hide what the member reads. Line breaks stay.
 */
export const showUnseen = (text: string) =>
	text.replace(/[\p{C}\p{Zl}\p{Zp}]/gu, (ch) => (ch === '\n' ? ch : `\\u{${ch.codePointAt(0)!.toString(16)}}`));

/** What a grant is called by whoever gave it; the delegation stores (an untyped module) hold these and more. */
export type NamedGrants = { id: string; label: string }[];

/** The label the member gave the grant an action was prepared under: the delegate's name as the member knows it. */
export function grantLabel(card: ActionCardData, grants: NamedGrants): string {
	return grants.find((g) => g.id === card.grant)?.label ?? '';
}

// The realm's own lines have a label of several words that starts with a capital ("What changes"); an argument of
// the call appears under its own name, one lower-case word (core.pending.summary_of). A label that is not the
// realm's kind is never shown as the realm's words.
export const isRealmLine = (line: SummaryLine) => /^[A-Z]\S*\s/.test(line.label);

const NEXT_STEP = 'What happens next';

/** The lines the delegate's arguments make: shown apart, as the delegate's. */
export const argumentLines = (card: ActionCardData) => card.summary.filter((l) => !isRealmLine(l));

/** The realm's words about the call, less what happens next, which has a place of its own. */
export const realmLines = (card: ActionCardData) =>
	card.summary.filter((l) => isRealmLine(l) && l.label !== NEXT_STEP);

export const nextStep = (card: ActionCardData) => card.summary.find((l) => l.label === NEXT_STEP)?.value ?? '';

/** What the call made, as `[name, text]` (the reply less `ok`). Text only: nothing in it is ever markup. */
export function madeBy(card: ActionCardData): [string, string][] {
	return Object.entries(card.result ?? {})
		.filter(([name]) => name !== 'ok' && name !== 'error' && name !== 'kind')
		.map(([name, value]) => [name, typeof value === 'object' ? JSON.stringify(value) : String(value)]);
}

/** Why a call the member approved was refused; empty when the realm gave none (an approval that never finished). */
export const failure = (card: ActionCardData): string =>
	typeof card.result?.error === 'string' ? card.result.error : '';
