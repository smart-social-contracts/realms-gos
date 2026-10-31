import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';
import { backend, quarterBackend, setActiveQuarter, whenBackendSignsAs } from '$lib/canisters.js';
import { read, run, quarter } from '$lib/realm';
import { principal } from '$lib/stores/auth';
import { activeQuarterId } from '$lib/stores/quarters';

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

/** Cached delegation lists from delegation_list. */
export const delegationsAsDelegate = writable([]);
export const delegationsAsGrantor = writable([]);
export const pendingDelegations = writable([]);

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
		while (fromGrantor || fromDelegate) {
			// A side already read to its end starts over at row 1 here: its rows are dropped.
			const page = await realm.read('delegation_list', {
				from_grantor: fromGrantor,
				from_delegate: fromDelegate,
				count: 100
			});
			if (!page.ok) return;
			if (fromGrantor) asGrantor.push(...page.as_grantor);
			if (fromDelegate) asDelegate.push(...page.as_delegate);
			fromGrantor = fromGrantor && page.next.grantor;
			fromDelegate = fromDelegate && page.next.delegate;
		}
		delegationsAsDelegate.set(asDelegate);
		delegationsAsGrantor.set(asGrantor);
		pendingDelegations.set(asDelegate.filter((d) => d.status === 'pending'));
	} catch (e) {
		console.warn('Failed to load delegations:', e);
	}
}

/** Permissions the caller holds here and may grant: [{operation, label, description}], ``operation`` being the permission. */
export async function loadDelegableOperations() {
	const realm = await resolveDelegationRealm();
	const [ops, me] = await Promise.all([realm.read('delegable_operations'), realm.read('me')]);
	if (!ops.ok) return [];
	const byPermission = new Map();
	for (const { op, permission } of ops.operations) {
		if (holds(me, permission)) byPermission.set(permission, [...(byPermission.get(permission) || []), op]);
	}
	return [...byPermission].map(([operation, names]) => ({
		operation,
		label: operation,
		description: names.join(', ')
	}));
}

/** Grant power of attorney from the caller (who is always the grantor). Times are epoch seconds. */
export async function grantDelegation({ delegate, operations, label, description, startsAt, expiresAt }) {
	const realm = await resolveDelegationRealm();
	return legacy(
		await realm.run('delegation_grant', {
			delegate,
			operations,
			label,
			description,
			starts_at: startsAt,
			expires_at: expiresAt
		})
	);
}

/** An open power of attorney request addressed to (or filed by) the caller. */
export async function loadDelegationRequest(requestId) {
	const realm = await resolveDelegationRealm();
	const [list, me] = await Promise.all([realm.read('delegation_list', { count: 1 }), realm.read('me')]);
	if (!list.ok) return legacy(list);
	const request = list.requests.find((r) => r.id === requestId);
	if (!request) return { success: false, error: 'This request is closed or was not made to you' };
	const permissions = request.scope.operations.map((operation) => ({
		operation,
		label: operation,
		grantor_holds: holds(me, operation)
	}));
	return { success: true, data: { ...request, permissions } };
}

/** Approve a request, with a subset of its permissions and an expiry no later than asked. */
export async function approveDelegationRequest(requestId, { operations, expiresAt } = {}) {
	const realm = await resolveDelegationRealm();
	return legacy(
		await realm.run('delegation_request_decide', {
			request: requestId,
			approve: true,
			operations,
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
