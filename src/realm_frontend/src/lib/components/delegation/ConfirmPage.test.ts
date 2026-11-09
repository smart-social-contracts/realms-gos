// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const realm = vi.hoisted(() => ({ read: vi.fn(), run: vi.fn() }));
const signedIn = vi.hoisted(() => ({ value: true }));

vi.mock('$lib/canisters', () => ({ backend: {}, quarterBackend: {} }));
vi.mock('$lib/stores/auth', async () => {
	const { writable } = await import('svelte/store');
	return { isAuthenticated: writable(signedIn.value), principal: writable('alice') };
});
vi.mock('$lib/stores/delegation.js', async () => {
	const { writable } = await import('svelte/store');
	return {
		resolveDelegationRealm: async () => realm,
		loadDelegations: async () => {},
		delegationsAsGrantor: writable([{ id: 'D-1', label: 'My Claude' }]),
		delegationsAsDelegate: writable([]),
		memberNickname: async (principal: string) => (principal === 'alice' ? 'Alice' : '')
	};
});

import { page } from '$app/stores';
import { bootI18n } from '$lib/i18n/boot';
import { isAuthenticated, principal } from '$lib/stores/auth';
import type { ActionCardData } from '$lib/stores/actions';
import Page from '../../../routes/(sidebar)/delegations/confirm/[action]/+page.svelte';

const card = (over: Partial<ActionCardData> = {}): ActionCardData => ({
	id: 'AC-1',
	grantor: 'alice',
	delegate: 'claude-principal',
	delegate_is_member: false,
	grant: 'D-1',
	method: 'payment_account_add',
	args: { address: 'abcd', symbol: 'ckBTC' },
	title: 'Add a payout account',
	summary: [
		{ label: 'address', value: 'abcd' },
		{ label: 'symbol', value: 'ckBTC' },
		{ label: 'What changes', value: 'Realm payments to you in "ckBTC" will go to "abcd" instead of "x"' }
	],
	risk: 'Decides where your money goes, and the realm cannot pull a payment back.',
	note: 'Please do it <b>now</b>',
	status: 'pending',
	hash: 'HASH-1',
	filed_at: 1,
	expires_at: 4_000_000_000,
	decided_at: null,
	result: null,
	...over
});

const open = async () => {
	render(Page);
	await screen.findByRole('heading', { level: 1 });
};
const buttons = () => screen.queryAllByRole('button').map((b) => b.textContent?.trim());

beforeEach(() => {
	bootI18n();
	realm.read.mockReset();
	realm.run.mockReset();
	realm.read.mockResolvedValue({ ok: true, action: card() });
	isAuthenticated.set(true);
	principal.set('alice');
	page.set({ params: { action: 'AC-1' }, url: new URL('http://localhost/delegations/confirm/AC-1') });
});
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
});

describe('the confirm page framed by a stranger', () => {
	it('shows a refusal and reads and runs nothing', async () => {
		Object.defineProperty(window, 'top', { value: {}, configurable: true });
		Object.defineProperty(document, 'referrer', { value: 'https://evil.example/page', configurable: true });
		try {
			render(Page);
			expect(await screen.findByText('Open this page directly')).toBeTruthy();
			expect(screen.queryByText('Approve')).toBeNull();
			expect(realm.read).not.toHaveBeenCalled();
			expect(realm.run).not.toHaveBeenCalled();
		} finally {
			Object.defineProperty(window, 'top', { value: window, configurable: true });
			Object.defineProperty(document, 'referrer', { value: '', configurable: true });
		}
	});
});

describe('the confirm page signed out', () => {
	it('asks to sign in and reads nothing', async () => {
		isAuthenticated.set(false);
		render(Page);
		expect(await screen.findByText('Sign in to review this action')).toBeTruthy();
		expect(realm.read).not.toHaveBeenCalled();
	});
});

