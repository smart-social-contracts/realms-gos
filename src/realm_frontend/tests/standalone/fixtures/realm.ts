// A standalone local realm (scripts/e2e_standalone_realm.py) opened the way a federation portal
// opens it: a host page embeds the realm with ?portal=1 and answers its portal bridge, signing
// delegations with the founder's key. No Internet Identity and no test-mode login involved.
import http from 'node:http';
import { readFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import type { FrameLocator, Page } from '@playwright/test';
import { Actor, HttpAgent } from '@dfinity/agent';
import { DelegationChain, Ed25519KeyIdentity } from '@dfinity/identity';

export interface StandaloneRealm {
	network_url: string;
	realm_url: string;
	realm_backend: string;
	realm_frontend: string;
	file_registry: string;
	marketplace: string;
	token_ledger: string;
	founder: string;
	founder_seed: string;
	name: string;
	codex?: string;
	extensions?: Record<string, string>;
}

export function loadRealm(): StandaloneRealm {
	const path = process.env.REALM_JSON || '/tmp/e2e/realm.json';
	return JSON.parse(readFileSync(path, 'utf8')) as StandaloneRealm;
}

export function founderIdentity(realm: StandaloneRealm): Ed25519KeyIdentity {
	return Ed25519KeyIdentity.generate(Uint8Array.from(Buffer.from(realm.founder_seed, 'hex')));
}

function hostHtml(realm: StandaloneRealm, path: string): string {
	const sep = path.includes('?') ? '&' : '?';
	return `<!doctype html>
<html><head><meta charset="utf-8"><title>Realms portal (e2e)</title>
<style>html,body{margin:0;height:100%}iframe{border:0;width:100%;height:100%}</style></head>
<body>
<iframe id="realm" src="${realm.realm_url}${path}${sep}portal=1"></iframe>
<script>
  const realm = document.getElementById('realm');
  window.addEventListener('message', (ev) => {
    if (ev.source !== realm.contentWindow || ev.data?.type !== 'bridge:hello') return;
    const channel = new MessageChannel();
    channel.port1.onmessage = async (m) => {
      const msg = m.data || {};
      if (msg.type === 'auth:request-delegation') {
        const signed = await window.__signDelegation(msg.payload.sessionPublicKeyDer);
        channel.port1.postMessage({
          type: 'auth:delegation',
          payload: { ...signed, backendCanisterId: ${JSON.stringify(realm.realm_backend)} },
        });
      }
    };
    realm.contentWindow.postMessage({ type: 'bridge:init' }, '*', [channel.port2]);
    channel.port1.postMessage({
      type: 'config:realm',
      payload: {
        backendCanisterId: ${JSON.stringify(realm.realm_backend)},
        frontendCanisterId: ${JSON.stringify(realm.realm_frontend)},
        env: 'local',
        path: ${JSON.stringify(path)},
      },
    });
  });
</script>
</body></html>`;
}

/** Serve the portal host page; returns its URL and a close function. */
export async function startPortalHost(
	realm: StandaloneRealm,
	path: string
): Promise<{ url: string; close: () => Promise<void> }> {
	const html = hostHtml(realm, path);
	const server = http.createServer((_req, res) => {
		res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
		res.end(html);
	});
	await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
	const { port } = server.address() as AddressInfo;
	return {
		url: `http://localhost:${port}/`,
		close: () => new Promise<void>((resolve) => server.close(() => resolve()))
	};
}

/** Let `page` sign delegations as `identity` for the portal host. */
export async function exposeSigner(page: Page, identity: Ed25519KeyIdentity): Promise<void> {
	await page.exposeFunction('__signDelegation', async (sessionPublicKeyDer: number[]) => {
		const der = new Uint8Array(sessionPublicKeyDer);
		const sessionKey = { toDer: () => der, rawKey: der, derKey: der } as never;
		const expiresAt = Date.now() + 60 * 60 * 1000;
		const chain = await DelegationChain.create(identity, sessionKey, new Date(expiresAt));
		return { delegation: chain.toJSON(), expiresAt };
	});
}

/** Open `path` of the realm inside the portal host; returns the realm's frame. */
export async function openInPortal(
	page: Page,
	realm: StandaloneRealm,
	path: string
): Promise<{ frame: FrameLocator; close: () => Promise<void> }> {
	const host = await startPortalHost(realm, path);
	await page.goto(host.url);
	return { frame: page.frameLocator('#realm'), close: host.close };
}

const readIdl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) =>
	IDL.Service({ read: IDL.Func([IDL.Text, IDL.Text], [IDL.Text], ['query']) });

/** A public read operation of the realm, as anonymous. */
export async function readOp(
	realm: StandaloneRealm,
	name: string,
	args: Record<string, unknown> = {}
): Promise<Record<string, unknown>> {
	const agent = await HttpAgent.create({ host: realm.network_url, shouldFetchRootKey: true });
	const actor = Actor.createActor<{ read: (n: string, a: string) => Promise<string> }>(
		readIdl as never,
		{
			agent,
			canisterId: realm.realm_backend
		}
	);
	return JSON.parse(await actor.read(name, JSON.stringify(args)));
}
