// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const realm = vi.hoisted(() => ({ read: vi.fn(), run: vi.fn() }));

vi.mock('$lib/canisters', () => ({
	backend: {},
	quarterBackend: {},
	setActiveQuarter: async () => {},
	whenBackendSignsAs: async () => {}
}));
vi.mock('$lib/realm', () => ({
	read: realm.read,
	run: realm.run,
	quarter: realm,
	must: async (reply: any) => {
		const out = await reply;
		if (!out.ok) throw new Error(out.error);
		return out;
	}
}));
vi.mock('$lib/stores/quarters', async () => {
	const { writable } = await import('svelte/store');
	return { activeQuarterId: writable(null) };
});
vi.mock('$lib/stores/auth', async () => {
	const { writable } = await import('svelte/store');
	return { isAuthenticated: writable(true), principal: writable('alice') };
});

import { page } from '$app/stores';
import { bootI18n } from '$lib/i18n/boot';
import { isAuthenticated } from '$lib/stores/auth';
import Page from '../../../routes/(sidebar)/delegations/+page.svelte';

const FAR = 66_666_666 * 60;
const group = (id: string, confirms = false) => ({
	id,
	label: `Label of ${id}`,
	summary: `What ${id} allows`,
	risk: `Risk of ${id}`,
	longest: 86400 * 90,
	confirms
});
const action = (id: string, title: string) => ({
	id,
	grantor: 'alice',
	delegate: 'claude-principal',
	delegate_is_member: false,
	grant: 'D-1',
	method: 'payment_account_add',
	args: {},
	title,
	summary: [{ label: 'What changes', value: `Change of ${id}` }],
	risk: `Risk of ${id}`,
	note: '',
	status: 'pending',
	hash: `HASH-${id}`,
	filed_at: 1,
	expires_at: FAR,
	decided_at: null,
	result: null
});
const grant = (over: object) => ({
	id: 'D-1',
	grantor: 'alice',
	delegate: 'claude-principal',
	scope: { groups: ['vote', 'propose'] },
	label: 'My Claude',
	description: '',
	starts_at: 0,
	expires_at: FAR,
	status: 'active',
	...over
});

function theRealm() {
	realm.read.mockImplementation(async (name: string, args: any) => {
		switch (name) {
			case 'delegation_list':
				return {
					ok: true,
					as_grantor: [grant({}), grant({ id: 'D-2', delegate: 'old-client', label: 'Old client', scope: { operations: ['proposal.vote'] } })],
					as_delegate: [],
					next: { grantor: null, delegate: null },
					requests: [
						{ id: 'DR-1', grantor: 'alice', delegate: 'bob-principal', scope: { groups: ['vote'] }, label: 'Bob', description: '', starts_at: 0, expires_at: FAR, request_expires_at: FAR }
					]
				};
			case 'action_list':
				return { ok: true, actions: [action('AC-1', 'Add a payout account'), action('AC-2', 'Move to another quarter')], next: null };
			case 'delegable_operations':
				return { ok: true, operations: [], groups: [group('vote'), group('propose', true), group('money')] };
			case 'delegation_activity':
				return {
					ok: true,
					events: [
						{ id: '9', op: 'proposal_vote', origin: 'user', args: { proposal: 'P-7' }, result: {}, at: 1_800_000_000 },
						{ id: '8', op: 'payment_account_add', origin: 'user', args: {}, result: { pending: 'AC-1' }, at: 1_799_000_000 }
					],
					since: 1_790_000_000,
					next: null
				};
			default:
				throw new Error(`unexpected read ${name} ${JSON.stringify(args)}`);
		}
	});
	realm.run.mockImplementation(async (name: string) => {
		if (name === 'action_approve') return { ok: true, action: { ...action('AC-1', 'Add a payout account'), status: 'done', result: { ok: true, account: 'PA-1' } } };
		if (name === 'action_decline') return { ok: true, action: { ...action('AC-2', 'Move to another quarter'), status: 'declined' } };
		if (name === 'delegation_revoke_all') return { ok: true, revoked: 2, next: null };
		if (name === 'delegation_grant') return { ok: true, delegation: 'D-3', status: 'active' };
		throw new Error(`unexpected run ${name}`);
	});
}

