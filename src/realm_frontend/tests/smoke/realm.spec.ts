import { test, expect } from '@playwright/test';
import { loadTargets } from './targets';
import { watchPageErrors } from './page-errors';

const SIGN_IN = /^Sign in$/i;
const EXTENSION_LINK = 'a[href^="/extensions/"]';

for (const target of loadTargets().filter((t) => t.kind === 'realm')) {
	test.describe(target.name, () => {
		test('public dashboard renders and the extension list shows', async ({ page }) => {
			const pageErrors = watchPageErrors(page);

			const response = await page.goto(target.url);
			expect(response?.status()).toBe(200);

			// The portal serves the realm's own frontend inside an iframe.
			const realm = page.frameLocator('iframe');
			await expect(realm.getByRole('button', { name: SIGN_IN }).first()).toBeVisible();
			await expect(realm.getByText('Proposals', { exact: true }).first()).toBeVisible();
			await expect(realm.locator(EXTENSION_LINK).first()).toBeAttached();
			await page.waitForLoadState('networkidle');

			expect(pageErrors.errors()).toEqual([]);
		});

		test('every public extension page loads', async ({ page }) => {
			await page.goto(target.url);
			const links = page.frameLocator('iframe').locator(EXTENSION_LINK);
			await expect(links.first()).toBeAttached();
			const paths = new Set(await links.evaluateAll((a) => a.map((x) => x.getAttribute('href')!)));
			expect(paths.size).toBeGreaterThan(0);

			const pageErrors = watchPageErrors(page);
			const realmPath = new URL(target.url).pathname.replace(/\/$/, '');

			for (const path of paths) {
				pageErrors.clear();
				await page.goto(new URL(`${realmPath}${path}`, target.url).href);
				await page.waitForLoadState('networkidle');
				// The iframe body is empty until the extension renders, so wait for its
				// heading before asserting that nothing is stuck or broken.
				const extension = page.frameLocator('iframe');
				await expect(extension.getByRole('heading').first(), path).toBeVisible();
				await expect(extension.locator('body'), path).not.toContainText('Loading...');
				await expect(extension.locator('body'), path).not.toContainText(/\berror\b/i);
				expect(pageErrors.errors(), path).toEqual([]);
			}
		});
	});
}
