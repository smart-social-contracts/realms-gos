// The life of a tax in the realm, with its real members, on the realm wizard.spec.ts launched (the founder and a
// second member in it) and proposals.spec.ts made the founder an administrator of:
//   scripts/e2e_ui.sh
//
// It runs once on a realm: the realm it starts from has no tax policy yet, and the officer has not joined.
//
// The tax_manager extension has no page that works on this realm (it was written for the earlier realm API and has
// not been ported), so the journey goes through the operations its backend would have called (api/taxes.py), as the
// member who would call them. Where the realm has a page for a step, the step uses it: the member sees, pays and
// refreshes their invoices on the member dashboard. (The realm's decisions are taken by its administrator alone, a
// 1 of 1 policy, so adopting a policy needs no vote here.)
//
// The people: the founder (an administrator) drafts and adopts the policy; the finance officer assesses; the second
// member and the officer are billed; an outsider never joined. What a step did is read back from the realm (its
// rows, the treasury, the ledger), not from a page's text.
import { expect, test, type Page } from '@playwright/test';
import { Ed25519KeyIdentity } from '@dfinity/identity';
import { extensionVersion, openExtension } from './fixtures/extension';
import { balanceOf, mint, transfer } from './fixtures/ledger';
import { done, leavesUnchanged, refused, row, rows } from './fixtures/ops';
import {
	founderIdentity,
	loadRealm,
	memberIdentity,
	readOp,
	type StandaloneRealm
} from './fixtures/realm';
import { snap } from './fixtures/snap';

let realm: StandaloneRealm;

test.beforeAll(() => {
	realm = loadRealm();
});

// The period and the descriptions carry the run, so the runs of a realm's rows can be told apart.
const RUN = Date.now().toString(36);
const PERIOD = `2026-${RUN}`;

const person = (seed: number) => Ed25519KeyIdentity.generate(new Uint8Array(32).fill(seed));
const principal = (who: Ed25519KeyIdentity) => who.getPrincipal().toText();
const founder = () => founderIdentity(realm);
const resident = memberIdentity();
const officer = person(51);
const outsider = person(52);

const SYMBOL = 'E2E';
// A seat of the Economy department, which only the syntropia codex seeds.
const SEAT = 'economy.finance_officer';
const LEDGER_FEE = 10_000n;
// What each tax of the policy asks, in the token's smallest unit.
const RESIDENCE = 1_000_000;
const LEVY = 500_000;
const REVENUE_DECLARED = 182_000;
const REVENUE_OWED = 3_640; // 2.00 % (2 000 000 hundred-millionths) of the declared revenue

const POLICY = {
	symbol: SYMBOL,
	taxes: [
		// Due in 30 days; the administrators are exempt.
		{
			code: 'residence',
			base: 'flat',
			method: 'fixed',
			amount: RESIDENCE,
			exempt_profiles: ['admin'],
			due_days: 30
		},
		// Due the moment it is billed, so an unpaid one is overdue at the next check.
		{ code: 'levy', base: 'flat', method: 'fixed', amount: LEVY, due_days: 0 },
		// Not billed by the realm: each member declares it.
		{ code: 'revenue', base: 'declared', method: 'proportional', rate: 2_000_000 }
	]
};

interface TaxPolicy {
	id: string;
	document: typeof POLICY;
	description: string;
	status: string;
	adopted_at: number;
}
interface TaxDeclaration {
	id: string;
	declarant: string;
	period: string;
	base: string;
	key: string;
	amount: number;
	status: string;
	invoice: string;
}
interface Invoice {
	id: string;
	payer: string;
	amount: number;
	symbol: string;
	due_at: number;
	status: string;
	reason: string;
	paid_at: number;
}
interface Assessed {
	count: number;
	skipped: number;
	invoices: string[];
}

const TAX_ROWS = ['TaxPolicy', 'TaxDeclaration', 'Invoice'];

/** The invoice `payer` was billed for `tax` in `PERIOD`. */
async function invoiceOf(payer: Ed25519KeyIdentity, tax: string): Promise<Invoice> {
	const declaration = (await rows<TaxDeclaration>(realm, 'TaxDeclaration')).find(
		(d) => d.key === `User:${principal(payer)}|${tax}|${PERIOD}`
	);
	expect(declaration, `${tax} was assessed for ${principal(payer)}`).toBeDefined();
	return row<Invoice>(realm, 'Invoice', declaration!.invoice);
}

