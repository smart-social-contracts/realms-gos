// A proposal of each kind this realm takes, filed by the founder and voted yes by the founder and the
// second member (wizard.spec.ts launched the realm and had the member join; the wizard sets the voting
// window to zero, so a proposal closes as soon as its outcome is certain):
//   scripts/e2e_ui.sh
//
// Kinds: poll, extension install, extension upgrade, codex install, role assignment, Realms GOS release, realm-wide
// (federal) vote, treasury transaction.
import { expect, test, type Page } from '@playwright/test';
import { extensionVersion, openExtension } from './fixtures/extension';
import {
	founderIdentity,
	loadRealm,
	memberIdentity,
	readOp,
	runOp,
	type StandaloneRealm
} from './fixtures/realm';
import { balanceOf, mint } from './fixtures/ledger';
import { publishNextVersion } from './fixtures/registry';
import { snap } from './fixtures/snap';
import { submitProposal, voteYes, type ProposalForm } from './fixtures/voting';

let realm: StandaloneRealm;
// Titles are told apart by run.
const RUN = Date.now().toString(36);

test.beforeAll(() => {
	realm = loadRealm();
});

async function proposalOf(id: string) {
	const reply = await readOp(realm, 'proposal_get', { proposal: id }, founderIdentity(realm));
	return {
		status: (reply.proposal as { status: string }).status,
		yes: (reply.tally as { yes: number }).yes
	};
}

const proposalStatus = async (id: string) => (await proposalOf(id)).status;

async function installedExtensions(): Promise<Record<string, string>> {
	const listed = await readOp(
		realm,
		'extension_list',
		{ from: 1, count: 500 },
		founderIdentity(realm)
	);
	const rows = listed.extensions as { id: string; version: string }[];
	return Object.fromEntries(rows.map((e) => [e.id, e.version]));
}

/** Files `form` as the founder through the voting page; returns the proposal's id. */
async function file(page: Page, form: ProposalForm): Promise<string> {
	const founder = await openExtension(page, realm, founderIdentity(realm), 'voting');
	try {
		const id = await submitProposal(founder.app, form);
		await snap(page, 'submitted');
		return id;
	} finally {
		await founder.close();
	}
}

/** Both members vote yes on proposal `id`, each on a page of their own. */
async function bothVoteYes(page: Page, id: string) {
	const founderPage = await page.context().newPage();
	const founder = await openExtension(founderPage, realm, founderIdentity(realm), 'voting');
	try {
		await voteYes(founder.app, id);
		await expect
			.poll(() => proposalOf(id), { message: "the founder's yes is counted and leaves it open" })
			.toEqual({ status: 'voting', yes: 1 });
	} finally {
		await founder.close();
	}

	const memberPage = await page.context().newPage();
	const member = await openExtension(memberPage, realm, memberIdentity(), 'voting');
	try {
		await voteYes(member.app, id);
		await snap(memberPage, 'voted');
	} finally {
		await member.close();
	}
}

/** The status of proposal `id` once its vote has closed. */
async function decided(id: string): Promise<string> {
	await expect
		.poll(() => proposalStatus(id), { message: `${id} is decided`, timeout: 120_000 })
		.not.toBe('voting');
	return proposalStatus(id);
}

/** Files `form`, has both members vote yes and expects the proposal to have run. */
async function fileVoteExecute(page: Page, form: ProposalForm) {
	const id = await file(page, form);
	await bothVoteYes(page, id);
	expect(await decided(id), 'the accepted action ran').toBe('executed');
}

test('a poll is voted and accepted', async ({ page }) => {
	const id = await file(page, {
		type: 'poll',
		title: `Plant a tree in the square ${RUN}`,
		description: 'An advisory poll: nothing runs when it passes.'
	});
	await bothVoteYes(page, id);
	// A poll has no action: passing is its end.
	expect(await decided(id)).toBe('accepted');
});

