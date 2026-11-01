// How a realm's money is budgeted and spent, with the realm's real members, on the realm wizard.spec.ts launched
// (the founder and a second member in it) and proposals.spec.ts made the founder an administrator of and gave a
// ledger and a TREASURY fund:
//   scripts/e2e_ui.sh
//
// The journey: the realm opens a fiscal period and adopts a rule that splits what the treasury receives between two
// department funds; a payment is made and the funds are credited; a budget is adopted for the Infrastructure fund; the
// founder spends from the fund on the vault page, within the budget; an overspend is refused; the administrator gives
// the second member the finance role, with which they amend the budget, and takes it away again. After each refusal the
// realm's rows are what they were.
//
// The realm's decisions are taken by its administrator alone, a 1 of 1 policy, so the role and the extension need no
// vote here. The specs before this one have made members, and a realm-wide proposal needs more than half of them: the
// founder and the second member alone can no longer carry a vote. proposals.spec.ts covers the votes.
//
// The administrator installs the budget_manager extension, but its page cannot run on this realm (see the fixme
// below), so the steps it should offer are the realm's operations called as the member who would call them. Spending
// has a page: the vault's. What a step did is read back from the realm, not from the page's text.
//
// The tests share one realm and run in this order. A realm has one fiscal period open at a time: a second run on
// the same realm books into the first run's period, and its funds keep what the first run left.
import { expect, test } from '@playwright/test';
import { Ed25519KeyIdentity } from '@dfinity/identity';
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
import { snap } from './fixtures/snap';

let realm: StandaloneRealm;

test.beforeAll(() => {
	realm = loadRealm();
});

// Ids carry the run, so the runs of a realm's rows can be told apart.
const RUN = Date.now().toString(36);

const founder = () => founderIdentity(realm);
const member = memberIdentity();
const payee = Ed25519KeyIdentity.generate(new Uint8Array(32).fill(51));
const principal = (who: Ed25519KeyIdentity) => who.getPrincipal().toText();

// The departments' funds the codex seeded, and the ledger proposals.spec.ts registered.
const SOURCE = 'TREASURY';
const ROADS = 'DEPT-INFRA';
const WELFARE = 'DEPT-SOC';
const SYMBOL = 'E2E';
const FEE = 10_000;
// The vault pays with the default category, so a budget for it plans for that category.
const CATEGORY = 'transfer';

interface Fund {
	id: string;
	balances: Record<string, number>;
	allocation_rule: string;
}
interface Period {
	id: string;
	status: string;
	starts_at: number;
	ends_at: number;
}
interface Rule {
	id: string;
	source_fund: string;
	rules: Record<string, number>;
	status: string;
}
interface Budget {
	id: string;
	fund: string;
	period: string;
	category: string;
	planned: number;
	actual: number;
	status: string;
}

/** The realm's rows of `entity`, as an administrator reads them. */
async function rows<T>(entity: string): Promise<T[]> {
	const out = await readOp(realm, 'realm_export', { entity, from: 1, count: 500 }, founder());
	return out.rows as T[];
}

async function row<T extends { id: string }>(entity: string, id: string): Promise<T> {
	const found = (await rows<T>(entity)).find((r) => r.id === id);
	expect(found, `${entity} ${id} is in the realm`).toBeDefined();
	return found!;
}

type Reply = Record<string, unknown>;

/** Calls `op` as `who` and expects the realm to have done it. */
async function done(op: string, args: Record<string, unknown>, who: Ed25519KeyIdentity) {
	const reply: Reply = await runOp(realm, op, args, who);
	expect(reply.ok, `${op}: ${reply.error}`).toBe(true);
	return reply;
}

/** Calls `op` as `who` and expects the realm to have refused it with a message that says why. */
async function refused(
	op: string,
	args: Record<string, unknown>,
	who: Ed25519KeyIdentity,
	why: RegExp
) {
	const reply: Reply = await runOp(realm, op, args, who);
	expect(reply.ok, `${op} was refused`).toBe(false);
	expect(reply.error).toMatch(why);
}

/** Runs `act` and expects the realm's rows of `entities` to be what they were: a refusal changes nothing. */
async function leavesUnchanged(entities: string[], act: () => Promise<void>) {
	const snapshot = () => Promise.all(entities.map((entity) => rows(entity)));
	const before = await snapshot();
	await act();
	expect(await snapshot(), `${entities.join(', ')} are unchanged`).toEqual(before);
}

