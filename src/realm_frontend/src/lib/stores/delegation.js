import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';
import { backend, quarterBackend, setActiveQuarter, whenBackendSignsAs } from '$lib/canisters.js';
import { read, run, quarter } from '$lib/realm';
import { principal } from '$lib/stores/auth';
import { activeQuarterId } from '$lib/stores/quarters';
import { choicesFor } from '$lib/utils/authorizeChoices';

const STORAGE_KEY = 'realm_acting_on_behalf_of';

function readStored() {
	if (!browser) return null;
	try {
		return sessionStorage.getItem(STORAGE_KEY) || null;
	} catch {
		return null;
	}
}

/** Grantor principal when acting on behalf of another user; null = self. */
export const actingOnBehalfOf = writable(readStored());

/**
 * @typedef {{groups?: string[], operations?: string[]}} Scope
 * @typedef {{id: string, grantor: string, delegate: string, label: string, description: string, scope: Scope,
 *   status: string, starts_at: number, expires_at: number}} Grant
 * @typedef {{id: string, grantor: string, delegate: string, delegate_is_member: boolean, label: string,
 *   description: string, scope: Scope, starts_at: number, expires_at: number, request_expires_at: number}} GrantRequest
 */

/** Cached delegation lists from delegation_list. @type {import('svelte/store').Writable<Grant[]>} */
export const delegationsAsDelegate = writable([]);
/** @type {import('svelte/store').Writable<Grant[]>} */
export const delegationsAsGrantor = writable([]);
/** @type {import('svelte/store').Writable<Grant[]>} */
export const pendingDelegations = writable([]);
/** Open requests to act for the caller, which they answer on the Authorize page. @type {import('svelte/store').Writable<GrantRequest[]>} */
export const requestsToMe = writable([]);

actingOnBehalfOf.subscribe((value) => {
	if (!browser) return;
	try {
		if (value) sessionStorage.setItem(STORAGE_KEY, value);
		else sessionStorage.removeItem(STORAGE_KEY);
	} catch {
		/* ignore */
	}
});

export function clearActingContext() {
	actingOnBehalfOf.set(null);
}

export function setActingOnBehalfOf(grantorPrincipal) {
	const v = (grantorPrincipal || '').trim();
	actingOnBehalfOf.set(v || null);
}

export function withDelegationArgs(args = {}) {
	const grantor = get(actingOnBehalfOf);
	if (grantor) {
		return { ...args, on_behalf_of: grantor };
	}
	return args;
}

export function withDelegationJson(args = {}) {
	return JSON.stringify(withDelegationArgs(args));
}

/** PoA lives on the member's assigned quarter backend when one is active. */
export async function resolveDelegationBackend() {
	// The auth store can flip to signed-in before the actor carries the identity
	// (portal sessions); an anonymous call would show no permissions or grants.
	await whenBackendSignsAs(get(principal));
	const quarterId = get(activeQuarterId);
	if (quarterId) {
		await setActiveQuarter(quarterId);
		return quarterBackend;
	}
	return backend;
}

function clientFor(actor) {
	return actor === quarterBackend ? quarter : { read, run };
}

/** ``{read, run}`` on the canister resolveDelegationBackend picks. */
export async function resolveDelegationRealm() {
	return clientFor(await resolveDelegationBackend());
}

/** A realm reply in the ``{success, data, error}`` shape these functions return. */
function legacy(reply) {
	const { ok, error, ...data } = reply;
	return ok ? { success: true, data } : { success: false, error };
}

function holds(me, permission) {
	const held = me.ok ? me.permissions : [];
	return held.includes('all') || held.includes(permission);
}

export async function loadDelegations(backendActor) {
	const realm = backendActor ? clientFor(backendActor) : await resolveDelegationRealm();
	try {
		const asGrantor = [];
		const asDelegate = [];
		let fromGrantor = 1;
		let fromDelegate = 1;
		let requests = [];
		while (fromGrantor || fromDelegate) {
			// A side already read to its end starts over at row 1 here: its rows are dropped.
			const page = await realm.read('delegation_list', {
				from_grantor: fromGrantor,
				from_delegate: fromDelegate,
				count: 100
			});
			if (!page.ok) return;
			requests = page.requests;
			if (fromGrantor) asGrantor.push(...page.as_grantor);
			if (fromDelegate) asDelegate.push(...page.as_delegate);
			fromGrantor = fromGrantor && page.next.grantor;
			fromDelegate = fromDelegate && page.next.delegate;
		}
		delegationsAsDelegate.set(asDelegate);
		delegationsAsGrantor.set(asGrantor);
		pendingDelegations.set(asDelegate.filter((d) => d.status === 'pending'));
		requestsToMe.set(requests.filter((r) => r.grantor === get(principal)));
	} catch (e) {
		console.warn('Failed to load delegations:', e);
	}
}

