#!/usr/bin/env node
// Browser check for a local realm (`realms local start` + `realms local setup`).
//
// Logs in without Internet Identity or a test build: the realm frontend
// accepts a delegation from a federation portal host through its portal bridge
// (src/realm_frontend/src/lib/portal-bridge.ts). This script plays that host:
// it serves a page on http://localhost that embeds the realm with ?portal=1,
// answers the bridge handshake, and signs delegations with a fresh Ed25519
// key, which `realms local add-admin` registers in the realm first.
//
// Then it opens /extensions/<id>, clicks the hello button inside the
// extension's sandboxed iframe and waits for the expected phrase.
//
//   node check.mjs --extension my_hello_world --expect "Hello, World!" --out ./artifacts

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { parseArgs } from 'node:util';

import { DelegationChain, Ed25519KeyIdentity } from '@dfinity/identity';
import { chromium } from 'playwright';

const { values: opts } = parseArgs({
  options: {
    extension: { type: 'string', default: 'my_hello_world' },
    expect: { type: 'string', default: 'Hello, World!' },
    out: { type: 'string', default: 'artifacts' },
    realms: { type: 'string', default: process.env.REALMS_BIN || 'realms' },
    headed: { type: 'boolean', default: false },
    timeout: { type: 'string', default: '180' },
    'keep-open': { type: 'string', default: '0' },
  },
});

const timeoutMs = Number(opts.timeout) * 1000;
const outDir = path.resolve(opts.out);
mkdirSync(outDir, { recursive: true });
const logLines = [];
const log = (line) => {
  const stamped = `[${new Date().toISOString()}] ${line}`;
  logLines.push(stamped);
  console.log(stamped);
};
const flushLog = () => writeFileSync(path.join(outDir, 'browser-check.log'), logLines.join('\n') + '\n');

function realms(...args) {
  return execFileSync(opts.realms, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

const status = JSON.parse(realms('local', 'status', '--json'));
if (!status.running) throw new Error('local realm network is not running');
if (status.launch !== 'completed') throw new Error(`realm setup not completed (launch: ${status.launch})`);
const realmUrl = status.url;
const { realm_backend: backendId, realm_frontend: frontendId } = status.canisters;

const user = Ed25519KeyIdentity.generate();
const userPrincipal = user.getPrincipal().toText();
log(`test user ${userPrincipal}`);
realms('local', 'add-admin', userPrincipal);
log('registered as realm admin');

const extPath = `/extensions/${opts.extension}`;
const hostHtml = `<!doctype html>
<html><head><meta charset="utf-8"><title>Realms portal (local check)</title>
<style>html,body{margin:0;height:100%}iframe{border:0;width:100%;height:100%}</style></head>
<body>
<iframe id="realm" src="${realmUrl}${extPath}?portal=1"></iframe>
<script>
  const realm = document.getElementById('realm');
  window.addEventListener('message', (ev) => {
    if (ev.source !== realm.contentWindow || ev.data?.type !== 'bridge:hello') return;
    const channel = new MessageChannel();
    channel.port1.onmessage = async (m) => {
      const msg = m.data || {};
      window.__bridgeLog?.(msg.type);
      if (msg.type === 'auth:request-delegation') {
        const signed = await window.__signDelegation(msg.payload.sessionPublicKeyDer);
        channel.port1.postMessage({
          type: 'auth:delegation',
          payload: { ...signed, backendCanisterId: ${JSON.stringify(backendId)} },
        });
      }
    };
    realm.contentWindow.postMessage({ type: 'bridge:init' }, '*', [channel.port2]);
    channel.port1.postMessage({
      type: 'config:realm',
      payload: {
        backendCanisterId: ${JSON.stringify(backendId)},
        frontendCanisterId: ${JSON.stringify(frontendId)},
        env: 'local',
        path: ${JSON.stringify(extPath)},
      },
    });
  });
</script>
</body></html>`;

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(hostHtml);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const hostUrl = `http://localhost:${server.address().port}/`;
log(`portal host ${hostUrl} embedding ${realmUrl}${extPath}?portal=1`);

const browser = await chromium.launch({ headless: !opts.headed });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on('console', (msg) => log(`console.${msg.type()}: ${msg.text()}`));
page.on('pageerror', (err) => log(`pageerror: ${err.message}`));

await page.exposeFunction('__bridgeLog', (type) => log(`bridge <- ${type}`));
await page.exposeFunction('__signDelegation', async (sessionPublicKeyDer) => {
  const der = new Uint8Array(sessionPublicKeyDer);
  const sessionKey = { toDer: () => der, rawKey: der, derKey: der };
  const expiresAt = Date.now() + 60 * 60 * 1000;
  const chain = await DelegationChain.create(user, sessionKey, new Date(expiresAt));
  log('bridge -> auth:delegation');
  return { delegation: chain.toJSON(), expiresAt };
});

let failed = null;
try {
  await page.goto(hostUrl);
  const realmFrame = page.frameLocator('#realm');
  const extFrame = realmFrame.frameLocator(`iframe[title="Extension ${opts.extension}"]`);
  const button = extFrame.getByTestId('hello-button').locator('button');
  await button.waitFor({ state: 'visible', timeout: timeoutMs });
  log('hello button visible');
  await page.screenshot({ path: path.join(outDir, 'before-click.png') });
  await button.click({ timeout: timeoutMs });
  log('hello button clicked');
  const result = extFrame.getByTestId('hello-result');
  await result.waitFor({ state: 'visible', timeout: timeoutMs });
  const deadline = Date.now() + timeoutMs;
  let text = '';
  while (Date.now() < deadline) {
    text = ((await result.textContent()) || '').trim();
    if (text === opts.expect) break;
    await page.waitForTimeout(250);
  }
  log(`hello result: ${JSON.stringify(text)}`);
  if (text !== opts.expect) failed = `expected ${JSON.stringify(opts.expect)}, got ${JSON.stringify(text)}`;
} catch (err) {
  failed = err.message;
} finally {
  await page.screenshot({ path: path.join(outDir, 'hello-result.png') });
  if (Number(opts['keep-open']) > 0) await page.waitForTimeout(Number(opts['keep-open']) * 1000);
  await browser.close();
  server.close();
}

if (failed) {
  log(`FAIL: ${failed}`);
  flushLog();
  process.exit(1);
}
log(`PASS: pressing the hello button shows ${JSON.stringify(opts.expect)}`);
flushLog();
