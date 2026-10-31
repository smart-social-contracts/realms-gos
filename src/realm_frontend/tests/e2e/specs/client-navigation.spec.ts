import { test, expect } from '@playwright/test';

// A throwing afterNavigate callback during the initial `enter` navigation
// aborts SvelteKit start-up before its router is installed, so every link
// click silently becomes a full page reload (app re-boot + ~30 canister calls).
test.describe('Client-side navigation', () => {
	test('sidebar links navigate without a full page reload', async ({ page }) => {
		test.setTimeout(60000);
		const pageErrors: string[] = [];
		page.on('pageerror', (e) => pageErrors.push(String(e)));

		await page.goto('/extensions/voting');
		const link = page.locator('a[href="/extensions/public_dashboard"]').first();
		await link.waitFor({ state: 'attached', timeout: 30000 });
		await page.evaluate(() => ((window as any).__navMarker = 1));

		await link.evaluate((a: HTMLAnchorElement) => a.click());
		await page.waitForURL('**/extensions/public_dashboard', { timeout: 15000 });

		const marker = await page.evaluate(() => (window as any).__navMarker);
		expect(marker, 'window state was lost: the click caused a full page reload').toBe(1);
		expect(pageErrors.filter((e) => e.includes("reading 'pathname'"))).toEqual([]);
	});
});
