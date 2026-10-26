/**
 * Consent-based private-data sharing via vetKeys IBE.
 *
 * The member's private data is encrypted with a random per-save Data Encryption
 * Key (DEK) using AES-256-GCM. The DEK is then wrapped (IBE-encrypted) once per
 * authorized recipient and stored as a `KeyEnvelope` at scope
 * `user:<member>:private`.
 *
 * A recipient (the member themselves, or a consented admin) decrypts by:
 *   1. deriving their own *sharing* vetKey (only they can — vetKD binds the
 *      derivation `input` to their principal via `ic.caller()`),
 *   2. IBE-decrypting their envelope to recover the DEK,
 *   3. AES-GCM decrypting the ciphertext.
 *
 * The plaintext DEK and vetKey never leave the browser; the canister only ever
 * stores ciphertext and opaque wrapped keys.
 *
 * Key derivation scheme (performance):
 * Instead of fetching a separate vetKD public key per recipient (one
 * inter-canister `vetkd_public_key` call each), everyone shares a single *root*
 * public key. A recipient is distinguished by the IBE **identity** — their
 * principal text. The browser therefore fetches the root key once and wraps the
 * DEK for every recipient locally, with zero extra management calls. Security is
 * unchanged: the matching sharing vetKey for identity `P` can only be derived by
 * the principal `P` itself (the backend forces `input = ic.caller()`).
 */

import {
	TransportSecretKey,
	DerivedPublicKey,
	EncryptedVetKey,
	IbeCiphertext,
	IbeIdentity,
	IbeSeed,
	type VetKey
} from '@dfinity/vetkeys';
// @ts-ignore
import { quarterBackend } from '$lib/canisters';
import { read, run, quarter, must } from '$lib/realm';

/** The realm client for ``backend``, the actor callers pass: ``quarterBackend`` means the active quarter. */
export function realmFor(backend: unknown) {
	return backend === quarterBackend ? quarter : { read, run };
}

// ---------------------------------------------------------------------------
// Hex helpers
// ---------------------------------------------------------------------------

function hexToBytes(hex: string): Uint8Array {
	const h = hex.replace(/\s/g, '');
	const bytes = new Uint8Array(h.length / 2);
	for (let i = 0; i < bytes.length; i++) {
		bytes[i] = parseInt(h.substring(i * 2, i * 2 + 2), 16);
	}
	return bytes;
}

function bytesToHex(bytes: Uint8Array): string {
	return Array.from(bytes)
		.map((b) => b.toString(16).padStart(2, '0'))
		.join('');
}

/**
 * The IBE identity / vetKD derivation input for a principal: the UTF-8 bytes of
 * its textual principal. The backend uses the same encoding
 * (`caller_principal.encode()`) for the `input`, keeping wrap and unwrap aligned.
 */
export function identityBytesFor(principalText: string): Uint8Array {
	return new TextEncoder().encode(principalText);
}

// ---------------------------------------------------------------------------
// VetKey derivation (shared root context + per-principal identity)
// ---------------------------------------------------------------------------

/** Fetch the shared root derived public key (one call, cacheable). */
export async function getSharingRootPublicKey(backend: any): Promise<DerivedPublicKey> {
	const { public_key } = await must(realmFor(backend).run('crypto_public_key'));
	return DerivedPublicKey.deserialize(hexToBytes(public_key));
}

/**
 * Derive the caller's *sharing* vetKey (bound to their own principal identity)
 * under the shared root context, for IBE-decrypting wrapped DEKs addressed to
 * them. `myPrincipal` must be the caller's own principal text.
 */
export async function deriveMySharingVetKey(
	backend: any,
	myPrincipal: string,
	rootDpk?: DerivedPublicKey
): Promise<{ vetKey: VetKey; dpk: DerivedPublicKey }> {
	const dpk = rootDpk ?? (await getSharingRootPublicKey(backend));

	const tsk = TransportSecretKey.random();
	const tpkHex = bytesToHex(tsk.publicKeyBytes());

	const { encrypted_key } = await must(
		realmFor(backend).run('crypto_derive_key', { transport_public_key: tpkHex })
	);
	const encryptedVetKey = EncryptedVetKey.deserialize(hexToBytes(encrypted_key));
	const identity = identityBytesFor(myPrincipal);
	const vetKey = encryptedVetKey.decryptAndVerify(tsk, dpk, identity);
	return { vetKey, dpk };
}

