// The gos.earth journey, end to end on a local stack:
//   scripts/local_up.sh --gaas                 (Realms product + GaaS orchestras on a local replica)
//   python3 scripts/e2e_gaas_scale.py          (portal test mode, a voucher; writes GAAS_JSON)
//   npx playwright test -c playwright.gaas.config.ts
// A visitor signs in on the portal and launches a realm (Alpha: credits, no payment); the founder walks the
// setup wizard, choosing a voting window of zero (open until certain); registration is open and auto-scaling
// on. Members join through the Terms until the capital is 90% full and the realm proposes its next quarter. A
// member files a proposal through the voting form, the members vote both through, Casals builds the quarter,
// the capital adopts it, and the next member to join lands in it.
//
// GAAS_JSON may name an https network_url (a live replica: no root key is fetched).
import { readFileSync, writeFileSync } from 'node:fs';
import { expect, test, type Browser, type FrameLocator, type Page } from '@playwright/test';
import { Actor, HttpAgent } from '@dfinity/agent';
import { Ed25519KeyIdentity } from '@dfinity/identity';
import { exposeSigner, needsRootKey, openInPortal, readOp, type StandaloneRealm } from '../standalone/fixtures/realm';
import { LAUNCH_TIMEOUT, launchThroughWizard } from '../standalone/fixtures/wizard';

interface Gaas {
	network_url: string;
	portal_url: string;
	registry: string;
	installer: string;
	conductor: string;
	token_ledger: string;
	voucher_code: string;
	realm_name: string;
}

const G: Gaas = JSON.parse(readFileSync(process.env.GAAS_JSON || '/tmp/e2e/gaas.json', 'utf8'));
const CODEX = process.env.E2E_CODEX || 'syntropia';
// While working on the later steps: the realm a run launched (it writes GAAS_JSON.realm.json), reused as is.
const REUSE = process.env.SCALE_REALM_JSON;
const WELCOME = 'Welcome to a realm that grows.';
const MANIFESTO = 'Scaled by its members.';

let shots = 0;
async function snap(page: Page, label: string) {
	const name = `${String(++shots).padStart(2, '0')}-${label}.png`;
	const body = await page.screenshot({ path: test.info().outputPath(name), fullPage: true });
	await test.info().attach(name, { body, contentType: 'image/png' });
}

/** The seed of the portal's deterministic test identity ``index`` (Internet Identity bypass): 0 is the creator. */
function testSeed(index: number): Uint8Array {
	const seed = new Uint8Array(32);
	seed.set([0xed, 0x57, index & 0xff, (index >>> 8) & 0xff, (index >>> 16) & 0xff, (index >>> 24) & 0xff]);
	return seed;
}

function testIdentity(index: number): Ed25519KeyIdentity {
	return Ed25519KeyIdentity.generate(testSeed(index));
}

function seedHex(index: number): string {
	return Buffer.from(testSeed(index)).toString('hex');
}

async function agentAs(identity?: Ed25519KeyIdentity) {
	return HttpAgent.create({ host: G.network_url, identity, shouldFetchRootKey: needsRootKey(G.network_url) });
}

/** Whether the asset canister ``canister`` answers /index.html. */
async function frontendServes(canister: string): Promise<boolean> {
	const idl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) =>
		IDL.Service({
			http_request: IDL.Func(
				[
					IDL.Record({
						url: IDL.Text,
						method: IDL.Text,
						body: IDL.Vec(IDL.Nat8),
						headers: IDL.Vec(IDL.Tuple(IDL.Text, IDL.Text)),
						certificate_version: IDL.Opt(IDL.Nat16)
					})
				],
				[IDL.Record({ status_code: IDL.Nat16 })],
				['query']
			)
		});
	const assets = Actor.createActor<{ http_request: (r: object) => Promise<{ status_code: number }> }>(idl as never, {
		agent: await agentAs(),
		canisterId: canister
	});
	const out = await assets.http_request({ url: '/index.html', method: 'GET', body: [], headers: [], certificate_version: [] });
	return out.status_code === 200;
}

async function redeemVoucher(founder: Ed25519KeyIdentity) {
	const idl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) =>
		IDL.Service({ redeem_voucher: IDL.Func([IDL.Text], [IDL.Text], []) });
	const registry = Actor.createActor<{ redeem_voucher: (c: string) => Promise<string> }>(idl as never, {
		agent: await agentAs(founder),
		canisterId: G.registry
	});
	return JSON.parse(await registry.redeem_voucher(G.voucher_code));
}

