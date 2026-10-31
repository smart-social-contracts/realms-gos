/**
 * Sign-in to the AI assistant service (ggg-ai).
 *
 * The service issues a session token only to a user who proves their principal: a signed
 * `read_state` request for the path `time`, made with the user's identity, sent to the service
 * instead of the IC. The token goes in `Authorization: Bearer` on every other call.
 */
import {
  Cbor,
  Expiry,
  type HttpAgentRequest,
  type Identity,
} from "@dfinity/agent";

export const AI_URL = "https://geister-api.realmsgos.dev/";

/** Renew a token this long before it expires, so a request never starts with a stale one. */
const REFRESH_MARGIN_MS = 60_000;

export class SignedOutError extends Error {
  constructor() {
    super("Sign in to use the AI assistant.");
  }
}

export type AiSession = {
  token: string;
  /** Milliseconds since the epoch. */
  expiresAt: number;
};

/** `renew`: drop the cached token, the service rejected it. */
export type SessionFn = (options?: { renew?: boolean }) => Promise<AiSession>;

const hexToBytes = (hex: string): Uint8Array =>
  Uint8Array.from(hex.match(/../g) ?? [], (byte) => parseInt(byte, 16));

/** Base64 of the CBOR envelope: the identity's signature over a read_state request carrying `nonce`. */
export async function signProof(
  identity: Identity,
  nonce: Uint8Array,
): Promise<string> {
  const content = {
    request_type: "read_state",
    sender: identity.getPrincipal(),
    paths: [[new TextEncoder().encode("time")]], // exactly this path: the service rejects any other
    ingress_expiry: Expiry.fromDeltaInMilliseconds(4 * 60 * 1000), // the IC accepts at most 5 minutes
    nonce,
  };
  // The envelope is built by hand: the IC's own agent would send it to a replica, not to us.
  const request = {
    endpoint: "read_state",
    body: content,
  } as unknown as HttpAgentRequest;
  const { body } = (await identity.transformRequest(request)) as {
    body: unknown;
  };
  return btoa(String.fromCharCode(...Cbor.encode(body)));
}

async function fetchJson(url: string, init?: RequestInit): Promise<any> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      body.error || `The AI assistant is unavailable (${response.status}).`,
    );
  return body;
}

async function openSession(identity: Identity): Promise<AiSession> {
  const { nonce } = await fetchJson(`${AI_URL}api/auth/challenge`);
  const proof = await signProof(identity, hexToBytes(nonce));
  const { token, expires_in } = await fetchJson(`${AI_URL}api/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nonce, proof }),
  });
  return { token, expiresAt: Date.now() + expires_in * 1000 };
}

/** A session function that opens a session on first use and reuses it until it nearly expires. */
export function createAiSession(
  getIdentity: () => Promise<Identity | null>,
): SessionFn {
  let cached: { principal: string; session: AiSession } | null = null;

  return async ({ renew = false } = {}) => {
    const identity = await getIdentity();
    if (!identity) {
      cached = null;
      throw new SignedOutError();
    }
    const principal = identity.getPrincipal().toText();
    if (
      !renew &&
      cached?.principal === principal &&
      cached.session.expiresAt - REFRESH_MARGIN_MS > Date.now()
    ) {
      return cached.session;
    }
    cached = { principal, session: await openSession(identity) };
    return cached.session;
  };
}

/** `fetch` with the session's Bearer token; one retry with a fresh session if the service says 401. */
export async function fetchAsUser(
  session: SessionFn,
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const send = async (options: { renew?: boolean }) => {
    const { token } = await session(options);
    return fetch(url, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${token}` },
    });
  };
  const response = await send({});
  return response.status === 401 ? send({ renew: true }) : response;
}