// ---------------------------------------------------------------------------
// DEK wrap / unwrap (IBE)
// ---------------------------------------------------------------------------

/** Generate a fresh 32-byte Data Encryption Key. */
export function randomDek(): Uint8Array {
	return crypto.getRandomValues(new Uint8Array(32));
}

/**
 * IBE-wrap a DEK for a recipient principal under the shared root public key,
 * using the recipient's principal as the IBE identity. Fully client-side — no
 * network call. Returns the serialized ciphertext as hex.
 */
export function wrapDekForIdentity(
	rootDpk: DerivedPublicKey,
	recipientPrincipal: string,
	dek: Uint8Array
): string {
	const identity = IbeIdentity.fromBytes(identityBytesFor(recipientPrincipal));
	const ct = IbeCiphertext.encrypt(rootDpk, identity, dek, IbeSeed.random());
	return bytesToHex(ct.serialize());
}

/**
 * Strip the toolkit envelope wrapper (`env:v=2:k=<hex>`) if present,
 * returning the raw wrapped-DEK hex. Mirrors `decode_envelope` in
 * ic_basilisk_toolkit.crypto, since the canister stores envelopes in this
 * format via CryptoService.grant_access.
 */
export function decodeEnvelope(stored: string): string {
	const prefix = 'env:v=2:k=';
	return stored.startsWith(prefix) ? stored.slice(prefix.length) : stored;
}

/** IBE-unwrap a wrapped DEK using the holder's vetKey. Accepts the toolkit's
 * `env:v=2:k=<hex>` envelope format or raw hex. */
export function unwrapDek(vetKey: VetKey, wrappedDek: string): Uint8Array {
	const ct = IbeCiphertext.deserialize(hexToBytes(decodeEnvelope(wrappedDek)));
	return ct.decrypt(vetKey);
}

// ---------------------------------------------------------------------------
// AES-256-GCM with an explicit DEK
// ---------------------------------------------------------------------------

async function importDek(dek: Uint8Array): Promise<CryptoKey> {
	return crypto.subtle.importKey('raw', dek as BufferSource, { name: 'AES-GCM' }, false, [
		'encrypt',
		'decrypt'
	]);
}

/** Encrypt a UTF-8 string with a DEK. Output: `enc:v=2:iv=<hex>:d=<hex>`. */
export async function aesGcmEncryptWithDek(dek: Uint8Array, plaintext: string): Promise<string> {
	const key = await importDek(dek);
	const iv = crypto.getRandomValues(new Uint8Array(12));
	const cipherBuf = await crypto.subtle.encrypt(
		{ name: 'AES-GCM', iv: iv as BufferSource },
		key,
		new TextEncoder().encode(plaintext) as BufferSource
	);
	return `enc:v=2:iv=${bytesToHex(iv)}:d=${bytesToHex(new Uint8Array(cipherBuf))}`;
}

/** Decrypt an `enc:v=2:...` ciphertext (or legacy raw hex) with a DEK. */
export async function aesGcmDecryptWithDek(dek: Uint8Array, ciphertext: string): Promise<string> {
	const key = await importDek(dek);
	let iv: Uint8Array;
	let data: Uint8Array;

	if (ciphertext.startsWith('enc:v=2:')) {
		const parts: Record<string, string> = {};
		for (const seg of ciphertext.slice('enc:v=2:'.length).split(':')) {
			const eq = seg.indexOf('=');
			if (eq > 0) parts[seg.slice(0, eq)] = seg.slice(eq + 1);
		}
		if (!parts.iv || !parts.d) throw new Error('Invalid enc:v=2 format');
		iv = hexToBytes(parts.iv);
		data = hexToBytes(parts.d);
	} else {
		const combined = hexToBytes(ciphertext);
		iv = combined.slice(0, 12);
		data = combined.slice(12);
	}

	const plainBuf = await crypto.subtle.decrypt(
		{ name: 'AES-GCM', iv: iv as BufferSource },
		key,
		data as BufferSource
	);
	return new TextDecoder().decode(plainBuf);
}

// ---------------------------------------------------------------------------
// Scope builders
// ---------------------------------------------------------------------------
//
// A scope names an encrypted payload and (server-side) who may manage read
// access to it. Keep these in sync with `managers` in the backend's api/crypto.py.

