import { Cbor } from '@dfinity/agent';
import { Ed25519KeyIdentity } from '@dfinity/identity';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AI_URL, SIGNED_OUT_MESSAGE, createAiSession, signProof } from './ai-session';

const identity = Ed25519KeyIdentity.generate(new Uint8Array(32).fill(7));
const NONCE_HEX = '00ff10aa'.repeat(8);

function decodeProof(proof: string) {
	return Cbor.decode(Uint8Array.from(atob(proof), (c) => c.charCodeAt(0))) as any;
}

/** A fake service: `challenge` hands out NONCE_HEX, `session` answers with a token that lasts `expiresIn` s. */
function fakeService(expiresIn = 3600) {
	const calls: { url: string; body?: any }[] = [];
	vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
		const body = init?.body ? JSON.parse(String(init.body)) : undefined;
		calls.push({ url, body });
		const reply = url.endsWith('/challenge')
			? { nonce: NONCE_HEX }
			: { token: `token-${calls.length}`, expires_in: expiresIn };
		return { ok: true, status: 200, json: async () => reply };
	});
	return calls;
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

describe('signProof', () => {
	it('signs a read_state request for the path "time" carrying the nonce', async () => {
		const nonce = Uint8Array.from([1, 2, 3, 4]);

		const envelope = decodeProof(await signProof(identity, nonce));

		expect(envelope.content.request_type).toBe('read_state');
		expect(envelope.content.paths).toEqual([[new TextEncoder().encode('time')]]);
		expect(Array.from(envelope.content.nonce)).toEqual([1, 2, 3, 4]);
		expect(envelope.sender_sig).toBeDefined();
	});
});

describe('createAiSession', () => {
	it('signs the nonce the AI service issued, whatever its caller passes', async () => {
		const calls = fakeService();
		const session = createAiSession(async () => identity);

		await (session as (options?: unknown) => Promise<unknown>)({
			nonce: 'ffff',
			url: 'https://evil.example/'
		});

		expect(calls.map((call) => call.url)).toEqual([
			`${AI_URL}api/auth/challenge`,
			`${AI_URL}api/auth/session`
		]);
		const { nonce, proof } = calls[1].body;
		expect(nonce).toBe(NONCE_HEX);
		const signed = decodeProof(proof).content.nonce;
		expect(Buffer.from(signed).toString('hex')).toBe(NONCE_HEX);
	});

	it('reuses the token until it nearly expires, then opens a new session', async () => {
		vi.useFakeTimers();
		const calls = fakeService(3600);
		const session = createAiSession(async () => identity);

		const first = await session();
		expect(await session()).toBe(first);
		expect(calls).toHaveLength(2);

		vi.advanceTimersByTime(3600 * 1000 - 30_000);
		const renewed = await session();
		expect(renewed.token).not.toBe(first.token);
		expect(calls).toHaveLength(4);
	});

	it('opens a new session on request, after the service rejected the token', async () => {
		const calls = fakeService();
		const session = createAiSession(async () => identity);

		await session();
		await session({ renew: true });

		expect(calls).toHaveLength(4);
	});

	it('does not hand one user the token of another', async () => {
		const calls = fakeService();
		let current = identity;
		const session = createAiSession(async () => current);

		await session();
		current = Ed25519KeyIdentity.generate(new Uint8Array(32).fill(8));
		await session();

		expect(calls).toHaveLength(4);
	});

	it('says to sign in when there is no identity', async () => {
		const calls = fakeService();
		const session = createAiSession(async () => null);

		await expect(session()).rejects.toThrow(SIGNED_OUT_MESSAGE);
		expect(calls).toHaveLength(0);
	});

	it('reports the service error when it refuses the proof', async () => {
		vi.stubGlobal('fetch', async (url: string) => ({
			ok: !url.endsWith('/session'),
			status: 401,
			json: async () =>
				url.endsWith('/session') ? { error: 'proof expired' } : { nonce: NONCE_HEX }
		}));
		const session = createAiSession(async () => identity);

		await expect(session()).rejects.toThrow('proof expired');
	});
});