interface Job {
	status: string;
	backend_canister_id: string;
	frontend_canister_id: string;
	error: string;
}

async function job(id: string): Promise<Job> {
	const idl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) => {
		const record = IDL.Record({
			status: IDL.Text,
			backend_canister_id: IDL.Text,
			frontend_canister_id: IDL.Text,
			error: IDL.Text
		});
		return IDL.Service({
			get_deployment_job_status: IDL.Func([IDL.Text], [IDL.Variant({ Ok: record, Err: IDL.Text })], ['query'])
		});
	};
	const installer = Actor.createActor<{ get_deployment_job_status: (j: string) => Promise<any> }>(
		idl as never,
		{ agent: await agentAs(), canisterId: G.installer }
	);
	const out = await installer.get_deployment_job_status(id);
	if ('Err' in out) throw new Error(`job ${id}: ${out.Err}`);
	return out.Ok;
}

/**
 * The portal's launch wizard: platform, basics (the name), subnet, review & deploy. Alpha has no payment step:
 * the review shows the realm's price in credits and nothing to pay. (A registry in can_test_mode, as on every
 * local stack, launches without charging them, so the balance is not checked here.) Returns the job id.
 */
async function launchFromPortal(page: Page): Promise<string> {
	await page.goto(`${G.portal_url}/deploy-gos`, { waitUntil: 'networkidle' });
	const next = page.getByRole('button', { name: 'Continue' });
	const name = page.locator('#name');
	// The first click can land before the wizard has hydrated: click until the next step shows.
	await expect(async () => {
		await next.click();
		await expect(name).toBeVisible({ timeout: 2_000 });
	}).toPass({ timeout: 60_000 });
	await name.fill(G.realm_name);
	await snap(page, 'portal-basics');
	await next.click();
	await next.click();
	const launch = page.getByRole('button', { name: 'Launch realm' });
	// The credits load after the page: refresh them until the launch button shows.
	const refresh = page.getByRole('button', { name: 'Refresh' });
	await expect(async () => {
		if (await refresh.isVisible()) await refresh.click();
		await expect(launch).toBeVisible({ timeout: 3_000 });
	}).toPass({ timeout: 60_000 });
	await snap(page, 'portal-review');
	await expect(page.locator('.deploy-cost')).toHaveText(/Cost: \d+ credits/);
	await expect(page.getByText(/pay with card|stripe/i), 'Alpha has no payment step').toHaveCount(0);
	await launch.click();
	await page.waitForURL(/\/my-dashboard\/deployments\?job=/, { timeout: 120_000 });
	await snap(page, 'portal-deployment');
	return new URL(page.url()).searchParams.get('job')!;
}

let realm: StandaloneRealm;

const readIdl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) =>
	IDL.Service({ read: IDL.Func([IDL.Text, IDL.Text], [IDL.Text], ['query']) });

/** A read operation of ``canister`` (the realm or one of its quarters), signed as ``identity``. */
async function readAs(identity: Ed25519KeyIdentity, canister: string, name: string, args = {}) {
	const actor = Actor.createActor<{ read: (n: string, a: string) => Promise<string> }>(readIdl as never, {
		agent: await agentAs(identity),
		canisterId: canister
	});
	return JSON.parse(await actor.read(name, JSON.stringify(args)));
}

/** ``member`` opens ``path`` of the realm in a browser of their own (a portal host signing as them). */
async function asMember<T>(browser: Browser, seat: number, path: string, run: (page: Page, frame: FrameLocator) => Promise<T>) {
	// A browser of their own is not the test's page: it records its video itself.
	const context = await browser.newContext({
		viewport: { width: 1280, height: 900 },
		recordVideo: { dir: test.info().outputPath(`member-${seat}-videos`), size: { width: 1280, height: 900 } }
	});
	const page = await context.newPage();
	try {
		await exposeSigner(page, testIdentity(seat));
		const { frame, close } = await openInPortal(page, realm, path);
		try {
			return await run(page, frame);
		} finally {
			await close();
		}
	} finally {
		await context.close();
	}
}

/**
 * The join page of a test-mode realm drops the portal's session and asks which test identity to join as: member
 * ``seat`` is "Identity seat+1" (the first two are cards, the others are picked by number).
 */
