// The setup wizard, walked as a founder does, for any realm a spec opened in a portal host frame.
import { expect, test, type FrameLocator, type Locator, type Page } from '@playwright/test';
import { LAUNCH_PHASES } from '../../../src/lib/setup/wizardLogic';
import { readOp, type StandaloneRealm } from './realm';

export const LAUNCH_TIMEOUT = 10 * 60 * 1000;

export interface WizardChoices {
	codex: string;
	symbol: string;
	welcome: string;
	manifesto: string;
	/** Days new realm-wide proposals stay open; 0 keeps them open until certain. Left as is when unset. */
	votingWindowDays?: number;
	snap: (page: Page, label: string) => Promise<void>;
}

/** Accept the demo notice when the realm shows one before `next` (staging and demo networks do). */
export async function passDemoNotice(frame: FrameLocator, next: Locator) {
	const notice = frame.locator('.demo-notice');
	await expect(notice.or(next).first()).toBeVisible({ timeout: 120_000 });
	if (await notice.isVisible()) {
		expect.soft(await notice.locator('.demo-notice__body[lang="en"]').innerText()).not.toBe('');
		// The page can remount once sign-in settles, which clears the tick: tick again until it holds.
		const proceed = frame.getByRole('button', { name: 'Continue' });
		await expect(async () => {
			await notice.locator('input[type="checkbox"]').check();
			await expect(proceed).toBeEnabled({ timeout: 2_000 });
		}).toPass({ timeout: 60_000 });
		await proceed.click();
	}
}

export function toolbarButton(frame: FrameLocator, name: string | RegExp) {
	return frame.locator('.setup-wizard__actions--toolbar').getByRole('button', { name });
}


/** From the welcome screen to a launched realm: codex, token (``realm.token_ledger``), branding, languages, launch. */
export async function launchThroughWizard(page: Page, frame: FrameLocator, realm: StandaloneRealm, choices: WizardChoices) {
	const { codex: CODEX, symbol, welcome: WELCOME, manifesto: MANIFESTO, snap } = choices;
	await test.step('welcome', async () => {
		const begin = frame.locator('.setup-wizard__hero-cta');
		await passDemoNotice(frame, begin);
		await expect(begin).toBeVisible({ timeout: 120_000 });
		await expect(frame.locator('.setup-wizard__hero-title')).toHaveText(realm.name);
		await snap(page, 'welcome');
		await begin.click();
	});

	await test.step(`codex: ${CODEX}`, async () => {
		const card = frame.locator('label.setup-wizard__codex-card--picker', {
			has: frame.locator(`input[value="${CODEX}"]`)
		});
		await expect(card, `codex ${CODEX} is offered by the registry`).toBeVisible({
			timeout: 60_000
		});
		await card.click();
		await expect(card).toHaveClass(/setup-wizard__codex-card--selected/);
		const version = frame.locator('.setup-wizard__codex-detail select');
		await expect(version).toBeVisible();
		const versions = await version.locator('option').allTextContents();
		expect(versions.length).toBeGreaterThan(0);
		await snap(page, 'codex');
		await toolbarButton(frame, 'Continue').click();
	});

	await test.step(`token: custom ledger ${realm.token_ledger} (${symbol})`, async () => {
		const custom = frame.locator('input[name="token"][value="custom"]');
		await expect(custom).toBeVisible({ timeout: 30_000 });
		await custom.check();
		await frame.locator('#token-canister').fill(realm.token_ledger);
		await frame.locator('#token-indexer').fill(realm.token_ledger);
		await expect(frame.locator('.setup-wizard__symbol strong')).toHaveText(symbol, {
			timeout: 30_000
		});
		const next = toolbarButton(frame, 'Continue');
		await expect(next).toBeEnabled();
		await snap(page, 'token');
		await next.click();
	});

	await test.step('branding', async () => {
		const welcome = frame.locator('#welcome-message');
		await expect(welcome).toBeVisible({ timeout: 30_000 });
		await welcome.fill(WELCOME);
		await frame.locator('#manifesto').fill(MANIFESTO);
		await snap(page, 'branding');
		await toolbarButton(frame, 'Continue').click();
	});

	await test.step('languages: English and Spanish', async () => {
		const spanish = frame.locator('label.setup-wizard__codex-card', { hasText: 'es' }).filter({
			has: frame.locator('p', { hasText: /^es$/ })
		});
		await expect(spanish).toBeVisible({ timeout: 30_000 });
		await spanish.locator('input[type="checkbox"]').check();
		await frame.locator('#primary-language').selectOption('en');
		await snap(page, 'languages');
		await toolbarButton(frame, 'Continue').click();
	});

	await test.step('review and launch', async () => {
		const review = frame.locator('.setup-wizard__review-list');
		await expect(review).toBeVisible({ timeout: 30_000 });
		await expect(review).toContainText(symbol);
		await expect(review).toContainText(/English/);
		await expect(review).toContainText(/Spanish|Español/);
		if (choices.votingWindowDays != null) {
			await frame.locator('#voting-window-days').fill(String(choices.votingWindowDays));
		}
		await snap(page, 'review');
		await toolbarButton(frame, 'Launch realm').click();

		const steps = frame.locator('.setup-wizard__launch-step');
		await expect(steps).toHaveCount(LAUNCH_PHASES.length, { timeout: 30_000 });
		await snap(page, 'launching');
		const failed = frame.locator('.setup-wizard__launch-step--failed');
		const done = frame.locator('.setup-wizard__launch-step--completed');
		// Once the realm is open the setup page may leave for the dashboard on its own, before
		// the last step renders completed: then the backend's status is what tells.
		await expect
			.poll(
				async () => {
					if ((await failed.count()) > 0) {
						const why = await frame.locator('.setup-wizard__launch-step-error').allTextContents();
						throw new Error(`launch failed: ${why.join(' | ')}`);
					}
					if ((await steps.count()) === 0) {
						const status = await readOp(realm, 'status');
						return status.status !== 'setup' ? LAUNCH_PHASES.length : 0;
					}
					return done.count();
				},
				{ timeout: LAUNCH_TIMEOUT, intervals: [2_000] }
			)
			.toBe(LAUNCH_PHASES.length);
		await snap(page, 'launched');
	});

}