test('an extension install is voted and executed', async ({ page }) => {
	await fileVoteExecute(page, {
		type: 'upgrade',
		title: `Install hello_world ${RUN}`,
		description: 'Install the hello_world extension from the registry.',
		upgrade: {
			target: 'extension',
			packageId: 'hello_world',
			version: extensionVersion('hello_world')
		}
	});
	expect(await installedExtensions()).toHaveProperty(
		'hello_world',
		extensionVersion('hello_world')
	);
});

test('an extension upgrade is voted and executed', async ({ page }) => {
	const newer = publishNextVersion(
		realm,
		'extensions/extensions/hello_world',
		(await installedExtensions()).hello_world
	);
	await fileVoteExecute(page, {
		type: 'upgrade',
		title: `Upgrade hello_world to ${newer} ${RUN}`,
		description: 'Move hello_world to the newer published version.',
		upgrade: { target: 'extension', packageId: 'hello_world', version: newer }
	});
	expect(await installedExtensions()).toHaveProperty('hello_world', newer);
});

test('a codex install is voted and executed', async ({ page }) => {
	const active = await readOp(realm, 'codex_status', { count: 1 }, founderIdentity(realm));
	const [, running] = (active.codex as { id: string }).id.split('@');
	const newer = publishNextVersion(realm, 'codices/codices/syntropia', running);
	await fileVoteExecute(page, {
		type: 'upgrade',
		title: `Install syntropia ${newer} ${RUN}`,
		description: 'Install the newer published syntropia codex.',
		upgrade: { target: 'codex', packageId: 'syntropia', version: newer }
	});
	const status = await readOp(realm, 'codex_status', { count: 1 }, founderIdentity(realm));
	expect((status.codex as { id: string }).id).toBe(`syntropia@${newer}`);
});

// After launch the founder is a member like the others: administering the realm, as releasing it, is
// something a vote gives. A role assignment is a proposal of its own kind, and the next ones need it.
test('a role assignment is voted and executed', async ({ page }) => {
	const founder = founderIdentity(realm);
	const filed = await runOp(
		realm,
		'proposal_create',
		{
			title: `Make the founder an administrator ${RUN}`,
			action: { op: 'role_assign', args: { user: realm.founder, profile: 'admin' } }
		},
		founder
	);
	await bothVoteYes(page, filed.proposal as string);
	expect(await decided(filed.proposal as string)).toBe('executed');
	const me = await readOp(realm, 'me', {}, founder);
	expect((me.user as { profiles: string[] }).profiles).toContain('admin');
});

// The voting form files no release; the realm files one from any member's proposal. (The managed_services page
// files it too, but only when the realm's own policy asks a vote for administrators' calls.)
test('a Realms GOS release is voted and executed', async ({ page }) => {
	const version = `99.0.${Date.now()}`;
	// The realm accepts a release only if its trust policy approves that module; the local realm asks no approval.
	const moduleHash = `sha256:${'ab'.repeat(32)}`;
	const filed = await runOp(
		realm,
		'proposal_create',
		{
			title: `Accept release ${version} ${RUN}`,
			action: { op: 'realm_upgrade', args: { version, module_hash: moduleHash } }
		},
		founderIdentity(realm)
	);
	const id = filed.proposal as string;
	await bothVoteYes(page, id);
	expect(await decided(id), 'the accepted action ran').toBe('executed');
	const monitor = await readOp(realm, 'realm_monitor', {}, founderIdentity(realm));
	expect(monitor.upgrade).toMatchObject({ version, module_hash: moduleHash });
});

