import { defineConfig, devices } from '@playwright/test';

// UI tests against a standalone local realm (scripts/e2e_standalone_realm.py writes REALM_JSON).
// No baseURL: each spec opens the realm inside a portal host page it serves itself.
export default defineConfig({
	testDir: './tests/standalone',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: 0,
	timeout: 15 * 60 * 1000,
	outputDir: 'test-results/standalone',
	reporter: [['list'], ['html', { outputFolder: 'playwright-report/standalone', open: 'never' }]],
	use: {
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure',
		video: 'retain-on-failure',
		headless: true,
		viewport: { width: 1280, height: 900 }
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } }
		}
	]
});
