import { defineConfig, devices } from '@playwright/test';

// Read-only check of the live sites (tests/smoke/targets.json, or SMOKE_TARGETS_FILE).
// Nothing is signed in or submitted. Run it after every deploy: npm run test:smoke
// SMOKE_ENV=production|staging checks one environment only.
export default defineConfig({
	testDir: './tests/smoke',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	// One retry on CI: a canister that is cold on the first request failed 4 of the first 28
	// nightly runs and passed when looked at again.
	retries: process.env.CI ? 1 : 0,
	timeout: 2 * 60 * 1000,
	// Canister-served pages load slowly, most of all on a cold CI runner.
	expect: { timeout: 30_000 },
	outputDir: 'test-results/smoke',
	reporter: [
		['list'],
		['html', { outputFolder: 'playwright-report/smoke', open: 'never' }],
		// What scripts/smoke_issue.sh reads to say which checks failed.
		['json', { outputFile: 'test-results/smoke/report.json' }]
	],
	use: {
		screenshot: 'on',
		video: 'on',
		headless: true,
		viewport: { width: 1280, height: 900 }
	},
	projects: [
		{ name: 'hosts', testMatch: /hosts\.spec\.ts/ },
		{ name: 'assistant', testMatch: /assistant\.spec\.ts/ },
		{
			name: 'chromium',
			testIgnore: [/hosts\.spec\.ts/, /assistant\.spec\.ts/],
			// A host that does not answer fails once, by name, in "hosts"; the page checks
			// are then skipped instead of timing out for two minutes each.
			dependencies: ['hosts'],
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
