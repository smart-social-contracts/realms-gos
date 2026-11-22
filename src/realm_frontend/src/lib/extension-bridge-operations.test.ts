// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { BRIDGE_PROTOCOL_VERSION, BRIDGE_SOURCE, createBridgeServer } from '@realmsgos/extension-bridge';

function sandbox(onCallExtension = vi.fn(async () => ({ ok: true }))) {
	const iframe = document.createElement('iframe');
	document.body.appendChild(iframe);
	const replies: any[] = [];
	vi.spyOn(iframe.contentWindow!, 'postMessage').mockImplementation((msg: any) => void replies.push(msg));
	createBridgeServer(iframe, {
		extensionId: 'board',
		capabilities: [],
		onCallExtension,
		getState: () => ({}) as any
	});
	const send = (data: object) =>
		window.dispatchEvent(new MessageEvent('message', { data: { source: BRIDGE_SOURCE, ...data }, source: iframe.contentWindow }));
	send({ kind: 'hello', sdkVersion: BRIDGE_PROTOCOL_VERSION });
	return { replies, send, onCallExtension };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('a sandboxed screen reaches the realm only through its own backend', () => {
	it('calls a backend function as a query when it reads, and as an update when it runs', async () => {
		const { replies, send, onCallExtension } = sandbox();
		send({ kind: 'call_extension', id: 1, fn: 'treasury', args: { count: 5 }, read: true });
		send({ kind: 'call_extension', id: 2, fn: 'pay', args: { amount: 10 } });
		await settle();
		expect(onCallExtension).toHaveBeenNthCalledWith(1, 'treasury', { count: 5 }, true);
		expect(onCallExtension).toHaveBeenNthCalledWith(2, 'pay', { amount: 10 }, false);
		expect(replies.filter((r) => r.kind === 'call_result').map((r) => r.id)).toEqual([1, 2]);
	});

	it('has no way to call a realm operation directly', async () => {
		const { replies, send, onCallExtension } = sandbox();
		send({ kind: 'call_operation', id: 1, name: 'delegation_grant', args: { delegate: 'mallory' }, read: false });
		await settle();
		expect(onCallExtension).not.toHaveBeenCalled();
		const [reply] = replies.filter((r) => r.id === 1);
		expect(reply.kind).toBe('error');
		expect(reply.error.message).toContain('calls only its own backend');
	});
});
