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
	// Canister calls take seconds: a control may take that long to show.
	expect: { timeout: 30_000 },
	outputDir: 'test-results/standalone',
	reporter: [['list'], ['html', { outputFolder: 'playwright-report/standalone', open: 'never' }]],
	// Screenshots and videos are kept for every test, passed or not: the run is reviewed afterwards from the report.
	use: {
		// The run has no retries, so 'on-first-retry' would record nothing: a trace is kept for failures only.
		trace: 'retain-on-failure',
		screenshot: 'on',
		video: 'on',
		headless: true,
		viewport: { width: 1280, height: 900 }
	},
	// The specs share one realm, which the wizard spec launches and the others use. That works only because there is
	// one worker and the projects run in this list's order: a failure of the wizard spec shows up in all of them.
	projects: ['wizard', 'proposals', 'extensions-ops', 'justice'].map((name) => ({
		name,
		testMatch: `${name}.spec.ts`,
		use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } }
	}))
});