/** The groups the caller may grant, in the realm's own words: [{id, label, summary, risk, longest, confirms}]. */
export async function loadDelegationGroups() {
	const realm = await resolveDelegationRealm();
	const reply = await realm.read('delegable_operations');
	if (!reply.ok) throw new Error(reply.error);
	return reply.groups || [];
}

/** Grant power of attorney from the caller (who is always the grantor) for ``groups``. Times are epoch seconds. */
export async function grantDelegation({ delegate, groups, label, description, startsAt, expiresAt }) {
	const realm = await resolveDelegationRealm();
	return legacy(
		await realm.run('delegation_grant', {
			delegate,
			groups,
			label,
			description,
			starts_at: startsAt,
			expires_at: expiresAt
		})
	);
}

export async function acceptDelegation(delegationId) {
	const realm = await resolveDelegationRealm();
	return legacy(await realm.run('delegation_accept', { delegation: delegationId }));
}

export async function revokeDelegation(delegationId) {
	const realm = await resolveDelegationRealm();
	return legacy(await realm.run('delegation_revoke', { delegation: delegationId }));
}

/** End every grant the caller gave, a page of them at a time. Resolves to how many it ended. */
export async function revokeAllDelegations() {
	const realm = await resolveDelegationRealm();
	let revoked = 0;
	for (let from = 1; from; ) {
		const reply = await realm.run('delegation_revoke_all', { from, count: 500 });
		if (!reply.ok) return { success: false, error: reply.error };
		revoked += reply.revoked;
		from = reply.next;
	}
	return { success: true, data: { revoked } };
}

/** What the delegate of ``delegationId`` did, newest first, from event id ``from`` (the latest when omitted). */
export async function loadDelegationActivity(delegationId, from) {
	const realm = await resolveDelegationRealm();
	return realm.read('delegation_activity', { delegation: delegationId, count: 50, from });
}

/** The nickname of the member ``principal``; empty when they have none or are not a member. */
export async function memberNickname(principal) {
	const realm = await resolveDelegationRealm();
	const reply = await realm.read('user_get', { user: principal });
	return reply.ok ? reply.user.nickname || '' : '';
}

/** An open power of attorney request addressed to (or filed by) the caller, with the choices it leaves them. */
export async function loadDelegationRequest(requestId) {
	const realm = await resolveDelegationRealm();
	const [list, me, ops] = await Promise.all([
		realm.read('delegation_list', { count: 1 }),
		realm.read('me'),
		realm.read('delegable_operations')
	]);
	if (!list.ok) return legacy(list);
	const request = list.requests.find((r) => r.id === requestId);
	if (!request) return { success: false, error: 'This request is closed or was not made to you' };
	const choices = choicesFor(request.scope, ops.ok ? ops.groups || [] : [], (permission) => holds(me, permission));
	return { success: true, data: { ...request, choices } };
}

/** Approve a request with a scope within what it asked (``{groups}`` or ``{operations}``) and an expiry no later. */
export async function approveDelegationRequest(requestId, { scope, expiresAt } = {}) {
	const realm = await resolveDelegationRealm();
	return legacy(
		await realm.run('delegation_request_decide', {
			request: requestId,
			approve: true,
			...scope,
			expires_at: expiresAt
		})
	);
}

export async function declineDelegationRequest(requestId) {
	const realm = await resolveDelegationRealm();
	return legacy(await realm.run('delegation_request_decide', { request: requestId, approve: false }));
}

/** Active delegations the caller may act under (active, started, not expired). */
export function activeDelegationsAsDelegate(list) {
	const now = Math.floor(Date.now() / 1000);
	return (list || []).filter(
		(d) =>
			d.status === 'active' &&
			(!d.starts_at || d.starts_at <= now) &&
			(!d.expires_at || d.expires_at > now)
	);
}
