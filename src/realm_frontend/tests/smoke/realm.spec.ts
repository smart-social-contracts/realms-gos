import { test, expect } from '@playwright/test';
import { loadTargets } from './targets';
import { watchPageErrors } from './page-errors';

const SIGN_IN = /^Sign in$/i;
const EXTENSION_LINK = 'a[href^="/extensions/"]';

for (const target of loadTargets().filter((t) => t.kind === 'realm')) {
	test.describe(target.name, () => {
		test('public dashboard renders and the extension list shows', async ({ page }) => {
			const collectErrors = watchPageErrors(page);

			const response = await page.goto(target.url);
			expect(response?.status()).toBe(200);

			// The portal serves the realm's own frontend inside an iframe.
			const realm = page.frameLocator('iframe');
			await expect(realm.getByRole('button', { name: SIGN_IN }).first()).toBeVisible();
			// The dashboard stats come from canister queries, slower than the page shell.
			await expect(realm.getByText('Proposals', { exact: true }).first()).toBeVisible({
				timeout: 30_000
			});
			await expect(realm.locator(EXTENSION_LINK).first()).toBeAttached();
			await page.waitForLoadState('networkidle');

			expect(collectErrors()).toEqual([]);
		});

		test('every public extension page loads', async ({ page }) => {
			await page.goto(target.url);
			const links = page.frameLocator('iframe').locator(EXTENSION_LINK);
			await expect(links.first()).toBeAttached();
			const paths = new Set(await links.evaluateAll((a) => a.map((x) => x.getAttribute('href')!)));
			expect(paths.size).toBeGreaterThan(0);

			for (const path of paths) {
				const collectErrors = watchPageErrors(page);
				await page.goto(new URL(`${new URL(target.url).pathname}${path}`, target.url).href);
				const body = page.frameLocator('iframe').locator('body');
				await expect(body, path).not.toContainText('Loading...');
				await expect(body, path).not.toContainText(/error/i);
				await page.waitForLoadState('networkidle');
				expect(collectErrors(), path).toEqual([]);
			}
		});
	});
}
