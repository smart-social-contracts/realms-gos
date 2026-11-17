// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * `assistant:prompt` carries a question and the text it is about up to the
 * portal. It must travel the same way the older messages do — over the
 * MessagePort the portal transferred — and never leave a realm that is not
 * embedded in a portal.
 */

/** A fresh module instance: portal-bridge keeps the port in module state. */
async function freshBridge() {
	vi.resetModules();
	return await import('./portal-bridge.ts');
}

/** Hand the realm a port the way the portal host does, and record what it posts. */
function handshake(): { sent: Array<Record<string, unknown>>; port: MessagePort } {
	const channel = new MessageChannel();
	const sent: Array<Record<string, unknown>> = [];
	channel.port1.onmessage = (event) => sent.push(event.data);
	channel.port1.start?.();
	window.dispatchEvent(
		new MessageEvent('message', {
			data: { type: 'bridge:init', version: '1' },
			ports: [channel.port2]
		})
	);
	return { sent, port: channel.port1 };
}

/** Messages are delivered on a macrotask in jsdom. */
const delivered = () => new Promise((resolve) => setTimeout(resolve, 0));

const prompt = {
	message: 'Explain eu/data_protection.py (layer eu, runs here)',
	autoSend: true,
	context: { text: 'def allow(): pass', languageId: 'python', label: 'eu/data_protection.py' }
};

beforeEach(() => {
	sessionStorage.setItem('realms:portal-embed', '1');
});

afterEach(() => {
	sessionStorage.clear();
});

describe('portalAssistantPrompt', () => {
	it('posts the question and its text on the port the portal transferred', async () => {
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();
		const { sent } = handshake();

		expect(bridge.portalAssistantPrompt(prompt)).toBe(true);
		await delivered();
		dispose();

		const posted = sent.find((msg) => msg.type === 'assistant:prompt');
		expect(posted?.payload).toEqual(prompt);
	});

	it('puts the text on the port only — never on a window message', async () => {
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();
		handshake();
		const onWindow: MessageEvent[] = [];
		const listener = (event: Event) => onWindow.push(event as MessageEvent);
		window.addEventListener('message', listener);

		bridge.portalAssistantPrompt(prompt);
		await delivered();
		window.removeEventListener('message', listener);
		dispose();

		expect(onWindow.map((event) => event.data?.type)).not.toContain('assistant:prompt');
		expect(JSON.stringify(onWindow.map((event) => event.data))).not.toContain('def allow');
	});

	it('ignores a bridge:init that carries no port, and holds the prompt', async () => {
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();
		const channel = new MessageChannel();
		const sent: Array<Record<string, unknown>> = [];
		channel.port1.onmessage = (event) => sent.push(event.data);
		// No `ports`, and a type the bridge does not know: neither adopts a port.
		window.dispatchEvent(new MessageEvent('message', { data: { type: 'bridge:init' } }));
		window.dispatchEvent(
			new MessageEvent('message', {
				data: { type: 'bridge:other' },
				ports: [channel.port2]
			})
		);

		expect(bridge.portalAssistantPrompt(prompt)).toBe(false);
		await delivered();
		dispose();

		expect(sent).toEqual([]);
	});

	it('delivers a prompt queued before the handshake, before assistant:open', async () => {
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();

		expect(bridge.portalAssistantPrompt(prompt)).toBe(false);
		expect(bridge.portalAssistantOpen()).toBe(false);
		const { sent } = handshake();
		await delivered();
		dispose();

		const types = sent.map((msg) => msg.type);
		expect(types).toContain('assistant:prompt');
		expect(types.indexOf('assistant:prompt')).toBeLessThan(types.indexOf('assistant:open'));
	});

	it('sends nothing at all when the realm is not embedded in a portal', async () => {
		sessionStorage.clear();
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();
		const { sent } = handshake();

		expect(bridge.portalAssistantPrompt(prompt)).toBe(false);
		await delivered();
		dispose();

		expect(sent).toEqual([]);
	});

	it('refuses a prompt with no question', async () => {
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();
		const { sent } = handshake();

		expect(bridge.portalAssistantPrompt({ message: '', autoSend: true })).toBe(false);
		await delivered();
		dispose();

		expect(sent.some((msg) => msg.type === 'assistant:prompt')).toBe(false);
	});
});

describe('portalFocusPush', () => {
	it('pushes only source, uri and label — the document text stays behind', async () => {
		const bridge = await freshBridge();
		const dispose = bridge.initPortalBridge();
		const { sent } = handshake();

		bridge.portalFocusPush({
			source: 'codex_viewer',
			uri: 'realms://codex_viewer/codex/eu-data_protection',
			label: 'eu/data_protection.py',
			snapshot: { languageId: 'python', range: { startLine: 1, endLine: 2 }, text: 'secret' }
		});
		await delivered();
		dispose();

		const posted = sent.find((msg) => msg.type === 'focus:push');
		expect(posted?.payload).toEqual({
			source: 'codex_viewer',
			uri: 'realms://codex_viewer/codex/eu-data_protection',
			label: 'eu/data_protection.py'
		});
		expect(JSON.stringify(posted)).not.toContain('secret');
	});
});