/** The fiscal period that covers now and is open, if there is one. */
async function openPeriod(): Promise<Period | undefined> {
	const now = Math.floor(Date.now() / 1000);
	return (await rows<Period>('FiscalPeriod')).find(
		(p) => p.status === 'open' && p.starts_at <= now && now < p.ends_at
	);
}

/** The adopted budget for the roads fund's payments in the open period, if there is one. */
async function adoptedBudget(): Promise<Budget | undefined> {
	const period = await openPeriod();
	return (await rows<Budget>('Budget')).find(
		(b) =>
			b.status === 'adopted' &&
			b.fund === ROADS &&
			b.period === period?.id &&
			b.category === CATEGORY
	);
}

/** What `fund` holds of the ledger's token. */
async function held(fund: string): Promise<number> {
	return (await row<Fund>('Fund', fund)).balances[SYMBOL] ?? 0;
}

// What the journey goes through, one test after the other.
let period: string;
let rule: string;
let budget: string;

test.describe.configure({ mode: 'serial' });

test.afterAll(async () => {
	// The treasury splits payments only while the realm says so; other specs expect it as the wizard left it.
	await runOp(realm, 'treasury_configure', { config: { auto_allocate: false } }, founder());
});

test('the realm opens a fiscal period, and only the finance role can', async () => {
	const now = Math.floor(Date.now() / 1000);
	const current = await openPeriod();
	if (current) {
		period = current.id;
	} else {
		period = `FY-${RUN}`;
		const day = 24 * 60 * 60;
		const open = { period, starts_at: now - day, ends_at: now + 30 * day };
		await leavesUnchanged(['FiscalPeriod'], async () => {
			await refused('period_open', open, member, /needs org\.manage_budget/);
		});
		await done('period_open', open, founder());
	}
	expect(await row<Period>('FiscalPeriod', period)).toMatchObject({ status: 'open' });

	// Periods follow one another: this one cannot start before the open one ends.
	await leavesUnchanged(['FiscalPeriod'], async () => {
		await refused(
			'period_open',
			{ period: `Overlap-${RUN}`, starts_at: now, ends_at: now + 60 },
			founder(),
			/starts before fiscal period \S+ ends/
		);
	});
});

test('the allocation rule is adopted by the finance role only', async () => {
	const shares = { [ROADS]: 60, [WELFARE]: 30 };
	await leavesUnchanged(['AllocationRule', 'Fund'], async () => {
		const draft = { source_fund: SOURCE, rules: shares };
		await refused('allocation_rule_draft', draft, member, /needs org\.manage_budget/);
		await refused(
			'allocation_rule_draft',
			{ source_fund: SOURCE, rules: { [ROADS]: 80, [WELFARE]: 30 } },
			founder(),
			/at most 100/
		);
		await refused(
			'allocation_rule_draft',
			{ source_fund: SOURCE, rules: { [`NOPE-${RUN}`]: 10 } },
			founder(),
			/unknown Fund/
		);
	});

	const drafted = await done(
		'allocation_rule_draft',
		{ source_fund: SOURCE, rules: shares },
		founder()
	);
	rule = drafted.rule as string;
	expect(await row<Rule>('AllocationRule', rule)).toMatchObject({
		source_fund: SOURCE,
		rules: shares,
		status: 'draft'
	});
	expect((await row<Fund>('Fund', SOURCE)).allocation_rule, 'a draft rules nothing').not.toBe(rule);

	await leavesUnchanged(['AllocationRule', 'Fund'], async () => {
		await refused('allocation_rule_propose', { rule }, member, /needs org\.manage_budget/);
	});
	await done('allocation_rule_propose', { rule }, founder());
	expect(await row<Rule>('AllocationRule', rule)).toMatchObject({ status: 'adopted' });
	expect((await row<Fund>('Fund', SOURCE)).allocation_rule).toBe(rule);

	await leavesUnchanged(['AllocationRule', 'Fund'], async () => {
		await refused('allocation_rule_propose', { rule }, founder(), /only a draft can be adopted/);
	});
	await done('treasury_configure', { config: { auto_allocate: true } }, founder());
});

