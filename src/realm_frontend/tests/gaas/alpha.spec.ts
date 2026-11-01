// The gos.earth journey as an Alpha visitor sees it: access by invitation code, no card billing. Run it with
// scripts/e2e_gaas.sh, which brings the stack up and puts it in Alpha mode (scripts/e2e_gaas_alpha.py writes
// ALPHA_JSON with the invitation codes); on a stack that is in Alpha mode, `npx playwright test -c
// playwright.gaas.config.ts alpha.spec.ts` runs this file alone.
// An applicant who is not activated meets the invitation gate, and is asked for a code in the dashboard too. A
// wrong, a used and an expired code are refused and change nothing; a good one, typed in lower case, activates the
// account with its credits and opens the launch wizard. The billing tab offers no card payment, a code can be
// redeemed there as well, a principal that skipped the gate cannot ask for a deployment, and the activated
// applicant launches a realm, which costs its price in credits.
//
// TEST-ONLY SHORTCUT: these tests skip Internet Identity. The portal turns the invitation gate off while the
// registry's Internet Identity bypass is on (invitation-state.js of gos-as-a-service), and a local stack has no
// Internet Identity, so signIn() writes an auth-client session (auth-client-db / ic-keyval, the keys `identity`
// and `delegation`) straight into the browser's IndexedDB: the applicant's own key delegates to a session key,
// and the applicant's principal is that key's. The portal then runs its real sign-in path from that session.
import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { Actor, HttpAgent } from '@dfinity/agent';
import { DelegationChain, Ed25519KeyIdentity } from '@dfinity/identity';
import { needsRootKey } from '../standalone/fixtures/realm';

interface Alpha {
	network_url: string;
	portal_url: string;
	registry: string;
	valid_code: string;
	dashboard_code: string;
	used_code: string;
	expired_code: string;
	realm_name: string;
}

const A: Alpha = JSON.parse(readFileSync(process.env.ALPHA_JSON || '/tmp/e2e/alpha.json', 'utf8'));
const INVITATION_CREDITS = 50;
const SESSION_MS = 60 * 60 * 1000;
const REQUEST_LINK = 'Request an invitation';
const TALLY_FORM = /^https:\/\/tally\.so\//;

let shots = 0;
async function snap(page: Page, label: string) {
	const name = `${String(++shots).padStart(2, '0')}-${label}.png`;
	const body = await page.screenshot({ path: test.info().outputPath(name), fullPage: true });
	await test.info().attach(name, { body, contentType: 'image/png' });
}

interface Registry {
	redeem_invitation_code: (code: string) => Promise<{ Ok: string } | { Err: string }>;
	is_principal_activated: (principal: string) => Promise<{ Ok: string } | { Err: string }>;
	get_credits: (principal: string) => Promise<{ Ok: { balance: bigint } } | { Err: string }>;
	request_deployment: (manifest: string) => Promise<string>;
}

const registryIdl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) => {
	const text = IDL.Variant({ Ok: IDL.Text, Err: IDL.Text });
	const credits = IDL.Variant({ Ok: IDL.Record({ balance: IDL.Nat64 }), Err: IDL.Text });
	return IDL.Service({
		redeem_invitation_code: IDL.Func([IDL.Text], [text], []),
		is_principal_activated: IDL.Func([IDL.Text], [text], ['query']),
		get_credits: IDL.Func([IDL.Text], [credits], ['query']),
		request_deployment: IDL.Func([IDL.Text], [IDL.Text], [])
	});
};

/** The registry, called as ``identity``. */
async function registryAs(identity: Ed25519KeyIdentity): Promise<Registry> {
	const agent = await HttpAgent.create({
		host: A.network_url,
		identity,
		shouldFetchRootKey: needsRootKey(A.network_url)
	});
	return Actor.createActor<Registry>(registryIdl as never, { agent, canisterId: A.registry });
}

async function creditsOf(user: Ed25519KeyIdentity): Promise<number> {
	const out = await (await registryAs(user)).get_credits(user.getPrincipal().toText());
	if ('Err' in out) throw new Error(`get_credits: ${out.Err}`);
	return Number(out.Ok.balance);
}

async function activationOf(user: Ed25519KeyIdentity): Promise<string> {
	const out = await (await registryAs(user)).is_principal_activated(user.getPrincipal().toText());
	return 'Ok' in out ? out.Ok : out.Err;
}

/** The applicant story: one person, activated by the last of their tries. Fresh every run, as the registry keeps them. */
const applicant = Ed25519KeyIdentity.generate();

/** Stores the session in the page the way auth-client's IndexedDB storage keeps it. */
const storeSession = ({ key, delegation }: { key: string; delegation: string }) =>
	new Promise<void>((resolve, reject) => {
		const open = indexedDB.open('auth-client-db', 1);
		open.onupgradeneeded = () => open.result.createObjectStore('ic-keyval');
		open.onerror = () => reject(open.error);
		open.onsuccess = () => {
			const tx = open.result.transaction('ic-keyval', 'readwrite');
			tx.objectStore('ic-keyval').put(key, 'identity');
			tx.objectStore('ic-keyval').put(delegation, 'delegation');
			tx.oncomplete = () => resolve();
			tx.onerror = () => reject(tx.error);
		};
	});

