import { beforeEach, describe, expect, it, vi } from 'vitest';

const member = { ok: true, user: { id: 'p', profiles: ['member'] } };
const stranger = { ok: false, error: 'not a user of this realm' };

const { probed, setActiveQuarter, members } = vi.hoisted(() => ({
	probed: [] as string[],
	setActiveQuarter: vi.fn(async () => {}),
	members: new Set<string>(),
}));

function me(id: string) {
	probed.push(id);
	return members.has(id) ? member : stranger;
}

vi.mock('$lib/realm', () => ({
	read: async (name: string) =>
		name === 'quarter_directory'
			? {
					ok: true,
					capital: 'cap',
					quarters: [
						{ id: 'cap', capital: true },
						{ id: 'q1', capital: false },
						{ id: 'q2', capital: false },
						{ id: 'q3', capital: false },
					],
				}
			: me('cap'),
	must: async (reply: any) => reply,
}));
vi.mock('$lib/canisters', () => ({
	createQuarterActor: async (id: string) => ({ read: async () => JSON.stringify(me(id)) }),
	setActiveQuarter,
}));
vi.mock('$lib/stores/quarters', async () => {
	const { writable } = await import('svelte/store');
	return { activeQuarterId: writable(null) };
});

const store = new Map<string, string>();
vi.stubGlobal('localStorage', {
	getItem: (k: string) => store.get(k) ?? null,
	setItem: (k: string, v: string) => void store.set(k, v),
	removeItem: (k: string) => void store.delete(k),
	clear: () => store.clear(),
});

import { probeFederatedMembership, verifyAssignedQuarter } from './federatedMembership';

describe('probeFederatedMembership', () => {
	beforeEach(() => {
		probed.length = 0;
		members.clear();
		setActiveQuarter.mockClear();
		localStorage.clear();
	});

	it('stops at the cached home quarter when asked to', async () => {
		members.add('q2');
		localStorage.setItem('home_quarter', 'q2');
		const { primary, hits } = await probeFederatedMembership({ stopAtCachedHit: true });
		expect(probed).toEqual(['q2']);
		expect(primary?.canisterId).toBe('q2');
		expect(hits).toHaveLength(1);
		expect(setActiveQuarter).toHaveBeenCalledWith('q2');
	});

	it('falls back to probing everything when the cache is stale', async () => {
		members.add('q3');
		localStorage.setItem('home_quarter', 'q1');
		const { primary } = await probeFederatedMembership({ stopAtCachedHit: true });
		expect(new Set(probed)).toEqual(new Set(['q1', 'cap', 'q2', 'q3']));
		expect(primary?.canisterId).toBe('q3');
	});

	it('returns every membership in candidate order for a full scan', async () => {
		members.add('q3');
		members.add('q1');
		const { hits } = await probeFederatedMembership({ activate: false });
		expect(hits.map((h) => h.canisterId)).toEqual(['q1', 'q3']);
		expect(setActiveQuarter).not.toHaveBeenCalled();
	});

	it('caches the capital for capital members so the next reload stops early', async () => {
		members.add('cap');
		localStorage.setItem('home_quarter', 'q1');
		const first = await probeFederatedMembership({ stopAtCachedHit: true });
		expect(first.primary?.canisterId).toBe('cap');
		expect(localStorage.getItem('home_quarter')).toBe('cap');
		probed.length = 0;
		await probeFederatedMembership({ stopAtCachedHit: true });
		expect(probed).toEqual(['cap']);
	});
});

describe('verifyAssignedQuarter', () => {
	beforeEach(() => {
		probed.length = 0;
		members.clear();
	});
	const capitalHit = (assigned: string) => ({
		canisterId: 'cap',
		isCapital: true,
		profiles: ['member'],
		response: { success: true, data: { userGet: { profiles: ['member'], assigned_quarter: assigned } } },
	});

	it('ignores an assigned quarter that does not know the caller', async () => {
		members.add('cap');
		expect(await verifyAssignedQuarter(capitalHit('q3'))).toBeNull();
		expect(probed).toEqual(['q3']);
	});

	it('follows an assigned quarter that recognizes the caller', async () => {
		members.add('q3');
		const hit = await verifyAssignedQuarter(capitalHit('q3'));
		expect(hit?.canisterId).toBe('q3');
		expect(hit?.isCapital).toBe(false);
	});

	it('does nothing without an assigned quarter', async () => {
		expect(await verifyAssignedQuarter(capitalHit(''))).toBeNull();
		expect(probed).toEqual([]);
	});
});

