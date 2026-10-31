import { describe, expect, it, vi } from 'vitest';
import { IDL } from '@dfinity/candid';

const poll = vi.fn();
vi.mock('$lib/canisters', () => ({ backend: {}, quarterBackend: {} }));
vi.mock('@dfinity/agent', () => ({
	Actor: { agentOf: () => 'agent', canisterIdOf: () => 'realm-id' },
	pollForResponse: (...args: unknown[]) => poll(...args),
	requestIdOf: () => 'request-id'
}));

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