// The fund is filled the way proposals.spec.ts fills TREASURY: an invoice is billed and paid into the account the
// realm names, and the realm books it, now splitting it by the adopted rule.
test('a payment into the treasury is split between the department funds', async () => {
	const amount = 1_000_000;
	const before = {
		source: await held(SOURCE),
		roads: await held(ROADS),
		welfare: await held(WELFARE)
	};

	const billed = await done(
		'invoice_create',
		{ payer: realm.founder, amount, symbol: SYMBOL, reason: `Tax: budget ${RUN}` },
		founder()
	);
	const payTo = billed.pay_to as { owner: string; subaccount: string };
	await mint(realm, payTo, BigInt(amount));
	// WORKAROUND for realms-gos #450: a fund pays out of the realm's own (default) account, and what an invoice
	// receives sits in its subaccount, which nothing sweeps. Without this second mint the payout would find no tokens.
	await mint(realm, { owner: payTo.owner }, BigInt(amount));
	await done('invoice_refresh', { invoice: billed.invoice }, founder());

	expect(
		(await row<{ id: string; status: string }>('Invoice', billed.invoice as string)).status
	).toBe('paid');
	expect(await held(ROADS)).toBe(before.roads + 600_000);
	expect(await held(WELFARE)).toBe(before.welfare + 300_000);
	expect(await held(SOURCE), 'what no rule gives away stays in the treasury').toBe(
		before.source + 100_000
	);
});

test('the finance role adopts a budget for the roads, and only it can', async () => {
	const draft = { fund: ROADS, period, category: CATEGORY, planned: 300_000 };
	// What the period's earlier budget for this category has spent carries over to its successor.
	const carried = (await adoptedBudget())?.actual ?? 0;
	await leavesUnchanged(['Budget'], async () => {
		await refused('budget_draft', draft, member, /needs org\.manage_budget/);
		await refused(
			'budget_draft',
			{ ...draft, period: `NOPE-${RUN}` },
			founder(),
			/unknown FiscalPeriod/
		);
	});

	budget = (await done('budget_draft', draft, founder())).budget as string;
	expect(await row<Budget>('Budget', budget)).toMatchObject({
		...draft,
		actual: 0,
		status: 'draft'
	});

	await leavesUnchanged(['Budget'], async () => {
		await refused('budget_propose', { budget }, member, /needs org\.manage_budget/);
	});
	await done('budget_propose', { budget }, founder());
	expect(await row<Budget>('Budget', budget)).toMatchObject({ status: 'adopted', actual: carried });

	await leavesUnchanged(['Budget'], async () => {
		await refused('budget_propose', { budget }, founder(), /only a draft for an open period/);
	});
});

test('the founder spends from the roads fund on the vault page, within the budget', async ({
	page
}) => {
	const spent = 200_000;
	const before = {
		fund: await held(ROADS),
		actual: (await row<Budget>('Budget', budget)).actual,
		payee: await balanceOf(realm, { owner: principal(payee) }),
		// The fund pays out of the realm's own ledger account.
		realm: await balanceOf(realm, { owner: realm.realm_backend })
	};

	const { app, frame, close } = await openExtension(page, realm, founder(), 'vault');
	try {
		await app.getByRole('button', { name: 'Send', exact: true }).first().click();
		await app.locator('#v-fund').selectOption(ROADS);
		await expect(app.getByText(`Available in ${ROADS}`)).toBeVisible();
		await snap(page, 'the roads fund and its balance');
		await app.locator('#v-to').fill(principal(payee));
		await app.locator('#v-amount').fill((spent / 1e8).toFixed(8));
		await app.getByRole('button', { name: 'Send', exact: true }).last().click();
		await frame.getByRole('button', { name: 'Send', exact: true }).last().click();
		await expect(app.getByText(/Sent .* to /)).toBeVisible({ timeout: 60_000 });
		await snap(page, 'sent');
	} finally {
		await close();
	}

	expect(await balanceOf(realm, { owner: principal(payee) })).toBe(before.payee + BigInt(spent));
	expect(await held(ROADS), 'the fund pays the amount and the ledger fee').toBe(
		before.fund - spent - FEE
	);
	expect(
		await balanceOf(realm, { owner: realm.realm_backend }),
		'the ledger charged the fee the books show'
	).toBe(before.realm - BigInt(spent + FEE));
	expect(
		(await row<Budget>('Budget', budget)).actual,
		"the budget's actual grew by the payment"
	).toBe(before.actual + spent);
});

