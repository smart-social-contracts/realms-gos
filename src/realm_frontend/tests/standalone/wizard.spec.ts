// The realm installation wizard, end to end, on a standalone local realm:
//   scripts/up.sh -e local                       (the product: registry with the codices, marketplace, token)
//   python3 scripts/e2e_standalone_realm.py      (one realm_backend + realm_frontend, in setup)
//   npx playwright test -c playwright.standalone.config.ts
// The founder signs in through the portal bridge, walks every wizard step, launches the realm and
// lands on its public dashboard. The realm's own state is then checked through its read operations.
import { expect, test, type FrameLocator, type Locator, type Page } from '@playwright/test';
import { Actor, HttpAgent } from '@dfinity/agent';
import {
	exposeSigner,
	founderIdentity,
	loadRealm,
	openInPortal,
	readOp,
	type StandaloneRealm
} from './fixtures/realm';

const CODEX = process.env.E2E_CODEX || 'syntropia';
const WELCOME = 'Welcome to the e2e realm, built by the wizard.';
const MANIFESTO = 'Every step of setup is tested.';
const LAUNCH_TIMEOUT = 10 * 60 * 1000;

let realm: StandaloneRealm;
let shots = 0;

test.beforeAll(() => {
	realm = loadRealm();
});

test.beforeEach(() => {
	shots = 0;
});

// Numbered screenshots in the test's output folder, also attached to the HTML report.
// The realm scrolls inside the portal's iframe, so the viewport grows to its content for the shot.
async function snap(page: Page, label: string) {
	const name = `${String(++shots).padStart(2, '0')}-${label}.png`;
	const viewport = page.viewportSize()!;
	const heights = await Promise.all(
		page
			.frames()
			.map((f) =>
				f
					.evaluate(() =>
						Math.max(...[...document.querySelectorAll('*')].map((e) => e.scrollHeight))
					)
					.catch(() => 0)
			)
	);
	const height = Math.min(Math.max(viewport.height, ...heights), 8000);
	await page.setViewportSize({ width: viewport.width, height });
	await page.waitForTimeout(300);
	const body = await page.screenshot({ path: test.info().outputPath(name), fullPage: true });
	await page.setViewportSize(viewport);
	await test.info().attach(name, { body, contentType: 'image/png' });
}

async function ledgerSymbol(ledger: string): Promise<string> {
	const agent = await HttpAgent.create({ host: realm.network_url, shouldFetchRootKey: true });
	const idl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) =>
		IDL.Service({ icrc1_symbol: IDL.Func([], [IDL.Text], ['query']) });
	const actor = Actor.createActor<{ icrc1_symbol: () => Promise<string> }>(idl as never, {
		agent,
		canisterId: ledger
	});
	return actor.icrc1_symbol();
}

async function passDemoNotice(frame: FrameLocator) {
	const notice = frame.locator('.demo-notice');
	if (await notice.isVisible().catch(() => false)) {
		await notice.locator('input[type="checkbox"]').check();
		await frame.getByRole('button', { name: 'Continue' }).click();
	}
}

function toolbarButton(frame: FrameLocator, name: string | RegExp) {
	return frame.locator('.setup-wizard__actions--toolbar').getByRole('button', { name });
}

test('the founder sets up and launches a realm through the wizard', async ({ page }) => {
	test.setTimeout(LAUNCH_TIMEOUT + 5 * 60 * 1000);
	const founder = founderIdentity(realm);
	expect(founder.getPrincipal().toText(), 'the founder key matches the installed founder').toBe(
		realm.founder
	);
	expect(realm.token_ledger, 'the product sheet provides a token ledger').toBeTruthy();
	const symbol = await ledgerSymbol(realm.token_ledger);

	page.on('console', (msg) => {
		if (msg.type() === 'error') console.log(`[realm console] ${msg.text()}`);
	});
	await exposeSigner(page, founder);
	const { frame, close } = await openInPortal(page, realm, '/setup');

	try {
		await test.step('welcome', async () => {
			await passDemoNotice(frame);
			const begin = frame.locator('.setup-wizard__hero-cta');
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
			await snap(page, 'review');
			await toolbarButton(frame, 'Launch realm').click();

			const steps = frame.locator('.setup-wizard__launch-step');
			await expect(steps).toHaveCount(5, { timeout: 30_000 });
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
							return status.status !== 'setup' ? 5 : 0;
						}
						return done.count();
					},
					{ timeout: LAUNCH_TIMEOUT, intervals: [2_000] }
				)
				.toBe(5);
			await snap(page, 'launched');
		});

		await test.step('the realm is launched (backend)', async () => {
			const status = await readOp(realm, 'status');
			expect(status.ok).toBe(true);
			expect(status.status).not.toBe('setup');
			const info = (await readOp(realm, 'realm_info')).realm as Record<string, unknown>;
			expect(info.name).toBe(realm.name);
			expect(info.welcome_message).toBe(WELCOME);
			expect(info.manifesto).toBe(MANIFESTO);
			expect(info.accounting_currency).toBe(symbol);
		});

		await test.step('the public dashboard', async () => {
			const proceed = toolbarButton(frame, 'Continue to the public dashboard');
			if (await proceed.isVisible().catch(() => false)) await proceed.click();
			await expect(frame.locator('.setup-wizard')).toHaveCount(0, { timeout: 60_000 });
			await expect(frame.getByText(WELCOME).first()).toBeVisible({ timeout: 120_000 });
			await snap(page, 'public-dashboard');
		});
	} catch (err) {
		await snap(page, 'failure').catch(() => {});
		throw err;
	} finally {
		await close();
	}
});

