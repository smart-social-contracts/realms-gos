import { defineConfig, devices } from '@playwright/test';

// Read-only check of the live sites (tests/smoke/targets.json, or SMOKE_TARGETS_FILE).
// Nothing is signed in or submitted. Run it after every deploy: npm run test:smoke
export default defineConfig({
	testDir: './tests/smoke',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: 0,
	timeout: 2 * 60 * 1000,
	// Canister-served pages load slowly, most of all on a cold CI runner.
	expect: { timeout: 30_000 },
	outputDir: 'test-results/smoke',
	reporter: [['list'], ['html', { outputFolder: 'playwright-report/smoke', open: 'never' }]],
	use: {
		screenshot: 'on',
		video: 'on',
		headless: true,
		viewport: { width: 1280, height: 900 }
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
