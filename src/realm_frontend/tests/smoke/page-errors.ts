import type { Page } from '@playwright/test';

// Collects what must stay empty on a healthy page: console errors, uncaught
// exceptions, and requests to the page's own origins that failed or got 4xx/5xx.
// A realm page also loads its canister in an iframe, so every frame's origin
// counts as "own". Third-party requests (map tiles, fonts) are not ours to fix.
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

	return () => {
		const ownOrigins = new Set(page.frames().map((frame) => new URL(frame.url()).origin));
		const failedOwnRequests = requests
			.filter((request) => ownOrigins.has(new URL(request.url).origin))
			.map((request) => `${request.problem}: ${request.url}`);
		return [...problems, ...failedOwnRequests];
	};
}
