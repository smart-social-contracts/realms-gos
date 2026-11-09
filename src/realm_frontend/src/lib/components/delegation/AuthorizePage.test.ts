// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const realm = vi.hoisted(() => ({ read: vi.fn(), run: vi.fn() }));

vi.mock('$lib/canisters', () => ({
	backend: {},
	quarterBackend: {},
	setActiveQuarter: async () => {},
	whenBackendSignsAs: async () => {}
}));
vi.mock('$lib/realm', () => ({ read: realm.read, run: realm.run, quarter: realm }));
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
import Page from '../../../routes/(sidebar)/delegations/authorize/[[request]]/+page.svelte';

// A whole minute, which is what the page's date field holds.
const FAR = 66_666_666 * 60;
const group = (id: string, confirms = false) => ({
	id,
	label: `Label of ${id}`,
	summary: `What ${id} allows`,
	risk: `Risk of ${id}`,
	longest: 31_536_000,
	confirms
});
const vocabulary = [group('vote'), group('propose', true), group('money')];

/** What the realm answers for a request with this scope, to a member who holds `permissions` and is not the delegate's kind. */
function realmWith(scope: object, permissions: string[] = ['proposal.vote'], member = false) {
	realm.read.mockImplementation(async (name: string) => {
		if (name === 'delegation_list') {
			return {
				ok: true,
				as_grantor: [],
				as_delegate: [],
				next: {},
				requests: [
					{
						id: 'DR-1',
						grantor: 'alice',
						delegate: 'claude-principal',
						delegate_is_member: member,
						scope,
						label: 'Claude',
						description: 'vote for me',
						starts_at: 0,
						expires_at: FAR,
						request_expires_at: FAR
					}
				]
			};
		}
		if (name === 'me') return { ok: true, permissions };
		if (name === 'delegable_operations') return { ok: true, operations: [], groups: vocabulary };
		throw new Error(`unexpected read ${name}`);
	});
	realm.run.mockResolvedValue({ ok: true, delegation: 'D-1', status: 'active' });
}

const open = async () => {
	render(Page);
	await screen.findByRole('heading', { level: 1 });
};
const box = (name: RegExp | string) => screen.getByRole('checkbox', { name }) as HTMLInputElement;
const decided = () => realm.run.mock.calls.map(([name, args]) => [name, args]);

beforeEach(() => {
	bootI18n();
	realm.read.mockReset();
	realm.run.mockReset();
	isAuthenticated.set(true);
	page.set({ params: { request: 'DR-1' }, url: new URL('http://localhost/delegations/authorize/DR-1') });
});
afterEach(() => cleanup());

describe('the Authorize page for a request by groups', () => {
	beforeEach(() => realmWith({ groups: ['vote', 'propose'] }));

	it('lists the groups asked for in the realm\'s words, ticked, with a badge on those that ask each time', async () => {
		await open();
		expect(screen.getByText('Authorize Claude')).toBeTruthy();
		expect(box(/Label of vote/).checked).toBe(true);
		expect(box(/Label of propose/).checked).toBe(true);
		expect(screen.getByText('What vote allows')).toBeTruthy();
		expect(screen.getByText('Risk of propose')).toBeTruthy();
		expect(screen.getAllByText('Asks you each time')).toHaveLength(1);
		expect(screen.queryByRole('checkbox', { name: /Label of money/ })).toBeNull();
		expect(screen.getByText('Not a member: for example an AI assistant connection.', { exact: false })).toBeTruthy();
		expect(screen.getByText('Never, even with everything: Launch, and approving the actions it prepares.')).toBeTruthy();
	});

	it('does not offer everything', async () => {
		await open();
		expect(screen.queryByRole('checkbox', { name: /Everything/ })).toBeNull();
	});

	it('grants the groups left ticked, and only those', async () => {
		await open();
		await fireEvent.click(box(/Label of propose/));
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(screen.getByText('Authorized')).toBeTruthy());
		expect(decided()).toEqual([['delegation_request_decide', { request: 'DR-1', approve: true, groups: ['vote'] }]]);
	});

	it('asks for no change of expiry when the member leaves it', async () => {
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(realm.run).toHaveBeenCalled());
		expect(realm.run.mock.calls[0][1].expires_at).toBeUndefined();
	});

	it('sends an earlier expiry as the member made it', async () => {
		await open();
		await fireEvent.input(screen.getByLabelText('Until'), { target: { value: '2090-01-01T00:00' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(realm.run).toHaveBeenCalled());
		expect(realm.run.mock.calls[0][1].expires_at).toBe(Math.floor(new Date('2090-01-01T00:00').getTime() / 1000));
	});

	it('refuses an expiry later than the request asked for and sends nothing', async () => {
		await open();
		await fireEvent.input(screen.getByLabelText('Until'), { target: { value: '2200-01-01T00:00' } });
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		expect((await screen.findByRole('alert')).textContent).toContain('The expiry can be at most');
		expect(realm.run).not.toHaveBeenCalled();
	});

	it('cannot grant with nothing ticked', async () => {
		await open();
		await fireEvent.click(box(/Label of vote/));
		await fireEvent.click(box(/Label of propose/));
		expect((screen.getByRole('button', { name: 'Authorize' }) as HTMLButtonElement).disabled).toBe(true);
		expect(realm.run).not.toHaveBeenCalled();
	});

	it('declines without granting anything', async () => {
		await open();
		await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
		await waitFor(() => expect(screen.getByText('Request declined')).toBeTruthy());
		expect(decided()).toEqual([['delegation_request_decide', { request: 'DR-1', approve: false }]]);
	});
});

