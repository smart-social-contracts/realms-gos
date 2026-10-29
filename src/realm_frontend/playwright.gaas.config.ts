import { defineConfig, devices } from '@playwright/test';

// UI tests against a local GaaS stack (scripts/local_up.sh --gaas, then scripts/e2e_gaas_scale.py writes
// GAAS_JSON). A realm is launched from the portal; each spec then opens it in a portal host page it serves.
export default defineConfig({
	testDir: './tests/gaas',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: 0,
	timeout: 60 * 60 * 1000,
	outputDir: 'test-results/gaas',
	reporter: [['list'], ['html', { outputFolder: 'playwright-report/gaas', open: 'never' }]],
	use: {
		trace: 'on',
		screenshot: 'on',
		video: 'on',
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