const open = async () => {
	render(Page);
	await screen.findByText('Waiting for you');
	await waitFor(() => expect(screen.queryByText('Add a payout account')).not.toBeNull());
};
const runs = (name: string) => realm.run.mock.calls.filter(([n]) => n === name).map(([, args]) => args);

beforeEach(() => {
	bootI18n();
	realm.read.mockReset();
	realm.run.mockReset();
	theRealm();
	isAuthenticated.set(true);
	page.set({ params: {}, url: new URL('http://localhost/delegations') });
});
afterEach(() => cleanup());

describe('the Power of attorney page', () => {
	it('is laid out as: waiting for you, your grants, activity, grants to you, the manual grant', async () => {
		await open();
		const headings = screen.getAllByRole('heading').map((h) => h.textContent?.trim());
		const order = ['Waiting for you', 'Your grants', 'Activity', 'Pending — accept to act on behalf', 'Granted to me (active)', 'Grant power of attorney'];
		const at = order.map((h) => headings.indexOf(h));
		expect(at.every((i) => i >= 0)).toBe(true);
		expect([...at].sort((a, b) => a - b)).toEqual(at);
	});
});

describe('Waiting for you', () => {
	it('shows each pending action as a card with the realm\'s summary and risk, and the requests with a link to review', async () => {
		await open();
		expect(realm.read).toHaveBeenCalledWith('action_list', { status: 'pending', from: 1, count: 500 });
		expect(screen.getByText('Add a payout account')).toBeTruthy();
		expect(screen.getByText('Move to another quarter')).toBeTruthy();
		expect(screen.getByText('Change of AC-1')).toBeTruthy();
		expect(screen.getAllByText('My Claude asks you to approve one action')).toHaveLength(2);
		expect(screen.getAllByRole('button', { name: 'Approve' })).toHaveLength(2);
		expect(screen.getAllByRole('button', { name: 'Decline' }).length).toBeGreaterThanOrEqual(2);
		expect(screen.getByText('Bob asks for power of attorney')).toBeTruthy();
		expect(screen.getByRole('link', { name: 'Review' }).getAttribute('href')).toBe('/delegations/authorize/DR-1');
	});

	it('approves one card with the id and hash it shows, and leaves the other waiting', async () => {
		await open();
		await fireEvent.click(screen.getAllByRole('button', { name: 'Approve' })[0]);
		await waitFor(() => expect(screen.getByText('Done.')).toBeTruthy());
		expect(runs('action_approve')).toEqual([{ action: 'AC-1', hash: 'HASH-AC-1' }]);
		expect(screen.getAllByRole('button', { name: 'Approve' })).toHaveLength(1);
	});

	it('declines the other with its id alone', async () => {
		await open();
		const second = screen.getByText('Move to another quarter').closest('div')!.parentElement!;
		await fireEvent.click(within(second as HTMLElement).getByRole('button', { name: 'Decline' }));
		await waitFor(() => expect(screen.getByText('Declined. Nothing ran.')).toBeTruthy());
		expect(runs('action_decline')).toEqual([{ action: 'AC-2' }]);
	});

	it('offers no way to decide inside a stranger\'s frame, whatever the card says', async () => {
		Object.defineProperty(window, 'top', { value: {}, configurable: true });
		Object.defineProperty(document, 'referrer', { value: 'https://evil.example/page', configurable: true });
		try {
			await open();
			expect(screen.getByText('Add a payout account')).toBeTruthy();
			expect(screen.getAllByText(/cannot be made inside another site/)).toHaveLength(2);
			expect(screen.queryAllByRole('button', { name: 'Approve' })).toHaveLength(0);
			expect(screen.queryAllByRole('button', { name: 'Decline' })).toHaveLength(0);
			expect(realm.run).not.toHaveBeenCalled();
		} finally {
			Object.defineProperty(window, 'top', { value: window, configurable: true });
			Object.defineProperty(document, 'referrer', { value: '', configurable: true });
		}
	});

	it('says so when nothing waits', async () => {
		realm.read.mockImplementation(async (name: string) =>
			name === 'action_list'
				? { ok: true, actions: [], next: null }
				: name === 'delegation_list'
					? { ok: true, as_grantor: [], as_delegate: [], next: {}, requests: [] }
					: { ok: true, groups: [], operations: [] }
		);
		render(Page);
		expect(await screen.findByText('Nothing waits for you.')).toBeTruthy();
	});
});