test('a realm-wide vote is voted and executed', async ({ page }) => {
	// A realm-wide vote closes at a deadline the realm's rule sets, a voting window long by default. A proposal
	// of its own shortens it, so the vote can end within the run.
	const shortened = await runOp(
		realm,
		'proposal_create',
		{
			title: `Shorten the realm-wide voting window ${RUN}`,
			action: { op: 'quarter_configure', args: { rule: { window: 45 } } }
		},
		founderIdentity(realm)
	);
	await bothVoteYes(page, shortened.proposal as string);
	expect(await decided(shortened.proposal as string)).toBe('executed');

	const founder = await openExtension(page, realm, founderIdentity(realm), 'voting');
	let ballot: string;
	try {
		const voting = founder.app;
		await voting.getByRole('button', { name: 'Realm-wide' }).click();
		await voting.getByRole('button', { name: 'Propose realm-wide vote' }).click();
		await voting.locator('#fv-title').fill(`Keep proposals open until certain ${RUN}`);
		await voting.locator('#fv-operation').fill('governance_update');
		await voting.locator('#fv-args').fill('{"voting_window": 0}');
		await voting.getByRole('button', { name: 'Propose vote' }).click();
		const ballotId = voting.locator('span', { hasText: 'Local ballot:' }).locator('code');
		await expect(ballotId).toBeVisible({ timeout: 60_000 });
		ballot = (await ballotId.innerText()).trim();
		await snap(page, 'proposed');

		await bothVoteYes(page, ballot);
		// realms-extensions#46: a page whose translations load late starts the tab over, on its list of votes. The vote
		// is opened again before it is finalized, and the realm tells whether finalizing worked.
		const finalize = voting.getByRole('button', { name: 'Finalize tally' });
		await expect(async () => {
			if ((await proposalStatus(ballot)) !== 'voting') return;
			if (!(await finalize.isVisible())) {
				await voting
					.getByRole('button', { name: 'Open', exact: true })
					.first()
					.click({ timeout: 5_000 });
			}
			await finalize.click({ timeout: 5_000 });
			await expect.poll(() => proposalStatus(ballot), { timeout: 10_000 }).not.toBe('voting');
		}, 'the vote can be finalized once its deadline has passed').toPass({
			timeout: 120_000,
			intervals: [5_000]
		});
		await snap(page, 'finalized');
	} finally {
		await founder.close();
	}
	expect(await proposalStatus(ballot)).toBe('accepted');
});

// The treasury pays through the realm's ledger. The fund is filled the way it is in life: an invoice is billed and
// paid into the account the realm names, and the realm books it. (The test ledger lets anyone mint.)
test('a treasury transaction is voted and executed', async ({ page }) => {
	const founder = founderIdentity(realm);
	const symbol = 'E2E';
	const run = async (op: string, args: Record<string, unknown>) => {
		const reply = await runOp(realm, op, args, founder);
		expect(reply.ok, `${op}: ${reply.error}`).toBe(true);
		return reply;
	};
	await run('token_register', { symbol, ledger: realm.token_ledger, decimals: 8, fee: 10_000 });
	await run('fund_create', { fund: 'TREASURY' });
	await run('treasury_configure', { config: { source_fund: 'TREASURY' } });
	const billed = await run('invoice_create', {
		payer: realm.founder,
		amount: 2_000_000,
		items: [{ concept: 'treasury', amount: 2_000_000 }],
		symbol
	});
	const payTo = billed.pay_to as { owner: string; subaccount: string };
	await mint(realm, payTo, 2_000_000n);
	// WORKAROUND for realms-gos #450: a fund pays out of the realm's own (default) account, and what an invoice
	// receives sits in its subaccount, which nothing sweeps. Without this second mint the payout would find no tokens.
	await mint(realm, { owner: payTo.owner }, 2_000_000n);
	await run('invoice_refresh', { invoice: billed.invoice });

	const member = memberIdentity().getPrincipal().toText();
	await fileVoteExecute(page, {
		type: 'transaction',
		title: `Pay the member ${RUN}`,
		description: 'A first payment from the treasury.',
		transaction: { fund: 'TREASURY', token: symbol, amount: '1000000', to: member }
	});
	expect(await balanceOf(realm, { owner: member })).toBe(1_000_000n);
});
