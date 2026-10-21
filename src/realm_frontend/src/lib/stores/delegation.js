import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';
import { backend, quarterBackend, setActiveQuarter, whenBackendSignsAs } from '$lib/canisters.js';
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

/** Cached delegation lists from list_delegations_json. */
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

function parseJson(raw) {
	return typeof raw === 'string' ? JSON.parse(raw) : raw;
}

export async function loadDelegations(backendActor) {
	const backend = backendActor || (await resolveDelegationBackend());
	if (!backend?.list_delegations_json) return;
	try {
		if (!backend.list_delegations_page_json) {
			const parsed = parseJson(await backend.list_delegations_json());
			if (!parsed?.success) return;
			const data = parsed.data || {};
			delegationsAsDelegate.set(data.as_delegate || []);
			delegationsAsGrantor.set(data.as_grantor || []);
			pendingDelegations.set(data.pending_inbox || []);
			return;
		}
		const asGrantor = [];
		const asDelegate = [];
		let grantorCursor = 1;
		let delegateCursor = 1;
		while (grantorCursor || delegateCursor) {
			const parsed = parseJson(
				await backend.list_delegations_page_json(
					JSON.stringify({
						grantor_cursor: grantorCursor || 0,
						delegate_cursor: delegateCursor || 0,
						count: 100
					})
				)
			);
			if (!parsed?.success) return;
			const data = parsed.data || {};
			asGrantor.push(...(data.as_grantor || []));
			asDelegate.push(...(data.as_delegate || []));
			grantorCursor = data.next_grantor_cursor;
			delegateCursor = data.next_delegate_cursor;
		}
		delegationsAsDelegate.set(asDelegate);
		delegationsAsGrantor.set(asGrantor);
		pendingDelegations.set(asDelegate.filter((d) => d.status === 'pending'));
	} catch (e) {
		console.warn('Failed to load delegations:', e);
	}
}

/** Operations the caller holds here and may grant: [{operation, label, description}]. */
export async function loadDelegableOperations() {
	const backend = await resolveDelegationBackend();
	if (!backend?.get_delegable_operations_json) return [];
	const parsed = parseJson(await backend.get_delegable_operations_json());
	return parsed?.success ? parsed.data || [] : [];
}

/** Grant power of attorney from the caller. Times are epoch seconds. */
export async function grantDelegation({ grantor, delegate, operations, label, description, startsAt, expiresAt }) {
	const backend = await resolveDelegationBackend();
	return parseJson(
		await backend.grant_delegation_json(
			JSON.stringify({
				grantor,
				delegate,
				scope: { operations },
				label,
				description,
				starts_at: startsAt,
				expires_at: expiresAt
			})
		)
	);
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