/** Signs ``user`` in on the portal, as Internet Identity would: their key delegates to a session key. */
async function signIn(page: Page, user: Ed25519KeyIdentity) {
	const session = Ed25519KeyIdentity.generate();
	const chain = await DelegationChain.create(
		user,
		session.getPublicKey(),
		new Date(Date.now() + SESSION_MS)
	);
	await page.goto(A.portal_url);
	await page.evaluate(storeSession, {
		key: JSON.stringify(session.toJSON()),
		delegation: JSON.stringify(chain.toJSON())
	});
}

/** The invitation gate of /deploy-gos, with ``code`` typed in and submitted. */
async function submitCode(page: Page, code: string) {
	await page.getByPlaceholder('Enter your invitation code').fill(code);
	await page.getByRole('button', { name: 'Submit Code' }).click();
}

test.describe.configure({ mode: 'serial' });

// Someone else redeems the used code first.
test.beforeAll(async () => {
	const other = Ed25519KeyIdentity.generate();
	expect(await (await registryAs(other)).redeem_invitation_code(A.used_code)).toHaveProperty('Ok');
});

test('the landing page offers a signed-out visitor the Alpha request link', async ({ page }) => {
	await page.goto(A.portal_url);
	const notice = page.getByRole('complementary', { name: 'gos.earth is in Alpha' });
	await expect(notice).toBeVisible({ timeout: 60_000 });
	await expect(notice.getByRole('link', { name: REQUEST_LINK })).toHaveAttribute(
		'href',
		TALLY_FORM
	);
	await snap(page, 'landing-alpha-notice');
});

test('/deploy-gos asks a signed-out visitor to sign in and says a code is required', async ({
	page
}) => {
	await page.goto(`${A.portal_url}/deploy-gos`);
	await expect(page.getByRole('heading', { name: 'Sign In Required' })).toBeVisible({
		timeout: 60_000
	});
	await expect(page.getByText(/invitation code is required/)).toBeVisible();
	await expect(page.getByRole('complementary', { name: 'gos.earth is in Alpha' })).toBeVisible();
	await snap(page, 'deploy-signed-out');
});

test('a signed-in applicant who is not activated meets the invitation gate', async ({ page }) => {
	await signIn(page, applicant);
	await page.goto(`${A.portal_url}/deploy-gos`);
	await expect(page.getByRole('heading', { name: 'Invitation Required' })).toBeVisible({
		timeout: 60_000
	});
	await expect(page.getByRole('button', { name: 'Submit Code' })).toBeVisible();
	await expect(page.getByRole('link', { name: REQUEST_LINK })).toHaveAttribute('href', TALLY_FORM);
	expect(await activationOf(applicant)).toBe('not_activated');
	await snap(page, 'invitation-gate');
});

test('the dashboard of an applicant who is not activated asks for a code, not a voucher', async ({
	page
}) => {
	await signIn(page, applicant);
	await page.goto(`${A.portal_url}/my-dashboard?tab=billing`);
	await expect(page.getByRole('heading', { name: 'Enter your invitation code' })).toBeVisible({
		timeout: 60_000
	});
	await expect(page.getByRole('link', { name: REQUEST_LINK })).toHaveAttribute('href', TALLY_FORM);
	await expect(page.getByRole('heading', { name: 'Redeem Voucher' })).toHaveCount(0);
	await snap(page, 'dashboard-awaiting-invitation');
});

const refusals = [
	{ what: 'a wrong code', code: () => 'NOT-AN-INVITATION', message: 'Invalid invitation code' },
	{
		what: 'a used code',
		code: () => A.used_code,
		message: 'This invitation code has already been used'
	},
	{
		what: 'an expired code',
		code: () => A.expired_code,
		message: 'This invitation code has expired'
	}
];

for (const { what, code, message } of refusals) {
	test(`${what} is refused with the registry's message and nothing changes`, async ({ page }) => {
		await signIn(page, applicant);
		await page.goto(`${A.portal_url}/deploy-gos`);
		await expect(page.getByRole('heading', { name: 'Invitation Required' })).toBeVisible({
			timeout: 60_000
		});
		await submitCode(page, code());
		await expect(page.locator('.invitation-error')).toHaveText(message, { timeout: 60_000 });
		await expect(page.getByRole('heading', { name: 'Invitation Required' })).toBeVisible();
		await snap(page, 'refused');
		// The screen's text for a wrong code is also the portal's fallback: the registry's own reply settles it.
		expect(await (await registryAs(applicant)).redeem_invitation_code(code())).toEqual({
			Err: message
		});
		expect(await activationOf(applicant), 'still not activated').toBe('not_activated');
		expect(await creditsOf(applicant), 'credits unchanged').toBe(0);
	});
}