/** What the realm's treasury holds of the token, in the fund the paid invoices are booked to. */
async function treasuryBalance(): Promise<number> {
	const view = await readOp(realm, 'treasury_view', {}, founder());
	const fund = (view.config as { source_fund?: string }).source_fund ?? 'ROOT';
	return (view.funds as Record<string, Record<string, number>>)[fund]?.[SYMBOL] ?? 0;
}

/** Where `invoice` is paid into. */
async function payTo(invoice: Invoice, payer: Ed25519KeyIdentity) {
	const mine = await readOp(realm, 'my_invoices', { count: 100 }, payer);
	const shown = (
		mine.invoices as { id: string; pay_to: { owner: string; subaccount: string } }[]
	).find((i) => i.id === invoice.id);
	expect(shown, `${invoice.id} is in ${principal(payer)}'s invoices`).toBeDefined();
	return shown!.pay_to;
}

test.describe.configure({ mode: 'serial' });

let policy: string;

test('the finance officer is seated', async () => {
	await done(realm, 'realm_join', {}, officer);
	await done(realm, 'role_assign', { user: principal(officer), profile: SEAT }, founder());
	const users = await rows<{ id: string; profiles: string[] }>(realm, 'User');
	expect(users.find((u) => u.id === principal(officer))?.profiles).toContain(SEAT);
});

test('assessing before a policy is adopted is refused', async () => {
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		await refused(realm, 'tax_assess', { period: PERIOD }, officer, /no tax policy is adopted/);
	});
});

test('only an administrator drafts and adopts a policy, only the finance seat assesses', async () => {
	const draft = { document: POLICY, description: `Refused ${RUN}` };
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		for (const who of [resident, officer, outsider]) {
			await refused(realm, 'tax_policy_draft', draft, who, /needs tax\.manage/);
			await refused(
				realm,
				'tax_policy_propose',
				{ policy: 'TAX-POLICY-1' },
				who,
				/needs tax\.manage/
			);
		}
		for (const who of [resident, outsider]) {
			await refused(realm, 'tax_assess', { period: PERIOD }, who, /needs tax\.assess/);
		}
	});
});

test('a policy that is not valid does not become a draft', async () => {
	const bad = { symbol: SYMBOL, taxes: [{ code: 'lottery', base: 'flat', method: 'lottery' }] };
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		await refused(realm, 'tax_policy_draft', { document: bad }, founder(), /method one of/);
	});
});

test('the administrator drafts the policy and adopts it', async () => {
	const drafted = await done(
		realm,
		'tax_policy_draft',
		{ document: POLICY, description: `Taxes ${RUN}` },
		founder()
	);
	policy = drafted.policy as string;
	expect(await row<TaxPolicy>(realm, 'TaxPolicy', policy)).toMatchObject({
		document: POLICY,
		description: `Taxes ${RUN}`,
		status: 'draft'
	});

	await done(realm, 'tax_policy_propose', { policy }, founder());
	const adopted = await row<TaxPolicy>(realm, 'TaxPolicy', policy);
	expect(adopted.status).toBe('adopted');
	expect(adopted.adopted_at).toBeGreaterThan(0);
});

test('an adopted policy cannot be adopted again', async () => {
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		await refused(
			realm,
			'tax_policy_propose',
			{ policy },
			founder(),
			/only a draft can be adopted/
		);
	});
});

test('the finance officer cannot bill a tax the members declare, or one the policy lacks', async () => {
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		await refused(
			realm,
			'tax_assess',
			{ period: PERIOD, tax: 'revenue' },
			officer,
			/self-declared/
		);
		await refused(
			realm,
			'tax_assess',
			{ period: PERIOD, tax: 'lottery' },
			officer,
			/has no tax 'lottery'/
		);
	});
});

