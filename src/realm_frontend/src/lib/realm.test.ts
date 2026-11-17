import { describe, expect, it, vi } from 'vitest';
import { IDL } from '@dfinity/candid';

const poll = vi.fn();
vi.mock('$lib/canisters', () => ({ backend: {}, quarterBackend: {} }));
vi.mock('@dfinity/agent', () => ({
	Actor: { agentOf: () => 'agent', canisterIdOf: () => 'realm-id' },
	pollForResponse: (...args: unknown[]) => poll(...args),
	requestIdOf: () => 'request-id'
}));

import { get } from 'svelte/store';
import { sentForApproval } from '$lib/stores/approvalSent';
import { on, stillProcessing } from './realm';

function processing() {
	const error: any = new Error('Call was returned undefined. We cannot determine if the call was successful or not.');
	error.cause = { code: { callContext: { httpDetails: { requestDetails: { method_name: 'run' } } } } };
	return error;
}

describe('an update the agent gave up on while it was still processing', () => {
	it('is polled for its reply', async () => {
		poll.mockResolvedValueOnce({ reply: IDL.encode([IDL.Text], ['{"ok":true,"installed":"role_manager"}']) });
		const actor = { run: vi.fn().mockRejectedValue(processing()) };
		expect(await on(actor).run('extension_install', { extension: 'role_manager' })).toEqual({
			ok: true,
			installed: 'role_manager'
		});
		expect(poll).toHaveBeenCalledWith('agent', 'realm-id', 'request-id');
	});

	it('leaves other failures alone', async () => {
		await expect(stillProcessing({}, new Error('sign in first'))).rejects.toThrow('sign in first');
		const actor = { read: vi.fn().mockRejectedValue(processing()) };
		await expect(on(actor).read('me')).rejects.toThrow('Call was returned undefined');
	});
});

describe('a call that was filed for a member to approve instead of done', () => {
	const filed = (link: string) => `{"ok":true,"pending":"AC-3","approve_link":"${link}","expires_at":9}`;
	const confirmLink = '/delegations/confirm/AC-3';

	it('is noted once, whichever page made it', async () => {
		sentForApproval.set(null);
		const actor = { run: vi.fn().mockResolvedValue(filed(confirmLink)) };
		await on(actor).run('payment_account_add', { on_behalf_of: 'alice', address: 'a' });
		expect(get(sentForApproval)).toEqual({ action: 'AC-3', href: confirmLink, grantor: 'alice' });
	});

	it('is not noted for a call that was done, or a read', async () => {
		sentForApproval.set(null);
		const actor = {
			run: vi.fn().mockResolvedValue('{"ok":true,"proposal":"P-1"}'),
			read: vi.fn().mockResolvedValue(filed(confirmLink))
		};
		await on(actor).run('proposal_vote', { proposal: 'P-1' });
		await on(actor).read('action_get', {});
		expect(get(sentForApproval)).toBeNull();
	});

	it.each(['javascript:alert(1)', 'https://evil.example/delegations/confirm/AC-3', '//evil.example/delegations/confirm/AC-3'])(
		'is not noted when its link is not a confirm page of this realm: %s',
		async (link) => {
			sentForApproval.set(null);
			const actor = { run: vi.fn().mockResolvedValue(filed(link)) };
			await on(actor).run('payment_account_add', { on_behalf_of: 'alice', address: 'a' });
			expect(get(sentForApproval)).toBeNull();
		}
	);
});