describe('the Authorize page for a request from a member', () => {
	it('says the requester is a member', async () => {
		realmWith({ groups: ['vote'] }, ['proposal.vote'], true);
		await open();
		expect(screen.getByText('A member of this realm.', { exact: false })).toBeTruthy();
		expect(screen.queryByText(/Not a member/)).toBeNull();
	});
});

describe('the Authorize page for a group the member holds nothing in', () => {
	it('shows it as one they cannot grant', async () => {
		realmWith({ groups: ['vote', 'office'] });
		await open();
		const office = box(/office/);
		expect(office.disabled).toBe(true);
		expect(office.checked).toBe(false);
		expect(screen.getByText('You hold nothing in this group, so you cannot grant it.')).toBeTruthy();
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(realm.run).toHaveBeenCalled());
		expect(realm.run.mock.calls[0][1].groups).toEqual(['vote']);
	});
});

describe('the Authorize page when the realm will not say what the groups are', () => {
	it('shows the realm\'s error, not ids the member seems to hold nothing in', async () => {
		realmWith({ groups: ['vote'] });
		const read = realm.read.getMockImplementation()!;
		realm.read.mockImplementation(async (name: string) =>
			name === 'delegable_operations' ? { ok: false, error: 'sign in first' } : read(name)
		);
		await open();
		expect(screen.getByText('Request not available')).toBeTruthy();
		expect(screen.getByText('sign in first')).toBeTruthy();
		expect(screen.queryByText(/You hold nothing in this group/)).toBeNull();
		expect(screen.queryByRole('checkbox')).toBeNull();
	});
});

describe('the Authorize page for a request for everything', () => {
	beforeEach(() => realmWith({ groups: ['*'] }));

	it('offers everything with its warning, ticks nothing, and will not grant until the member ticks', async () => {
		await open();
		expect(box(/Everything/).checked).toBe(false);
		expect(screen.getByText(/Everything you can do in this realm, now and later, for up to 90 days/)).toBeTruthy();
		for (const id of ['vote', 'propose', 'money']) expect(box(new RegExp(`Label of ${id}`)).checked).toBe(false);
		expect((screen.getByRole('button', { name: 'Authorize' }) as HTMLButtonElement).disabled).toBe(true);
	});

	it('grants everything only once the member ticks it', async () => {
		await open();
		await fireEvent.click(box(/Everything/));
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(realm.run).toHaveBeenCalled());
		expect(realm.run.mock.calls[0][1].groups).toEqual(['*']);
	});

	it('may be narrowed to some groups', async () => {
		await open();
		await fireEvent.click(box(/Label of money/));
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(realm.run).toHaveBeenCalled());
		expect(realm.run.mock.calls[0][1].groups).toEqual(['money']);
	});
});

describe('the Authorize page for a request by permission names', () => {
	it('shows them as it always did and grants permission names', async () => {
		realmWith({ operations: ['proposal.vote', 'self.data_view'] });
		await open();
		expect(box(/proposal\.vote/).checked).toBe(true);
		expect(box(/self\.data_view/).disabled).toBe(true);
		await fireEvent.click(screen.getByRole('button', { name: 'Authorize' }));
		await waitFor(() => expect(realm.run).toHaveBeenCalled());
		expect(realm.run.mock.calls[0][1]).toMatchObject({ approve: true, operations: ['proposal.vote'] });
		expect(realm.run.mock.calls[0][1].groups).toBeUndefined();
	});
});

describe('the Authorize page framed by a stranger', () => {
	it('shows a refusal and reads and runs nothing', async () => {
		realmWith({ groups: ['vote'] });
		Object.defineProperty(window, 'top', { value: {}, configurable: true });
		Object.defineProperty(document, 'referrer', { value: 'https://evil.example/', configurable: true });
		try {
			render(Page);
			expect(await screen.findByText('Open this page directly')).toBeTruthy();
			expect(realm.read).not.toHaveBeenCalled();
			expect(screen.queryByRole('button', { name: 'Authorize' })).toBeNull();
		} finally {
			Object.defineProperty(window, 'top', { value: window, configurable: true });
			Object.defineProperty(document, 'referrer', { value: '', configurable: true });
		}
	});
});
