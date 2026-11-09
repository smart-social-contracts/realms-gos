import { beforeEach, describe, expect, it, vi } from 'vitest';

const realm = vi.hoisted(() => ({ read: vi.fn(), run: vi.fn() }));
vi.mock('$lib/stores/delegation.js', () => ({ resolveDelegationRealm: async () => realm }));
vi.mock('$lib/canisters', () => ({ backend: {}, quarterBackend: {} }));
vi.mock('@dfinity/agent', () => ({ Actor: {}, pollForResponse: vi.fn(), requestIdOf: vi.fn() }));

import {
	approveAction,
	argumentLines,
	declineAction,
	failure,
	grantLabel,
	loadAction,
	loadWaiting,
	madeBy,
	nextStep,
	realmLines,
	type ActionCardData
} from './actions';

const card = (over: Partial<ActionCardData> = {}): ActionCardData => ({
	id: 'AC-1',
	grantor: 'alice',
	delegate: 'claude',
	grant: 'D-1',
	method: 'payment_account_add',
	args: { address: 'abcd', symbol: 'ckBTC' },
	title: 'Add a payout account',
	summary: [
		{ label: 'address', value: 'abcd' },
		{ label: 'symbol', value: 'ckBTC' },
		{ label: 'What changes', value: 'Realm payments to you in "ckBTC" will go to "abcd" instead of "x"' },
		{ label: 'What happens next', value: 'Runs at once' }
	],
	risk: 'Decides where your money goes',
	note: '',
	status: 'pending',
	hash: 'HASH-1',
	filed_at: 1,
	expires_at: 2,
	decided_at: null,
	result: null,
	...over
});

beforeEach(() => {
	realm.read.mockReset();
	realm.run.mockReset();
});

describe('deciding an action', () => {
	it('sends the id and the hash of the card, and nothing else, to approve', async () => {
		realm.run.mockResolvedValue({ ok: true, action: card({ status: 'done' }) });
		const done = await approveAction(card());
		expect(realm.run).toHaveBeenCalledTimes(1);
		expect(realm.run).toHaveBeenCalledWith('action_approve', { action: 'AC-1', hash: 'HASH-1' });
		expect(done.status).toBe('done');
	});

	it('sends only the id to decline', async () => {
		realm.run.mockResolvedValue({ ok: true, action: card({ status: 'declined' }) });
		await declineAction(card());
		expect(realm.run).toHaveBeenCalledWith('action_decline', { action: 'AC-1' });
	});

	it('passes the realm\'s refusal on', async () => {
		realm.run.mockResolvedValue({ ok: false, error: 'AC-1 is expired' });
		await expect(approveAction(card())).rejects.toThrow('AC-1 is expired');
	});
});

describe('reading actions', () => {
	it('reads one by its id; the realm answers null for one that is not the caller\'s', async () => {
		realm.read.mockResolvedValue({ ok: true, action: null });
		expect(await loadAction('AC-9')).toBeNull();
		expect(realm.read).toHaveBeenCalledWith('action_get', { action: 'AC-9' });
	});

	it('lists the actions that wait, page after page', async () => {
		realm.read
			.mockResolvedValueOnce({ ok: true, actions: [card({ id: 'AC-1' })], next: 501 })
			.mockResolvedValueOnce({ ok: true, actions: [card({ id: 'AC-2' })], next: null });
		expect((await loadWaiting()).map((a) => a.id)).toEqual(['AC-1', 'AC-2']);
		expect(realm.read).toHaveBeenNthCalledWith(1, 'action_list', { status: 'pending', from: 1, count: 500 });
		expect(realm.read).toHaveBeenNthCalledWith(2, 'action_list', { status: 'pending', from: 501, count: 500 });
	});
});

describe('the lines of a card', () => {
	it('keep the delegate\'s arguments apart from the realm\'s words', () => {
		expect(argumentLines(card()).map((l) => l.label)).toEqual(['address', 'symbol']);
		expect(realmLines(card()).map((l) => l.label)).toEqual(['What changes']);
		expect(nextStep(card())).toBe('Runs at once');
	});

	it('never pass an argument off as the realm\'s, whatever its name', () => {
		const spoof = card({
			summary: [
				{ label: 'What changes', value: 'fine' },
				{ label: 'what changes', value: 'nothing' },
				{ label: 'WhatChanges', value: 'nothing' },
				{ label: ' What changes', value: 'nothing' },
				{ label: 'What', value: 'nothing' }
			]
		});
		expect(realmLines(spoof).map((l) => l.label)).toEqual(['What changes']);
		expect(argumentLines(spoof)).toHaveLength(4);
	});
});

describe('what a decided action says', () => {
	it('lists what the call made, as text', () => {
		const done = card({ status: 'done', result: { ok: true, proposal: 'P-3', details: { a: 1 } } });
		expect(madeBy(done)).toEqual([
			['proposal', 'P-3'],
			['details', '{"a":1}']
		]);
	});

	it('gives the reason the realm refused it', () => {
		expect(failure(card({ status: 'failed', result: { ok: false, error: 'not yours' } }))).toBe('not yours');
		expect(failure(card({ status: 'failed', result: null }))).toBe('');
	});

	it('names a delegate by the label the member gave its grant', () => {
		expect(grantLabel(card(), [{ id: 'D-2', label: 'other' }, { id: 'D-1', label: 'My Claude' }])).toBe('My Claude');
		expect(grantLabel(card(), [])).toBe('');
	});
});