describe('Your grants', () => {
	it('shows the groups of a grant as chips, and the permission names of an old one', async () => {
		await open();
		const mine = screen.getByText('My Claude').closest('li')!;
		expect(within(mine).getByText('Label of vote')).toBeTruthy();
		expect(within(mine).getByText('Label of propose')).toBeTruthy();
		const old = screen.getByText('Old client').closest('li')!;
		expect(within(old).getByText('proposal.vote')).toBeTruthy();
	});

	it('asks on the page before it revokes every grant, then does', async () => {
		const confirm = vi.spyOn(window, 'confirm');
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Revoke all' }));
		expect(screen.getByText('Revoke all your grants? Every delegate stops acting for you at once.')).toBeTruthy();
		expect(runs('delegation_revoke_all')).toEqual([]);
		await fireEvent.click(screen.getByRole('button', { name: 'Yes, revoke all' }));
		expect(await screen.findByText('Revoked 2 grants.')).toBeTruthy();
		expect(runs('delegation_revoke_all')).toEqual([{ from: 1, count: 500 }]);
		expect(confirm).not.toHaveBeenCalled();
	});

	it('revokes nothing when the member cancels the question', async () => {
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Revoke all' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		expect(screen.getByRole('button', { name: 'Revoke all' })).toBeTruthy();
		expect(screen.queryByRole('button', { name: 'Yes, revoke all' })).toBeNull();
		expect(runs('delegation_revoke_all')).toEqual([]);
	});
});

describe('Activity', () => {
	it('shows what a delegate did, with links, and how far back the realm\'s log reaches', async () => {
		await open();
		await fireEvent.click(screen.getAllByRole('link', { name: 'Activity' })[0]);
		expect(await screen.findByText('What My Claude did')).toBeTruthy();
		expect(realm.read).toHaveBeenCalledWith('delegation_activity', expect.objectContaining({ delegation: 'D-1', count: 50 }));
		expect(screen.getByRole('link', { name: 'P-7' }).getAttribute('href')).toBe('/proposal/P-7');
		expect(screen.getByRole('link', { name: 'AC-1' }).getAttribute('href')).toBe('/delegations/confirm/AC-1');
		expect(screen.getByText(/The realm keeps its most recent events only\. This list reaches back to/)).toBeTruthy();
	});
});

describe('the manual grant', () => {
	it('lists the groups, never offers everything, and suggests 30 days', async () => {
		await open();
		await screen.findByLabelText(/Label of money/);
		expect(screen.queryByRole('checkbox', { name: /Everything/ })).toBeNull();
		const days = (new Date((screen.getByLabelText('Expires') as HTMLInputElement).value).getTime() - Date.now()) / 86_400_000;
		expect(days).toBeGreaterThan(29.9);
		expect(days).toBeLessThan(30.1);
	});

	it('grants the groups ticked', async () => {
		await open();
		await fireEvent.input(screen.getByLabelText('Grant to (principal)'), { target: { value: 'some-principal' } });
		await fireEvent.click(await screen.findByLabelText(/Label of money/));
		await fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
		expect(await screen.findByText('Granted.')).toBeTruthy();
		const [sent] = runs('delegation_grant');
		expect(sent).toMatchObject({ delegate: 'some-principal', groups: ['money'] });
		expect(sent.operations).toBeUndefined();
	});

	it('says so when the groups could not be read, instead of claiming nothing can be delegated', async () => {
		const read = realm.read.getMockImplementation()!;
		realm.read.mockImplementation(async (name: string, args: unknown) =>
			name === 'delegable_operations' ? { ok: false, error: 'sign in first' } : read(name, args)
		);
		render(Page);
		expect((await screen.findByRole('alert')).textContent).toBe('sign in first');
	});

	it('will not grant with no group ticked', async () => {
		await open();
		await fireEvent.input(screen.getByLabelText('Grant to (principal)'), { target: { value: 'some-principal' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
		expect((await screen.findByRole('alert')).textContent).toContain('Choose at least one');
		expect(runs('delegation_grant')).toEqual([]);
	});
});