// The portal upper-cases what is typed: the registry hashes a code exactly as sent, and codes are issued in upper case.
test('a valid code, typed in lower case, activates the account, adds its credits and opens the wizard', async ({
	page
}) => {
	await signIn(page, applicant);
	await page.goto(`${A.portal_url}/deploy-gos`);
	await expect(page.getByRole('heading', { name: 'Invitation Required' })).toBeVisible({
		timeout: 60_000
	});
	await submitCode(page, A.valid_code.toLowerCase());
	await expect(page.getByRole('button', { name: 'Continue' })).toBeVisible({ timeout: 60_000 });
	await expect(page.getByRole('heading', { name: 'Invitation Required' })).toHaveCount(0);
	await snap(page, 'wizard-opens');
	expect(await activationOf(applicant)).toBe('activated');
	expect(await creditsOf(applicant)).toBe(INVITATION_CREDITS);
});

test('the billing tab offers no card payment', async ({ page }) => {
	const cardRequests: string[] = [];
	page.on('request', (request) => {
		if (/checkout|stripe/i.test(request.url())) cardRequests.push(request.url());
	});
	await signIn(page, applicant);
	await page.goto(`${A.portal_url}/my-dashboard?tab=billing`);
	const buy = page.getByRole('button', { name: 'Buy credits' });
	await expect(buy).toBeDisabled({ timeout: 60_000 });
	await expect(page.getByText('Not available', { exact: true })).toBeVisible();
	await expect(page.getByText('No purchases yet')).toBeVisible();
	await expect(page.getByText(/pay with card|stripe|coming soon/i)).toHaveCount(0);
	await expect(page.getByRole('heading', { name: 'Redeem Voucher' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Enter your invitation code' })).toHaveCount(0);
	await snap(page, 'billing-not-available');
	expect(cardRequests, 'no card flow was started').toEqual([]);
});

test('a code redeemed in the dashboard activates the account and shows the voucher box', async ({
	page
}) => {
	const second = Ed25519KeyIdentity.generate();
	await signIn(page, second);
	await page.goto(`${A.portal_url}/my-dashboard?tab=billing`);
	await page.getByPlaceholder('Enter your invitation code').fill(A.dashboard_code);
	await page.getByRole('button', { name: 'Redeem', exact: true }).click();
	await expect(
		page.getByText(`Account activated, ${INVITATION_CREDITS} credits added`)
	).toBeVisible({ timeout: 60_000 });
	await expect(page.getByRole('heading', { name: 'Redeem Voucher' })).toBeVisible();
	await expect(page.getByRole('link', { name: REQUEST_LINK })).toHaveCount(0);
	await snap(page, 'dashboard-activated');
	expect(await activationOf(second)).toBe('activated');
	expect(await creditsOf(second)).toBe(INVITATION_CREDITS);
});

test('request_deployment refuses a principal that is not activated', async () => {
	const stranger = Ed25519KeyIdentity.generate();
	const reply = JSON.parse(
		await (
			await registryAs(stranger)
		).request_deployment(JSON.stringify({ name: 'no-invitation', network: 'local' }))
	);
	expect(reply.success).toBe(false);
	expect(reply.error).toMatch(/not activated/);
});

test('the activated applicant launches a realm, which costs its price in credits', async ({
	page
}) => {
	test.setTimeout(30 * 60 * 1000);
	await signIn(page, applicant);
	await page.goto(`${A.portal_url}/deploy-gos`);
	const next = page.getByRole('button', { name: 'Continue' });
	const name = page.locator('#name');
	// The first click can land before the wizard has hydrated: click until the next step shows.
	await expect(async () => {
		await next.click();
		await expect(name).toBeVisible({ timeout: 2_000 });
	}).toPass({ timeout: 60_000 });
	await name.fill(A.realm_name);
	await next.click();
	await next.click();
	const launch = page.getByRole('button', { name: 'Launch realm' });
	// The credits load after the page: refresh them until the launch button shows.
	const refresh = page.getByRole('button', { name: 'Refresh' });
	await expect(async () => {
		if (await refresh.isVisible()) await refresh.click();
		await expect(launch).toBeVisible({ timeout: 3_000 });
	}).toPass({ timeout: 60_000 });
	const cost = await page.locator('.deploy-cost').innerText();
	const price = Number(/Cost: (\d+) credits/.exec(cost)?.[1]);
	expect(price, `the review shows a price: "${cost}"`).toBeGreaterThan(0);
	await snap(page, 'review');
	await launch.click();
	await page.waitForURL(/\/my-dashboard\/deployments\?job=/, { timeout: 120_000 });
	await expect(page.getByRole('link', { name: /Visit Realm/ })).toBeVisible({
		timeout: 20 * 60 * 1000
	});
	await snap(page, 'realm-live');
	expect(await creditsOf(applicant), 'the realm took its price').toBe(INVITATION_CREDITS - price);
});
