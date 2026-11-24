// @vitest-environment jsdom
import { createHash } from 'node:crypto';
import { describe, expect, it, vi } from 'vitest';
import { BRIDGE_PROTOCOL_VERSION, BRIDGE_SOURCE, createBridgeServer, validateExtToHostMessage } from '@realmsgos/extension-bridge';
import { saveSandboxFile } from './extension-bridge-host';

const ALICE = '2eqns-rmzes-7npxw-dxpw2-qdy2s-mw6ix-svdo2-oya7o-a6ldc-sqgwh-bqe';
const BOB = 'z32zf-ic72u-jhtqq-ojjqp-jblhx-dxxin-vqelt-ljmo4-ymwmh-gcnzo-hae';

function sandbox(capabilities: string[], handlers: Record<string, any> = {}) {
	const iframe = document.createElement('iframe');
	document.body.appendChild(iframe);
	const replies: any[] = [];
	vi.spyOn(iframe.contentWindow!, 'postMessage').mockImplementation((msg: any) => void replies.push(msg));
	createBridgeServer(iframe, { extensionId: 'banking', capabilities, getState: () => ({}) as any, ...handlers });
	const send = (data: object) =>
		window.dispatchEvent(new MessageEvent('message', { data: { source: BRIDGE_SOURCE, ...data }, source: iframe.contentWindow }));
	send({ kind: 'hello', sdkVersion: BRIDGE_PROTOCOL_VERSION });
	return { replies, send, reply: (id: number) => replies.find((r) => r.id === id) };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('capability vault: the host seals and opens records, the member\'s key stays in the host', () => {
	it('is refused to a screen that does not declare it', async () => {
		const onVaultSeal = vi.fn();
		const { send, reply } = sandbox(['notify'], { onVaultSeal });
		send({ kind: 'vault_seal', id: 1, recipients: [ALICE], data: { iban: 'CH93 0076 2011 6238 5295 7' } });
		await settle();
		expect(onVaultSeal).not.toHaveBeenCalled();
		expect(reply(1)).toMatchObject({ kind: 'error', error: { code: 'denied' } });
	});

	it('seals for each recipient once, and returns what the host sealed', async () => {
		const onVaultSeal = vi.fn(async () => ({ ciphertext: 'enc:v=2:x', keys: { [ALICE]: 'aa', [BOB]: 'bb' } }));
		const { send, reply } = sandbox(['vault'], { onVaultSeal });
		send({ kind: 'vault_seal', id: 1, recipients: [ALICE, BOB, ALICE], data: { n: 1 } });
		await settle();
		expect(onVaultSeal).toHaveBeenCalledWith([ALICE, BOB], { n: 1 });
		expect(reply(1)).toMatchObject({ kind: 'call_result', result: { ciphertext: 'enc:v=2:x' } });
	});

	it('opens a record with the key wrapped for the member, and takes nothing that is not a key', async () => {
		const onVaultOpen = vi.fn(async () => ({ iban: 'CH93 0076 2011 6238 5295 7' }));
		const { send, reply } = sandbox(['vault'], { onVaultOpen });
		send({ kind: 'vault_open', id: 1, wrappedKey: 'ab12', ciphertext: 'enc:v=2:x' });
		send({ kind: 'vault_open', id: 2, wrappedKey: 'not a key', ciphertext: 'enc:v=2:x' });
		await settle();
		expect(onVaultOpen).toHaveBeenCalledTimes(1);
		expect(reply(1)).toMatchObject({ kind: 'call_result', result: { iban: 'CH93 0076 2011 6238 5295 7' } });
		expect(reply(2)).toMatchObject({ kind: 'error', error: { code: 'bad_request' } });
	});

	it('takes principals only as recipients', () => {
		const out = validateExtToHostMessage({ source: BRIDGE_SOURCE, kind: 'vault_seal', id: 1, recipients: ['<script>'], data: 1 });
		expect(out).toMatchObject({ ok: false, responseId: 1 });
	});
});

describe('capability download: the realm decides whether a file leaves', () => {
	const XML = '<Document>pain.001</Document>';
	const SHA = createHash('sha256').update(XML).digest('hex');

	it('is refused to a screen that does not declare it', async () => {
		const onSaveFile = vi.fn();
		const { send, reply } = sandbox(['vault'], { onSaveFile });
		send({ kind: 'save_file', id: 1, name: 'order.xml', mimeType: 'application/xml', content: XML });
		await settle();
		expect(onSaveFile).not.toHaveBeenCalled();
		expect(reply(1)).toMatchObject({ kind: 'error', error: { code: 'denied' } });
	});

	it('takes a plain file name and a listed type only', () => {
		for (const bad of [{ name: '../order.xml' }, { name: 'a/b.xml' }, { mimeType: 'text/html' }]) {
			const out = validateExtToHostMessage({
				source: BRIDGE_SOURCE, kind: 'save_file', id: 1, name: 'order.xml', mimeType: 'application/xml', content: XML, ...bad,
			});
			expect(out).toMatchObject({ ok: false, responseId: 1 });
		}
	});

	it('asks the extension\'s backend about these exact bytes, and saves them only when it allows', async () => {
		const callRead = vi.fn(async () => ({ ok: true, allowed: true }));
		const deliverFile = vi.fn();
		const saved = await saveSandboxFile({ callRead, deliverFile }, { name: 'order.xml', mimeType: 'application/xml', content: XML, base64: false });
		expect(callRead).toHaveBeenCalledWith('download_allowed', { name: 'order.xml', sha256: SHA, size: XML.length });
		expect(saved).toEqual({ sha256: SHA, size: XML.length });
		expect(deliverFile).toHaveBeenCalledTimes(1);
		expect(new TextDecoder().decode(deliverFile.mock.calls[0][2])).toBe(XML);
	});

	it('saves nothing when the backend does not allow the file, or cannot be asked', async () => {
		for (const callRead of [
			vi.fn(async () => ({ ok: true, allowed: false, reason: 'no commitment for this file' })),
			vi.fn(async () => ({ ok: false, error: 'no function download_allowed' })),
			vi.fn(async () => { throw new Error('denied'); }),
		]) {
			const deliverFile = vi.fn();
			await expect(
				saveSandboxFile({ callRead, deliverFile }, { name: 'order.xml', mimeType: 'application/xml', content: XML, base64: false }),
			).rejects.toMatchObject({ code: 'denied' });
			expect(deliverFile).not.toHaveBeenCalled();
		}
	});

	it('hashes the decoded bytes of a base64 file', async () => {
		const bytes = Buffer.from([0x25, 0x50, 0x44, 0x46, 0x00, 0xff]);
		const callRead = vi.fn(async () => ({ ok: true, allowed: true }));
		const saved = await saveSandboxFile(
			{ callRead, deliverFile: vi.fn() },
			{ name: 'bill.pdf', mimeType: 'application/pdf', content: bytes.toString('base64'), base64: true },
		);
		expect(saved.sha256).toBe(createHash('sha256').update(bytes).digest('hex'));
	});
});