/** `user:<principal>:<name>` — owned by the principal (self-service). */
export const userScope = (principal: string, name = 'private') => `user:${principal}:${name}`;
/** `dept:<department>:<name>` — managed by the department's head and staff. */
export const deptScope = (department: string, name: string) => `dept:${department}:${name}`;
/** `realm:<name>` — managed by the root department's staff. */
export const realmScope = (name: string) => `realm:${name}`;

// ---------------------------------------------------------------------------
// High-level orchestration (generic over scope — reusable for any payload)
// ---------------------------------------------------------------------------

export interface SharePlan {
	/** Ciphertext to persist wherever the payload lives (a field, an entity…). */
	ciphertext: string;
	/**
	 * Wrapped DEK per recipient principal, ready to upsert in a single
	 * `crypto_grant` call.
	 */
	wrappedDeks: Record<string, string>;
}

/**
 * Encrypt `data` with a fresh DEK and wrap that DEK for every principal in
 * `recipients` (deduplicated). The shared root public key is fetched once; all
 * wraps are local (no per-recipient network call).
 *
 * `recipients` should include everyone who must be able to read the payload —
 * typically the owner plus any consented parties. The caller is responsible for
 * persisting `ciphertext` (e.g. `private_data_update`, or a document field)
 * and for calling {@link grantScopeData} with `wrappedDeks`.
 */
export async function buildSharePlan(
	backend: any,
	recipients: string[],
	data: unknown
): Promise<SharePlan> {
	const dek = randomDek();
	const ciphertext = await aesGcmEncryptWithDek(dek, JSON.stringify(data));

	const rootDpk = await getSharingRootPublicKey(backend);

	const wrappedDeks: Record<string, string> = {};
	for (const r of recipients) {
		if (!r || wrappedDeks[r]) continue;
		wrappedDeks[r] = wrapDekForIdentity(rootDpk, r, dek);
	}

	return { ciphertext, wrappedDeks };
}

/**
 * Persist a share plan's access grants for `scope` in (at most) two
 * calls: one grant for all current recipients, and one revoke for principals
 * who previously had access but no longer do.
 *
 * @param previousRecipients principals that currently hold access (to diff against)
 * @param keep principals that must never be revoked (e.g. the owner)
 * @returns the principals now granted access (the keys of `wrappedDeks`)
 */
export async function grantScopeData(
	backend: any,
	scope: string,
	wrappedDeks: Record<string, string>,
	opts: { previousRecipients?: string[]; keep?: string[] } = {}
): Promise<string[]> {
	const realm = realmFor(backend);
	const granted = Object.keys(wrappedDeks);
	await must(realm.run('crypto_grant', { scope, keys: wrappedDeks }));

	const grantedSet = new Set(granted);
	const keepSet = new Set(opts.keep ?? []);
	const toRevoke = (opts.previousRecipients ?? []).filter(
		(p) => !grantedSet.has(p) && !keepSet.has(p)
	);
	if (toRevoke.length > 0) {
		try {
			await must(realm.run('crypto_revoke', { scope, principals: toRevoke }));
		} catch (e) {
			console.warn('[sharing] batch revoke failed:', e);
		}
	}
	return granted;
}

/**
 * Decrypt a scope's payload for the calling principal: fetch the DEK envelope
 * wrapped for them, derive their sharing vetKey, unwrap the DEK, and AES-decrypt
 * `ciphertext`. Returns null if the caller has no envelope or decryption fails.
 */
export async function decryptScopeData<T = Record<string, string>>(
	backend: any,
	scope: string,
	myPrincipal: string,
	ciphertext: string
): Promise<T | null> {
	if (!ciphertext) return null;
	try {
		const envelope = await realmFor(backend).read('crypto_envelope', { scope });
		const wrapped = envelope.ok && envelope.wrapped_dek;
		if (wrapped) {
			const { vetKey } = await deriveMySharingVetKey(backend, myPrincipal);
			const dek = unwrapDek(vetKey, wrapped);
			const plaintext = await aesGcmDecryptWithDek(dek, ciphertext);
			return JSON.parse(plaintext) as T;
		}
	} catch (e) {
		console.warn('[sharing] scope decrypt failed:', e);
	}
	return null;
}