test('the finance officer assesses the members', async () => {
	const out = await done<Assessed>(realm, 'tax_assess', { period: PERIOD }, officer);
	// Residence: everyone but the administrator. Levy: everyone.
	const members = (await rows<{ id: string; status: string }>(realm, 'Member')).filter(
		(m) => m.status === 'active'
	);
	expect(out.count).toBe(members.length * 2 - 1);
	expect(out.skipped).toBe(0);

	for (const who of [resident, officer]) {
		expect(await invoiceOf(who, 'residence')).toMatchObject({
			payer: `User:${principal(who)}`,
			amount: RESIDENCE,
			symbol: SYMBOL,
			status: 'pending'
		});
		expect(await invoiceOf(who, 'levy')).toMatchObject({ amount: LEVY, status: 'pending' });
	}
	const declarations = await rows<TaxDeclaration>(realm, 'TaxDeclaration');
	const mine = declarations.filter((d) => d.period === PERIOD);
	expect(mine.map((d) => d.key)).not.toContain(`User:${principal(founder())}|residence|${PERIOD}`);
	expect(mine.every((d) => d.status === 'assessed')).toBe(true);
	expect(mine.some((d) => d.base === 'revenue')).toBe(false);
});

test('assessing the same period again bills nobody twice', async () => {
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		const out = await done<Assessed>(realm, 'tax_assess', { period: PERIOD }, officer);
		expect(out.count).toBe(0);
		expect(out.skipped).toBeGreaterThan(0);
	});
});

/** `who`'s dashboard, and the row of `invoice` in its list (the dashboard names an invoice by its reason). */
async function invoiceRow(page: Page, who: Ed25519KeyIdentity, invoice: Invoice) {
	const dashboard = await openExtension(page, realm, who, 'member_dashboard');
	const line = dashboard.app
		.locator('tr, article')
		.filter({ has: dashboard.app.getByText(invoice.reason, { exact: true }) });
	return { ...dashboard, line };
}

test('a member pays the residence tax and the treasury receives it', async ({ page }) => {
	const invoice = await invoiceOf(resident, 'residence');
	const target = await payTo(invoice, resident);
	await mint(realm, { owner: principal(resident) }, BigInt(RESIDENCE) + LEDGER_FEE);
	const treasuryBefore = await treasuryBalance();
	const walletBefore = await balanceOf(realm, { owner: principal(resident) });

	const { app, line, close } = await invoiceRow(page, resident, invoice);
	try {
		await expect(line).toContainText('Pending');
		await line.getByRole('button', { name: /Pay/ }).click();
		await expect(app.getByText(target.owner).first()).toBeVisible();
		await snap(page, 'how to pay');
		// The dialog is taller than the window, so its Close button is out of reach (realms-extensions#41): use the
		// cross at its top.
		await app.getByRole('button', { name: '×', exact: true }).click();

		await transfer(realm, resident, target, BigInt(RESIDENCE));
		expect(await balanceOf(realm, { owner: principal(resident) })).toBe(
			walletBefore - BigInt(RESIDENCE) - LEDGER_FEE
		);

		await line.getByRole('button', { name: /Refresh/ }).click();
		await expect(line).toContainText('Paid');
		await snap(page, 'paid');
	} finally {
		await close();
	}
	expect(await row<Invoice>(realm, 'Invoice', invoice.id)).toMatchObject({ status: 'paid' });
	expect((await row<Invoice>(realm, 'Invoice', invoice.id)).paid_at).toBeGreaterThan(0);
	const declaration = (await rows<TaxDeclaration>(realm, 'TaxDeclaration')).find(
		(d) => d.invoice === invoice.id
	);
	expect(declaration?.status).toBe('settled');
	expect(await treasuryBalance()).toBe(treasuryBefore + RESIDENCE);
});

test('a payment that falls short does not settle the invoice', async () => {
	const invoice = await invoiceOf(officer, 'residence');
	const target = await payTo(invoice, officer);
	await mint(realm, { owner: principal(officer) }, BigInt(RESIDENCE) + LEDGER_FEE);
	await transfer(realm, officer, target, BigInt(RESIDENCE) - 1n);

	const treasuryBefore = await treasuryBalance();
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		const checked = await done(realm, 'invoice_check', { invoice: invoice.id }, officer);
		expect(checked.status).toBe('pending');
	});
	expect(await treasuryBalance()).toBe(treasuryBefore);
});

test('nobody checks another member invoice', async () => {
	const invoice = await invoiceOf(officer, 'residence');
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		await refused(realm, 'invoice_check', { invoice: invoice.id }, resident, /is not yours/);
	});
});