test('after launch /setup sends the founder to the public dashboard', async ({ page }) => {
	const founder = founderIdentity(realm);
	await exposeSigner(page, founder);
	const { frame, close } = await openInPortal(page, realm, '/setup');
	try {
		// A launched realm sends /setup back to its dashboard.
		await expect(frame.getByText(WELCOME).first()).toBeVisible({ timeout: 120_000 });
		await expect(frame.locator('.setup-wizard__hero-cta')).toHaveCount(0);
		await snap(page, 'setup-redirects-to-dashboard');
	} finally {
		await close();
	}
	const status = await readOp(realm, 'status');
	expect(status.status).not.toBe('setup');
});

// Text that means a page is still loading, or that it failed, in the realm or in an extension's own iframe.
const LOADING = /\bLoading\b[^\n]{0,40}\.\.\.|\bLoading\b[^\n]{0,40}…/i;
const BROKEN =
	/Failed to load|has no backend|is not available to you|is not a function|not declared|is not a read operation|unknown operation|Cannot read propert|Something went wrong|SDK version mismatch|\bundefined\b|\bNaN\b|\[object Object\]/i;

async function pageTexts(page: Page): Promise<string[]> {
	return Promise.all(
		page
			.frames()
			.filter((f) => f !== page.mainFrame())
			.map((f) => f.evaluate(() => document.body?.innerText ?? '').catch(() => ''))
	);
}

// The startup splash (#app-splash) or a "Loading..." text in the realm or an extension's iframe.
async function stillLoading(page: Page): Promise<boolean> {
	const splash = await Promise.all(
		page.frames().map((f) =>
			f
				.locator('#app-splash')
				.isVisible()
				.catch(() => false)
		)
	);
	return splash.some(Boolean) || (await pageTexts(page)).some((t) => LOADING.test(t));
}

async function settle(page: Page, timeout = 45_000): Promise<boolean> {
	for (const end = Date.now() + timeout; Date.now() < end; await page.waitForTimeout(1_000)) {
		if (!(await stillLoading(page))) return true;
	}
	return false;
}

test('every sidebar page of the launched realm loads without errors', async ({ page }) => {
	test.setTimeout(30 * 60 * 1000);
	const founder = founderIdentity(realm);
	await exposeSigner(page, founder);
	const errors: string[] = [];
	page.on('console', (msg) => {
		if (msg.type() === 'error') errors.push(`console: ${msg.text()}`);
	});
	page.on('pageerror', (err) => errors.push(`page error: ${err.message}`));

	const portal: { current?: Awaited<ReturnType<typeof openInPortal>> } = {};
	// The sidebar folds its sections again after each navigation.
	async function unfold(sidebar: Locator) {
		const folded = sidebar.locator('.fold-summary[aria-expanded="false"]');
		while ((await folded.count()) > 0) await folded.first().click();
	}
	// Opens the realm at / with every sidebar section unfolded; again after a page that loses the sidebar.
	async function openSidebar() {
		await portal.current?.close();
		portal.current = await openInPortal(page, realm, '/');
		const sidebar = portal.current.frame.locator('aside').filter({ visible: true }).first();
		// Signed in (the ME section) and done loading the realm's extension menu.
		await expect(sidebar.locator('nav a[href="/identities"]')).toBeVisible({ timeout: 120_000 });
		await expect(sidebar.locator('nav .animate-spin')).toHaveCount(0, { timeout: 120_000 });
		await expect(
			sidebar
				.locator('nav a[href^="/extensions/"]:not([href="/extensions/public_dashboard"])')
				.first()
		).toBeAttached({ timeout: 120_000 });
		await unfold(sidebar);
		return sidebar;
	}

	try {
		let sidebar = await openSidebar();
		const links = await sidebar
			.locator('nav a[href]')
			.evaluateAll((as) =>
				as.map((a) => ({ href: a.getAttribute('href')!, label: (a.textContent ?? '').trim() }))
			);
		const pages = [...new Map(links.map((l) => [l.href, l])).values()];
		expect(pages.length, 'the sidebar lists pages').toBeGreaterThan(0);
		console.log(`sidebar: ${pages.map((p) => `${p.label} (${p.href})`).join(', ')}`);

		for (const { href, label } of pages) {
			await test.step(`${label} (${href})`, async () => {
				let link = sidebar.locator(`nav a[href="${href}"]`).first();
				errors.length = 0;
				await unfold(sidebar).catch(() => {});
				// A page can drop or cover the sidebar: then start again from the realm's home.
				if (
					!(await link.click({ timeout: 10_000 }).then(
						() => true,
						() => false
					))
				) {
					sidebar = await openSidebar();
					link = sidebar.locator(`nav a[href="${href}"]`).first();
					errors.length = 0;
					await link.click({ timeout: 30_000 });
				}
				const loaded = await settle(page);
				await page.waitForTimeout(1_500);
				await snap(page, `sidebar-${href.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '')}`);
				expect.soft(loaded, `${label}: finishes loading`).toBe(true);
				const broken = (await pageTexts(page))
					.flatMap((t) => t.split('\n'))
					.filter((line) => BROKEN.test(line));
				expect.soft(broken, `${label}: error text on the page`).toEqual([]);
				expect.soft([...errors], `${label}: console errors`).toEqual([]);
			});
		}
	} finally {
		await portal.current?.close();
	}
});