describe('the confirm page for a pending action', () => {
	it('shows the realm\'s record from the title down, the delegate\'s words apart, and nothing editable', async () => {
		const { container } = render(Page);
		expect(await screen.findByText('Add a payout account')).toBeTruthy();
		expect(realm.read).toHaveBeenCalledWith('action_get', { action: 'AC-1' });
		expect(screen.getByText('My Claude asks you to approve one action')).toBeTruthy();
		expect(screen.getByText('claude-principal')).toBeTruthy();
		expect(screen.getByText('Not a member: for example an AI assistant connection.')).toBeTruthy();
		expect(screen.getByText('payment_account_add')).toBeTruthy();
		expect(screen.getByText(/Realm payments to you in "ckBTC" will go to "abcd"/)).toBeTruthy();
		expect(screen.getByText(/Decides where your money goes/)).toBeTruthy();
		// The assistant's note is text, never markup, and says whose words they are.
		expect(container.querySelector('b')).toBeNull();
		expect(screen.getByText('Please do it <b>now</b>')).toBeTruthy();
		expect(screen.getByText(/My Claude says \(their own words, not the realm's\)/)).toBeTruthy();
		// Arguments sit in their own block, not among the realm's lines.
		const given = container.querySelector('dl')!;
		expect(given.textContent).toContain('address');
		expect(given.textContent).not.toContain('What changes');
		expect(screen.getByText('Recorded as your own act, prepared by My Claude.')).toBeTruthy();
		expect(screen.getByText(/You can decide until /)).toBeTruthy();
		expect(container.querySelectorAll('input, textarea, select, [contenteditable]')).toHaveLength(0);
		expect(buttons()).toEqual(['Decline', 'Approve']);
	});

	it('approves with the id and the hash it shows, and shows the outcome', async () => {
		realm.run.mockResolvedValue({
			ok: true,
			action: card({ status: 'done', result: { ok: true, account: 'PA-3' } })
		});
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
		await waitFor(() => expect(screen.getByText('Done.')).toBeTruthy());
		expect(realm.run).toHaveBeenCalledTimes(1);
		expect(realm.run).toHaveBeenCalledWith('action_approve', { action: 'AC-1', hash: 'HASH-1' });
		expect(screen.getByText('PA-3')).toBeTruthy();
		expect(buttons()).toEqual([]);
	});

	it('links the proposal an approval made', async () => {
		realm.run.mockResolvedValue({ ok: true, action: card({ status: 'done', result: { ok: true, proposal: 'P-12' } }) });
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
		const link = await screen.findByRole('link', { name: 'P-12' });
		expect(link.getAttribute('href')).toBe('/proposal/P-12');
	});

	it('shows why a call the member approved was refused', async () => {
		realm.run.mockResolvedValue({
			ok: true,
			action: card({ status: 'failed', result: { ok: false, error: 'alice is banned' } })
		});
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
		expect(await screen.findByText('alice is banned')).toBeTruthy();
		expect(buttons()).toEqual([]);
	});

	it('declines with the id alone', async () => {
		realm.run.mockResolvedValue({ ok: true, action: card({ status: 'declined' }) });
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
		await waitFor(() => expect(screen.getByText('Declined. Nothing ran.')).toBeTruthy());
		expect(realm.run).toHaveBeenCalledWith('action_decline', { action: 'AC-1' });
		expect(buttons()).toEqual([]);
	});

	it('shows the realm\'s refusal and the action as it now stands', async () => {
		realm.run.mockResolvedValue({ ok: false, error: 'AC-1 is expired' });
		realm.read.mockResolvedValueOnce({ ok: true, action: card() }).mockResolvedValueOnce({
			ok: true,
			action: card({ status: 'expired' })
		});
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
		expect(await screen.findByText('AC-1 is expired')).toBeTruthy();
		await waitFor(() => expect(buttons()).toEqual([]));
		expect(screen.getByText(/Expired: it waited more than a day/)).toBeTruthy();
	});
});

describe('the confirm page for an action that is not waiting', () => {
	it.each([
		['expired', /Expired: it waited more than a day/],
		['void', /No longer valid/],
		['declined', /Declined\. Nothing ran\./],
		['withdrawn', /Withdrawn by whoever prepared it/],
		['done', /Done\./],
		['failed', /Failed: the realm refused it/],
		['approving', /Running now/],
		['approved', /Approved\. Whoever prepared it can now read it once/]
	])('shows %s and has no button', async (status, text) => {
		realm.read.mockResolvedValue({ ok: true, action: card({ status: status as ActionCardData['status'] }) });
		await open();
		expect(screen.getByText(text)).toBeTruthy();
		expect(buttons()).toEqual([]);
		// Nothing asks the member to decide any more, so the card no longer says it does.
		expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('My Claude prepared one action for you');
		expect(screen.queryByText(/asks you to approve/)).toBeNull();
		expect(screen.queryByText(/You can decide until/)).toBeNull();
	});
});

describe('the confirm page for a delegate who is a member', () => {
	it('says so', async () => {
		realm.read.mockResolvedValue({ ok: true, action: card({ delegate_is_member: true }) });
		await open();
		expect(screen.getByText('A member of this realm.')).toBeTruthy();
		expect(screen.queryByText(/Not a member/)).toBeNull();
	});
});

describe('the confirm page for someone it is not for', () => {
	it('says it is not available when the realm gives no card', async () => {
		realm.read.mockResolvedValue({ ok: true, action: null });
		await open();
		expect(screen.getByText('Action not available')).toBeTruthy();
		expect(screen.getByText('alice')).toBeTruthy();
		expect(buttons()).toEqual([]);
	});

	it('lets the delegate read the action, in words for the delegate, but not decide it', async () => {
		principal.set('claude-principal');
		const { container } = render(Page);
		await screen.findByRole('heading', { level: 1 });
		await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('You asked Alice to approve one action'));
		expect(screen.getByText('Add a payout account')).toBeTruthy();
		expect(screen.getByText('Only the member it was prepared for can approve or decline it.')).toBeTruthy();
		expect(screen.getByText(/Alice can decide until /)).toBeTruthy();
		expect(screen.getByText('The call, as you gave it')).toBeTruthy();
		expect(screen.getByText('You said (your own words, not the realm\'s)')).toBeTruthy();
		expect(screen.getByText('Recorded as Alice\'s own act, prepared by you.')).toBeTruthy();
		// None of what the card says to the member: who asks, that the name is unverified, who prepared it.
		expect(container.textContent).not.toMatch(/asks you to approve|You can decide until|is not verified|Prepared by the principal/);
		expect(buttons()).toEqual([]);
	});

	it('names a member with no nickname by their principal, shortened', async () => {
		principal.set('claude-principal');
		realm.read.mockResolvedValue({ ok: true, action: card({ grantor: 'zzzzzzzzzzzzzzzzzzzzzzzz' }) });
		await open();
		await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('You asked zzzzzzzz…zzzzzz to approve one action'));
	});

	it('tells the delegate of a decided action what it did, not what the member may do', async () => {
		principal.set('claude-principal');
		realm.read.mockResolvedValue({ ok: true, action: card({ status: 'declined' }) });
		await open();
		expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('You prepared one action for Alice');
		expect(screen.queryByText(/can decide until/)).toBeNull();
	});
});

describe('the note of the delegate', () => {
	it('is text that reads in its own direction, with control and bidirectional characters shown as escapes', async () => {
		realm.read.mockResolvedValue({ ok: true, action: card({ note: 'Pay \u202Eeulav\u200F now\u0007\nthank you' }) });
		const { container } = render(Page);
		await screen.findByRole('heading', { level: 1 });
		const note = container.querySelector('blockquote')!;
		expect(note.getAttribute('dir')).toBe('auto');
		expect(note.textContent).toBe('Pay \\u{202e}eulav\\u{200f} now\\u{7}\nthank you');
	});
});