test('a payment the fund cannot cover is refused and moves nothing', async ({ page }) => {
	const tooMuch = (await held(ROADS)) + 1;
	const outsider = Ed25519KeyIdentity.generate(new Uint8Array(32).fill(52));
	const pay = { fund: ROADS, to: principal(payee), amount: tooMuch, symbol: SYMBOL };

	await leavesUnchanged(['Fund', 'Budget', 'Transfer', 'LedgerEntry'], async () => {
		await refused('transfer_create', pay, member, /needs transfer\.create/);
		await refused('transfer_create', pay, outsider, /needs transfer\.create/);
		await refused('transfer_create', pay, founder(), /fund DEPT-INFRA holds \d+ E2E, less than/);

		const { app, frame, close } = await openExtension(page, realm, founder(), 'vault');
		try {
			await app.getByRole('button', { name: 'Send', exact: true }).first().click();
			await app.locator('#v-fund').selectOption(ROADS);
			await app.locator('#v-to').fill(principal(payee));
			await app.locator('#v-amount').fill((tooMuch / 1e8).toFixed(8));
			await app.getByRole('button', { name: 'Send', exact: true }).last().click();
			await frame.getByRole('button', { name: 'Send', exact: true }).last().click();
			await expect(app.getByText(/holds \d+ E2E, less than/)).toBeVisible({ timeout: 60_000 });
			await snap(page, 'refused');
		} finally {
			await close();
		}
	});
});

test('the administrator gives the member the finance role, with which they amend the budget, and takes it away', async () => {
	const profile = 'economy.finance_officer';
	const amend = { budget, planned: 400_000 };
	await leavesUnchanged(['Budget'], async () => {
		await refused('budget_amend', amend, member, /needs org\.manage_budget/);
	});

	await done('role_assign', { user: principal(member), profile }, founder());
	const spentSoFar = (await row<Budget>('Budget', budget)).actual;
	const amended = await done('budget_amend', amend, member);
	expect(amended.amends).toBe(budget);
	expect(await row<Budget>('Budget', budget)).toMatchObject({ status: 'amended' });
	expect(await row<Budget>('Budget', amended.budget as string)).toMatchObject({
		fund: ROADS,
		category: CATEGORY,
		planned: 400_000,
		actual: spentSoFar,
		status: 'adopted'
	});

	await done('role_revoke', { user: principal(member), profile }, founder());
	await leavesUnchanged(['Budget'], async () => {
		await refused(
			'budget_amend',
			{ budget: amended.budget, planned: 1 },
			member,
			/needs org\.manage_budget/
		);
	});
});

test('the administrator installs budget_manager from the registry', async () => {
	const version = extensionVersion('budget_manager');
	await done('extension_install', { extension: 'budget_manager', version }, founder());
	const listed = await readOp(realm, 'extension_list', { from: 1, count: 500 }, founder());
	const installed = (listed.extensions as { id: string; version: string }[]).find(
		(e) => e.id === 'budget_manager'
	);
	expect(installed).toMatchObject({ version });
});

// realms-extensions#40: the pinned budget_manager is the pre-slim extension. Its backend imports `ctx` from ggg_sdk,
// which the slim realm's sandbox does not provide, and its manifest declares no operations, so none of its functions
// can run. The ported 2.0.0 is only on the wip/2.0.0-port branch.
test.fixme('the budget page shows the treasury overview and the allocation rule', async ({
	page
}) => {
	const { app, close } = await openExtension(page, realm, founder(), 'budget_manager');
	try {
		await expect(app.getByRole('heading', { name: 'Budget management' })).toBeVisible();
		await app.getByRole('button', { name: 'Allocation' }).click();
		const { allocation_rule } = await row<Fund>('Fund', SOURCE);
		await expect(app.getByText(allocation_rule)).toBeVisible();
	} finally {
		await close();
	}
});

// realms-gos#465: the planned amount of a budget limits nothing: transfer_create checks the fund's balance only, so a
// payment that takes a budget past its plan goes through.
test.fixme('a payment past the budget of its category is refused and moves nothing', async () => {
	const { planned, actual } = (await adoptedBudget())!;
	const pay = {
		fund: ROADS,
		to: principal(payee),
		amount: Math.max(planned - actual, 0) + 1,
		symbol: SYMBOL
	};
	await leavesUnchanged(['Fund', 'Budget', 'Transfer'], async () => {
		await refused('transfer_create', pay, founder(), /budget/);
	});
});

// realms-gos#464: budget_draft takes any integer: a negative plan is stored as a draft that could be adopted.
test.fixme('a budget with a negative amount is refused', async () => {
	await leavesUnchanged(['Budget'], async () => {
		await refused(
			'budget_draft',
			{ fund: ROADS, period: (await openPeriod())!.id, category: `negative-${RUN}`, planned: -5 },
			founder(),
			/positive|negative/
		);
	});
});
