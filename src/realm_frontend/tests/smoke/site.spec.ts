import { test, expect } from '@playwright/test';
import { loadTargets } from './targets';
import { watchPageErrors } from './page-errors';

// The portal says "Log In", the marketplace and realms say "Sign in".
const SIGN_IN = /^(Sign in|Log In)$/i;

for (const target of loadTargets().filter((t) => t.kind !== 'realm')) {
	test(`${target.name}: loads without errors and offers sign-in`, async ({ page, request }) => {
		const collectErrors = watchPageErrors(page);

		const response = await page.goto(target.url);
		expect(response?.status()).toBe(200);
		await page.waitForLoadState('networkidle');

		const canisterIds = await request.get(new URL('/canister_ids.js', target.url).href);
		expect(canisterIds.status(), '/canister_ids.js').toBe(200);

		await expect(page.getByRole('button', { name: SIGN_IN }).first()).toBeVisible();
		expect(collectErrors()).toEqual([]);
	});
}