async function pickTestIdentity(page: Page, frame: FrameLocator, seat: number) {
	const picker = frame.locator('#join-custom-identity-number');
	// Now and then the page stays on "Signing you in": load it again.
	let attempts = 0;
	await expect(async () => {
		if (attempts++) await page.reload();
		await expect(picker).toBeVisible({ timeout: 30_000 });
	}).toPass({ timeout: 150_000 });
	const number = seat + 1;
	if (number <= 2) {
		await frame.getByRole('button', { name: new RegExp(`^Identity ${number}`) }).click();
	} else {
		await picker.fill(String(number));
		await frame.getByRole('button', { name: 'Select', exact: true }).click();
	}
	await frame.getByRole('button', { name: /^Continue as/ }).click();
}

/** The Terms step of the join page: Continue stays disabled until the member ticks the agreement. */
async function acceptTerms(page: Page, frame: FrameLocator, seat: number) {
	const agree = frame.locator('#join-terms-agree');
	const proceed = frame.getByRole('button', { name: 'Continue' });
	await expect(agree).toBeVisible({ timeout: 120_000 });
	await expect(proceed, 'Continue waits for the agreement').toBeDisabled();
	if (seat === 1) await snap(page, 'terms');
	// The page can remount once sign-in settles, which clears the tick: tick again until it holds.
	await expect(async () => {
		await agree.check();
		await expect(proceed).toBeEnabled({ timeout: 2_000 });
	}).toPass({ timeout: 60_000 });
	await proceed.click();
}

/** Member ``seat`` joins through the realm's join page, Terms included; returns the quarter it joined. */
async function join(browser: Browser, seat: number): Promise<string> {
	return asMember(browser, seat, '/join', async (page, frame) => {
		await pickTestIdentity(page, frame, seat);
		await acceptTerms(page, frame, seat);
		const quarter = frame.getByText(/JOINING QUARTER/i).locator('xpath=..');
		const joinButton = frame.getByRole('button', { name: 'Join Realm' });
		await expect(joinButton).toBeEnabled({ timeout: 120_000 });
		const target = (await quarter.innerText().catch(() => '')).replace(/\s+/g, ' ');
		await joinButton.click();
		await expect
			.poll(async () => {
				for (const cid of quarterIds) {
					const me = await readAs(testIdentity(seat), cid, 'me').catch(() => ({ ok: false }));
					if (me.ok && me.user) return cid;
				}
				return '';
			}, { message: `member ${seat} joins`, timeout: 120_000, intervals: [2_000] })
			.not.toBe('');
		if (seat === 1 || seat === LAST_SEAT) await snap(page, `join-${seat}`);
		for (const cid of quarterIds) {
			const me = await readAs(testIdentity(seat), cid, 'me').catch(() => ({ ok: false }));
			if (me.ok && me.user) return cid;
		}
		throw new Error(`member ${seat} joined nowhere (page said ${target})`);
	});
}

/** Member ``seat`` files a proposal without an action through the voting extension's form; returns its id. */
async function submitProposal(browser: Browser, seat: number, title: string): Promise<string> {
	await asMember(browser, seat, '/extensions/voting', async (page, frame) => {
		await frame.getByRole('button', { name: 'Submit Proposal' }).click({ timeout: 120_000 });
		await frame.locator('#rt-title').fill(title);
		await frame.locator('#rt-desc').fill(`${title}, filed by member ${seat} through the form.`);
		await snap(page, 'proposal-form');
		await frame.locator('form button[type="submit"]').click();
		await expect(frame.getByText(/submitted — voting is open/)).toBeVisible({ timeout: 60_000 });
		await snap(page, 'proposal-submitted');
	});
	const open = await readAs(testIdentity(seat), realm.realm_backend, 'proposal_list', { status: 'voting' });
	const filed = (open.proposals ?? []).find((p: any) => p.title === title);
	expect(filed, `proposal "${title}" is open for voting`).toBeTruthy();
	return filed.id;
}

/** Member ``seat`` votes yes on each of ``proposals`` in the voting extension. */
async function voteYes(browser: Browser, seat: number, proposals: string[]) {
	await asMember(browser, seat, '/extensions/voting', async (page, frame) => {
		for (const proposal of proposals) {
			const card = frame.locator(`[data-proposal-id="${proposal}"]`);
			await expect(card).toBeVisible({ timeout: 120_000 });
			if (seat === 0 && proposal === proposals[0]) await snap(page, 'vote-open');
			await card.getByRole('button', { name: 'Yes', exact: true }).click();
			await expect
				.poll(async () => {
					const out = await readAs(testIdentity(0), realm.realm_backend, 'proposal_get', { proposal });
					return (out.votes ?? []).some((v: any) => v.voter === testIdentity(seat).getPrincipal().toText());
				}, { message: `member ${seat}'s vote on ${proposal} is counted`, timeout: 60_000, intervals: [1_000] })
				.toBe(true);
		}
	});
}

