import { writable } from 'svelte/store';

/** An act a human delegate prepared for a member, which now waits for the member's approval. */
export type SentForApproval = { action: string; href: string; grantor: string };

/** The latest act the signed-in member prepared for someone else, for the one notice that says so. */
export const sentForApproval = writable<SentForApproval | null>(null);

/**
 * Called with every reply of `run`: a call that asks the member each time is not done but filed, and the realm
 * replies with the action and where its member decides. One place tells the delegate, whichever page they were on.
 */
export function noteIfSent(reply: any, args: { on_behalf_of?: string }) {
	if (reply?.ok && typeof reply.pending === 'string' && typeof reply.approve_link === 'string') {
		sentForApproval.set({ action: reply.pending, href: reply.approve_link, grantor: args.on_behalf_of ?? '' });
	}
}
