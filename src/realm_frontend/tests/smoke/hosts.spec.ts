import { test, expect } from '@playwright/test';
import { lookup } from 'node:dns/promises';
import { connect } from 'node:net';
import { loadTargets } from './targets';

// A browser tries a host's addresses one after the other, so one dead address among
// several makes a random share of visits stall for 30 seconds or more. That looks like
// a slow page, and the page checks time out without saying why. Try every address.
//
// IPv4 only: GitHub's runners have no IPv6 route, so an AAAA record would always fail there.
const PORT = 443;
const CONNECT_TIMEOUT_MS = 5_000;

function acceptsConnections(address: string): Promise<boolean> {
	return new Promise((resolve) => {
		const socket = connect({ host: address, port: PORT, timeout: CONNECT_TIMEOUT_MS });
		socket.once('connect', () => {
			socket.destroy();
			resolve(true);
		});
		socket.once('timeout', () => {
			socket.destroy();
			resolve(false);
		});
		socket.once('error', () => resolve(false));
	});
}

const hostnames = [...new Set(loadTargets().map((target) => new URL(target.url).hostname))];

for (const hostname of hostnames) {
	test(`${hostname}: every address accepts connections`, async () => {
		const addresses = (await lookup(hostname, { all: true, family: 4 })).map((a) => a.address);
		const reachable = await Promise.all(addresses.map(acceptsConnections));
		const dead = addresses.filter((_, i) => !reachable[i]);

		expect(dead, `addresses of ${hostname} that accept no connection on port ${PORT}`).toEqual([]);
	});
}