const LAST_SEAT = 9;
let quarterIds: string[] = [];
let voters: number[] = [];

async function directory() {
	const out = await readOp(realm, 'quarter_directory');
	return (out.quarters ?? []) as { id: string; status: string; population: number; capital: boolean }[];
}

test('a realm born in the portal grows a quarter through a realm-wide vote', async ({ page, browser }) => {
	const founder = testIdentity(0);
	// The founder's page signs as the founder throughout; members get browsers of their own.
	await exposeSigner(page, founder);

	if (REUSE) realm = JSON.parse(readFileSync(REUSE, 'utf8'));
	if (!REUSE) await test.step('a visitor signs in on the portal', async () => {
		await page.goto(`${G.portal_url}/my-dashboard`);
		await expect(page.locator('.principal-text')).toHaveText(founder.getPrincipal().toText(), { timeout: 60_000 });
		await snap(page, 'portal-signed-in');
	});

	if (!REUSE) await test.step('the founder launches the realm from the portal', async () => {
		expect((await redeemVoucher(founder)).success, 'the voucher pays for one realm').toBe(true);
		const id = await launchFromPortal(page);
		let done: Job | undefined;
		await expect
			.poll(
				async () => {
					done = await job(id);
					if (done.status === 'failed') throw new Error(`deployment failed: ${done.error}`);
					return done.status;
				},
				{ message: `job ${id} completes`, timeout: 20 * 60 * 1000, intervals: [5_000] }
			)
			.toBe('completed');
		const port = new URL(G.network_url).port;
		realm = {
			network_url: G.network_url,
			realm_url: `http://${done!.frontend_canister_id}.localhost:${port}`,
			realm_backend: done!.backend_canister_id,
			realm_frontend: done!.frontend_canister_id,
			file_registry: '',
			marketplace: '',
			token_ledger: G.token_ledger,
			founder: founder.getPrincipal().toText(),
			founder_seed: seedHex(0),
			name: G.realm_name
		};
		expect((await readOp(realm, 'status')).status).toBe('setup');
		writeFileSync(`${process.env.GAAS_JSON || '/tmp/e2e/gaas.json'}.realm.json`, JSON.stringify(realm, null, 1));
	});

	if (!REUSE) await test.step(`the founder walks the setup wizard (${CODEX})`, async () => {
		test.setTimeout(LAUNCH_TIMEOUT + 30 * 60 * 1000);
		const { frame, close } = await openInPortal(page, realm, '/setup');
		try {
			await launchThroughWizard(page, frame, realm, {
				codex: CODEX,
				symbol: await ledgerSymbol(),
				welcome: WELCOME,
				manifesto: MANIFESTO,
				votingWindowDays: 0,
				snap
			});
		} finally {
			await close();
		}
		expect((await readOp(realm, 'status')).status).not.toBe('setup');
	});

	await test.step('auto-scaling is on: a test network scales at 9 of 10', async () => {
		const { frame, close } = await openInPortal(page, realm, '/extensions/realm_settings');
		try {
			await frame.locator('.chrome-tab', { hasText: 'Advanced' }).click({ timeout: 120_000 });
			await expect(frame.getByText('Auto-scaling enabled')).toBeVisible({ timeout: 60_000 });
			await snap(page, 'auto-scaling');
		} finally {
			await close();
		}
		const scale = await readOp(realm, 'scale_status');
		expect(scale.enabled, 'auto-scaling is on').toBe(true);
		expect(scale.scale_at, 'a test network scales at 9 of 10').toBe(9);
	});

	await test.step('members join until the capital is 90% full; the realm proposes a quarter', async () => {
		quarterIds = [realm.realm_backend];
		const scaleAt = Number((await readOp(realm, 'scale_status')).scale_at);
		let seat = 0;
		while (Number((await directory())[0].population) < scaleAt) {
			seat += 1;
			expect(await join(browser, seat), `member ${seat} joins the capital`).toBe(realm.realm_backend);
		}
		const scale = await readOp(realm, 'scale_status');
		expect(scale.wanted && scale.in_flight, 'the realm wants a quarter and has proposed one').toBe(true);
		voters = Array.from({ length: seat + 1 }, (_, i) => i);
	});

	let poll = '';
	await test.step('a member files a proposal through the voting form', async () => {
		poll = await submitProposal(browser, 1, 'Plant a tree on the main square');
	});

	await test.step('the members vote the proposals through', async () => {
		const open = await readAs(founder, realm.realm_backend, 'proposal_list', { status: 'voting' });
		const quarterAdd = (open.proposals ?? []).find((p: any) => String(p.title).startsWith('quarter_add'))?.id ?? '';
		expect(quarterAdd, 'the realm filed quarter_add').not.toBe('');
		const filed = await readAs(founder, realm.realm_backend, 'proposal_get', { proposal: quarterAdd });
		expect(filed.proposal.deadline, 'the voting window the founder set (0): open until certain').toBe(0);
		const needed = Math.floor(voters.length / 2) + 1;
		for (const seat of voters.slice(0, needed)) await voteYes(browser, seat, [quarterAdd, poll]);
		// The status turns to executed when the action starts: its result lands when the action returns.
		await expect
			.poll(
				async () => {
					const settled = await readAs(founder, realm.realm_backend, 'proposal_get', { proposal: quarterAdd });
					return `${settled.proposal.status}: ${JSON.stringify(settled.proposal.result)}`;
				},
				{ message: 'the deciding vote ran quarter_add', timeout: 120_000, intervals: [2_000] }
			)
			.toMatch(/^executed: .*"ok":true/);
		const decided = await readAs(founder, realm.realm_backend, 'proposal_get', { proposal: poll });
		expect(decided.proposal.status, 'the member\'s proposal has no action to run: it is accepted').toBe('accepted');
	});

	let quarter = '';
	await test.step('Casals builds the quarter and the capital adopts it', async () => {
		await expect
			.poll(
				async () => {
					const scale = (await readOp(realm, 'scale_status')) as any;
					if (scale.pending?.error) console.log(`adoption: ${scale.pending.error}`);
					const sub = (await directory()).find((q) => !q.capital);
					quarter = sub?.id ?? '';
					return sub?.status ?? 'waiting';
				},
				{ message: 'the new quarter is registered and active', timeout: 20 * 60 * 1000, intervals: [5_000] }
			)
			.toBe('active');
		const info = (await readOp({ ...realm, realm_backend: quarter }, 'realm_info')) as any;
		expect(info.realm.is_capital, 'the new canister runs as a quarter').toBe(false);
		quarterIds = [quarter, realm.realm_backend];
		await asMember(browser, 0, '/extensions/realm_settings', async (p, frame) => {
			await frame.locator('.chrome-tab', { hasText: 'Advanced' }).click({ timeout: 120_000 });
			await expect(frame.getByText(/Quarters\s*\(2\)/)).toBeVisible({ timeout: 120_000 });
			await snap(p, 'two-quarters');
		});
	});

	await test.step('the next member to join lands in the new quarter', async () => {
		expect(await join(browser, LAST_SEAT), 'the next member joins the new quarter').toBe(quarter);
	});
});

// gos-as-a-service#58: the job turns "completed", which the portal shows as "your realm is live", only once the stand
// is built: its frontend serves. It runs after the journey above, whose voucher left the founder with credits.
test('#58: a realm is live only after its frontend has finished uploading', async ({ page }) => {
	const id = await launchFromPortal(page);
	let done: Job | undefined;
	await expect
		.poll(
			async () => {
				done = await job(id);
				if (done.status === 'failed') throw new Error(`deployment failed: ${done.error}`);
				return done.status;
			},
			{ message: `job ${id} completes`, timeout: 20 * 60 * 1000, intervals: [1_000] }
		)
		.toBe('completed');
	expect(await frontendServes(done!.frontend_canister_id), 'the realm is live, so its frontend serves').toBe(true);
});

async function ledgerSymbol(): Promise<string> {
	const idl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) =>
		IDL.Service({ icrc1_symbol: IDL.Func([], [IDL.Text], ['query']) });
	const ledger = Actor.createActor<{ icrc1_symbol: () => Promise<string> }>(idl as never, {
		agent: await agentAs(),
		canisterId: G.token_ledger
	});
	return ledger.icrc1_symbol();
}

