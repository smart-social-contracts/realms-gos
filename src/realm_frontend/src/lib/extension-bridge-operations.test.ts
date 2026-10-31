// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { BRIDGE_PROTOCOL_VERSION, BRIDGE_SOURCE, createBridgeServer } from '@realmsgos/extension-bridge';

function sandbox(operations: string[], onCallOperation = vi.fn(async () => ({ ok: true }))) {
	const iframe = document.createElement('iframe');
	document.body.appendChild(iframe);
	const replies: any[] = [];
	vi.spyOn(iframe.contentWindow!, 'postMessage').mockImplementation((msg: any) => void replies.push(msg));
	createBridgeServer(iframe, {
		extensionId: 'board',
		capabilities: [],
		operations,
		onCallOperation,
		getState: () => ({}) as any
	});
	const send = (data: object) =>
		window.dispatchEvent(new MessageEvent('message', { data: { source: BRIDGE_SOURCE, ...data }, source: iframe.contentWindow }));
	send({ kind: 'hello', sdkVersion: BRIDGE_PROTOCOL_VERSION });
	return { replies, send, onCallOperation };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('call_operation', () => {
	it('reaches only the operations the installed manifest declares', async () => {
		const { replies, send, onCallOperation } = sandbox(['proposal_list']);
		send({ kind: 'call_operation', id: 1, name: 'proposal_list', args: { count: 5 }, read: true });
		send({ kind: 'call_operation', id: 2, name: 'delegation_grant', args: { delegate: 'mallory' }, read: false });
		await settle();
		expect(onCallOperation).toHaveBeenCalledTimes(1);
		expect(onCallOperation).toHaveBeenCalledWith('proposal_list', { count: 5 }, true);
		expect(replies.find((m) => m.id === 1)).toMatchObject({ kind: 'call_result', result: { ok: true } });
		expect(replies.find((m) => m.id === 2)).toMatchObject({ kind: 'error', error: { code: 'denied' } });
	});

	it('refuses everything when the manifest declares no operations', async () => {
		const { replies, send, onCallOperation } = sandbox([]);
		send({ kind: 'call_operation', id: 1, name: 'me', read: true });
		await settle();
		expect(onCallOperation).not.toHaveBeenCalled();
		expect(replies.find((m) => m.id === 1)?.error.code).toBe('denied');
	});
});
