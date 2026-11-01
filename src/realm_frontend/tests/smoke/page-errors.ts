import type { Page } from '@playwright/test';

// Collects what must stay empty on a healthy page: console errors, uncaught
// exceptions, and requests to our own hosts that failed or got 4xx/5xx.
// Canister queries and calls go to the IC API hosts (icp-api.io, icp0.io) and the
// realm's assets to <id>.icp0.io, never to the page's own origin, so those hosts
// count as ours. Third-party requests (map tiles, fonts) are not ours to fix.
const OWN_HOSTS = /(^|\.)(icp0\.io|ic0\.app|icp-api\.io)$/;
const OWN_HOSTNAMES = ['geister-api.realmsgos.dev'];

export function watchPageErrors(page: Page) {
	const problems: string[] = [];
	const requests: { url: string; problem: string }[] = [];

	page.on('console', (msg) => {
		if (msg.type() === 'error') problems.push(`console error: ${msg.text()}`);
	});
	page.on('pageerror', (error) => problems.push(`uncaught exception: ${error.message}`));
	page.on('requestfailed', (request) => {
		const reason = request.failure()?.errorText ?? 'failed';
		// The browser cancels in-flight requests when we navigate away; that is not a site fault.
		if (reason !== 'net::ERR_ABORTED') requests.push({ url: request.url(), problem: reason });
	});
	page.on('response', (response) => {
		if (response.status() >= 400) {
			requests.push({ url: response.url(), problem: `HTTP ${response.status()}` });
		}
	});

	return {
		errors: () => {
			const ownHosts = new Set(
				page
					.frames()
					.filter((frame) => frame.url() !== '')
					.map((frame) => new URL(frame.url()).hostname)
			);
			const isOwn = (url: string) => {
				const { hostname } = new URL(url);
				return (
					ownHosts.has(hostname) || OWN_HOSTS.test(hostname) || OWN_HOSTNAMES.includes(hostname)
				);
			};
			const failedOwnRequests = requests
				.filter((request) => isOwn(request.url))
				.map((request) => `${request.problem}: ${request.url}`);
			return [...problems, ...failedOwnRequests];
		},
		// Forget what was seen so far, so a late error from one page is not counted against the next.
		clear: () => {
			problems.length = 0;
			requests.length = 0;
		}
	};
}
