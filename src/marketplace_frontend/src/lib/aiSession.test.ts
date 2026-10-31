import assert from "node:assert/strict";
import { afterEach, describe, it, mock } from "node:test";
import { Cbor } from "@dfinity/agent";
import { Ed25519KeyIdentity } from "@dfinity/identity";
import {
  AI_URL,
  SignedOutError,
  createAiSession,
  fetchAsUser,
  signProof,
} from "./aiSession.ts";

const identity = Ed25519KeyIdentity.generate(new Uint8Array(32).fill(7));
const NONCE_HEX = "00ff10aa".repeat(8);

function decodeProof(proof: string): any {
  return Cbor.decode(Uint8Array.from(atob(proof), (c) => c.charCodeAt(0)));
}

type Call = { url: string; init?: RequestInit };

/** A fake service: `/challenge` hands out NONCE_HEX, `/session` a token that lasts `expiresIn` s, others `status`. */
function fakeService({ expiresIn = 3600, statuses = [200] as number[] } = {}) {
  const calls: Call[] = [];
  let other = 0;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const json = async () =>
      url.endsWith("/challenge")
        ? { nonce: NONCE_HEX }
        : { token: `token-${calls.length}`, expires_in: expiresIn };
    if (url.includes("/api/auth/")) return { ok: true, status: 200, json };
    const status = statuses[Math.min(other++, statuses.length - 1)];
    return { ok: status < 400, status, json: async () => ({}) };
  }) as typeof fetch;
  return calls;
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  mock.timers.reset();
});

describe("signProof", () => {
  it("signs a read_state request for the path 'time' carrying the nonce", async () => {
    const envelope = decodeProof(
      await signProof(identity, Uint8Array.from([1, 2, 3, 4])),
    );

    assert.equal(envelope.content.request_type, "read_state");
    assert.deepEqual(envelope.content.paths, [
      [new TextEncoder().encode("time")],
    ]);
    assert.deepEqual(Array.from(envelope.content.nonce), [1, 2, 3, 4]);
    assert.ok(envelope.sender_sig);
  });
});

describe("createAiSession", () => {
  it("signs the nonce the service issued", async () => {
    const calls = fakeService();

    await createAiSession(async () => identity)();

    assert.deepEqual(
      calls.map((call) => call.url),
      [`${AI_URL}api/auth/challenge`, `${AI_URL}api/auth/session`],
    );
    const { nonce, proof } = JSON.parse(String(calls[1].init?.body));
    assert.equal(nonce, NONCE_HEX);
    assert.equal(
      Buffer.from(decodeProof(proof).content.nonce).toString("hex"),
      NONCE_HEX,
    );
  });

  it("reuses the token until it nearly expires, then opens a new session", async () => {
    mock.timers.enable({ apis: ["Date"] });
    const calls = fakeService({ expiresIn: 3600 });
    const session = createAiSession(async () => identity);

    const first = await session();
    assert.equal(await session(), first);
    assert.equal(calls.length, 2);

    mock.timers.tick(3600 * 1000 - 30_000);
    assert.notEqual((await session()).token, first.token);
    assert.equal(calls.length, 4);
  });

  it("opens a new session on request, and for another user", async () => {
    const calls = fakeService();
    let current = identity;
    const session = createAiSession(async () => current);

    await session();
    await session({ renew: true });
    assert.equal(calls.length, 4);

    current = Ed25519KeyIdentity.generate(new Uint8Array(32).fill(8));
    await session();
    assert.equal(calls.length, 6);
  });

  it("says to sign in when there is no identity", async () => {
    const calls = fakeService();

    await assert.rejects(createAiSession(async () => null)(), SignedOutError);
    assert.equal(calls.length, 0);
  });

  it("reports the service's error when it refuses the proof", async () => {
    globalThis.fetch = (async (url: string) => ({
      ok: !url.endsWith("/session"),
      status: 401,
      json: async () =>
        url.endsWith("/session")
          ? { error: "proof expired" }
          : { nonce: NONCE_HEX },
    })) as typeof fetch;

    await assert.rejects(
      createAiSession(async () => identity)(),
      /proof expired/,
    );
  });
});

describe("fetchAsUser", () => {
  const session = async ({ renew = false } = {}) => ({
    token: renew ? "fresh" : "stale",
    expiresAt: 0,
  });

  it("sends the Bearer token", async () => {
    const calls = fakeService();

    await fetchAsUser(session, `${AI_URL}api/ask`, {
      method: "POST",
      headers: { Accept: "text/plain" },
    });

    assert.deepEqual(calls[0].init?.headers, {
      Accept: "text/plain",
      Authorization: "Bearer stale",
    });
  });

  it("retries once with a fresh session on 401", async () => {
    const calls = fakeService({ statuses: [401, 200] });

    const response = await fetchAsUser(session, `${AI_URL}api/ask`);

    assert.equal(response.status, 200);
    assert.deepEqual(
      calls.map(
        (call) => (call.init?.headers as Record<string, string>).Authorization,
      ),
      ["Bearer stale", "Bearer fresh"],
    );
  });

  it("gives up after that one retry", async () => {
    const calls = fakeService({ statuses: [401] });

    assert.equal((await fetchAsUser(session, `${AI_URL}api/ask`)).status, 401);
    assert.equal(calls.length, 2);
  });
});