test('an unpaid levy is overdue, and still can be paid', async ({ page }) => {
	const invoice = await invoiceOf(resident, 'levy');
	const target = await payTo(invoice, resident);

	const { line, close } = await invoiceRow(page, resident, invoice);
	try {
		await line.getByRole('button', { name: /Refresh/ }).click();
		await expect(line).toContainText('Overdue');
		await snap(page, 'overdue');
	} finally {
		await close();
	}
	expect((await row<Invoice>(realm, 'Invoice', invoice.id)).status).toBe('overdue');

	await mint(realm, { owner: principal(resident) }, BigInt(LEVY) + LEDGER_FEE);
	await transfer(realm, resident, target, BigInt(LEVY));
	await done(realm, 'invoice_check', { invoice: invoice.id }, resident);
	expect((await row<Invoice>(realm, 'Invoice', invoice.id)).status).toBe('paid');
});

test('a member declares the revenue and is billed its share once', async () => {
	const declared = await done(
		realm,
		'tax_declare',
		{ period: PERIOD, base: 'revenue', amount: REVENUE_DECLARED },
		resident
	);
	expect(declared.owed).toBe(REVENUE_OWED);
	expect(await row<Invoice>(realm, 'Invoice', declared.invoice as string)).toMatchObject({
		payer: `User:${principal(resident)}`,
		amount: REVENUE_OWED,
		status: 'pending'
	});

	await leavesUnchanged(realm, TAX_ROWS, async () => {
		const again = { period: PERIOD, base: 'revenue', amount: 1 };
		await refused(realm, 'tax_declare', again, resident, /has already declared revenue/);
		await refused(
			realm,
			'tax_declare',
			{ ...again, base: 'residence' },
			officer,
			/assessed by the realm/
		);
	});
});

// realms-extensions#41: the member dashboard reports a payment it found with ctx.openModal, which its manifest does
// not declare: the realm answers "Something went wrong: Capability 'modal' not declared" instead of "paid". The
// invoice itself is paid.
test.fixme('the member is told the invoice is paid', async ({ page }) => {
	const invoice = await invoiceOf(officer, 'levy');
	await mint(realm, { owner: principal(officer) }, BigInt(LEVY) + LEDGER_FEE);
	await transfer(realm, officer, await payTo(invoice, officer), BigInt(LEVY));
	const { frame, line, close } = await invoiceRow(page, officer, invoice);
	try {
		await line.getByRole('button', { name: /Refresh/ }).click();
		await expect(frame.getByText(/paid/i).first()).toBeVisible();
		await expect(frame.getByText('Something went wrong')).toHaveCount(0);
	} finally {
		await close();
	}
});

// realms-gos#466: a tax document is checked only for its codes, bases, methods and rates (api/taxes.py check): an
// amount that is negative or not a number, and a due date in the past, are drafted, and once adopted a text amount
// makes every tax_assess trap ("invalid literal for int()") instead of answering with a refusal.
test.fixme('a tax document with an amount that cannot be billed is refused', async () => {
	const fixed = { code: 'x', base: 'flat', method: 'fixed' };
	const documents = [
		{ ...fixed, amount: -5 },
		{ ...fixed, amount: 'abc' },
		{ ...fixed, amount: 5, due_days: -3 }
	];
	await leavesUnchanged(realm, TAX_ROWS, async () => {
		for (const tax of documents) {
			await refused(
				realm,
				'tax_policy_draft',
				{ document: { symbol: SYMBOL, taxes: [tax] } },
				founder(),
				/amount|due_days/
			);
		}
	});
});

// realms-extensions#40: tax_manager was written for the earlier realm API (`ctx.tax`), which this realm does not have,
// and it is not ported. Installed, its page opens with its title and description and nothing else: no policy, no
// draft form, no error.
test.fixme('the tax_manager page shows the adopted policy', async ({ page }) => {
	const version = extensionVersion('tax_manager');
	await done(realm, 'extension_install', { extension: 'tax_manager', version }, founder());
	const { app, close } = await openExtension(page, realm, founder(), 'tax_manager');
	try {
		await expect(app.getByText('residence')).toBeVisible();
	} finally {
		await close();
	}
});

// realms-gos#467: there is no operation to waive or cancel an invoice or a tax declaration, so a tax cannot be
// forgiven.
test.fixme('the finance officer waives a member invoice', async () => {
	const invoice = await invoiceOf(officer, 'levy');
	await done(realm, 'invoice_waive', { invoice: invoice.id }, officer);
	expect((await row<Invoice>(realm, 'Invoice', invoice.id)).status).toBe('cancelled');
});
