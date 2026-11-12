// Runs one function of the verify page (docs/verify/index.html) under Node, for tests/deployed/test_verify_page.py.
//
//   node verify_page.cjs <the page> <json>      json: {"fn": "verifyProof", "args": [...], "fetch": {"status": 200, "body": "<hex>"}}
//
// The page's script is run as it is, in a context of its own with Node's WebCrypto and no DOM. An argument or a result
// that is bytes is written as {"$hex": "..."}, and a function that fetches proofs by code as {"$fetcher": {code: proof}}.
// With "fetch" the page's requests are answered with that reply, and the
// request it made is returned beside the result.
const fs = require("fs");
const vm = require("vm");

const [pagePath, payload] = process.argv.slice(2);
const html = fs.readFileSync(pagePath, "utf8");
const script = html.match(/<script id="verify-script">([\s\S]*?)<\/script>/)[1];
const request = JSON.parse(payload);

let seen = null;
const sandbox = {
  module: { exports: {} }, crypto: globalThis.crypto, TextEncoder, TextDecoder, URLSearchParams, console, Date, BigInt,
  fetch: async (url, init) => {
    seen = { url, headers: init.headers, method: init.method, body: Buffer.from(init.body).toString("hex") };
    const reply = Buffer.from(request.fetch.body, "hex");
    return { ok: request.fetch.status === 200, status: request.fetch.status,
      arrayBuffer: async () => reply.buffer.slice(reply.byteOffset, reply.byteOffset + reply.length) };
  },
};
vm.createContext(sandbox);
vm.runInContext(script, sandbox);

const revive = (value) => {
  if (Array.isArray(value)) return value.map(revive);
  if (value && typeof value === "object") {
    if ("$hex" in value) return sandbox.module.exports.fromHex(value.$hex);
    // a fetcher of proofs by code, as the page's checkPrevious takes one: {"$fetcher": {code: proof}}
    if ("$fetcher" in value) return async (which) => {
      if (!value.$fetcher[which.code]) throw new Error("no such document");
      return value.$fetcher[which.code];
    };
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, revive(item)]));
  }
  return value;
};
const plain = (value) => {
  if (ArrayBuffer.isView(value)) return { $hex: Buffer.from(value.buffer, value.byteOffset, value.byteLength).toString("hex") };
  if (Array.isArray(value)) return value.map(plain);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, plain(item)]));
  return value;
};

(async () => {
  try {
    const result = await sandbox.module.exports[request.fn](...revive(request.args));
    process.stdout.write(JSON.stringify({ result: plain(result), request: seen }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: String(error.message || error), request: seen }));
  }
})();
