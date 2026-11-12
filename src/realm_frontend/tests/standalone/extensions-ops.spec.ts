// The main operation of each installed extension, one test per row of the table below, on the realm
// wizard.spec.ts launched (the founder and a second member in it) and proposals.spec.ts made the founder
// an administrator of:
//   scripts/e2e_ui.sh
//
// A row says what the extension exists for: the page it opens, what the person does there and what
// they see or what the realm then holds. A row that cannot run on a local realm is a `fixme` with its reason.
import { readFileSync } from 'node:fs';
import { expect, test, type FrameLocator, type Page } from '@playwright/test';
import { Ed25519KeyIdentity } from '@dfinity/identity';
import { openExtension, type ExtensionPage } from './fixtures/extension';
import {
	founderIdentity,
	loadRealm,
	memberIdentity,
	readOp,
	runOp,
	type StandaloneRealm
} from './fixtures/realm';
import { balanceOf } from './fixtures/ledger';
import { snap } from './fixtures/snap';

let realm: StandaloneRealm;

test.beforeAll(() => {
	realm = loadRealm();
});

interface Tools {
	page: Page;
	app: ExtensionPage;
	/** The realm's frame, for its dialogs. */
	frame: FrameLocator;
}

interface Row {
	id: string;
	path: string;
	/** What the person does on the page. */
	action: string;
	/** What they see, or what the realm holds afterwards. */
	expected: string;
	/** Who opens the page; the founder unless told otherwise. */
	as?: 'member';
	/** What the realm needs before the page opens. */
	setup?: () => Promise<void>;
	run: (tools: Tools) => Promise<void>;
}

/** The realm's rows of `entity` (as an administrator reads them). */
async function rows(entity: string): Promise<Record<string, unknown>[]> {
	const out = await readOp(
		realm,
		'realm_export',
		{ entity, from: 1, count: 500 },
		founderIdentity(realm)
	);
	return out.rows as Record<string, unknown>[];
}

/** The extension reports each result in a dialog of the realm's frame; this closes it. */
async function closeDialog(frame: FrameLocator) {
	await frame.getByRole('button', { name: 'Close', exact: true }).last().click();
}

async function installedExtensions(): Promise<Record<string, string>> {
	const listed = await readOp(
		realm,
		'extension_list',
		{ from: 1, count: 500 },
		founderIdentity(realm)
	);
	return Object.fromEntries(
		(listed.extensions as { id: string; version: string }[]).map((e) => [e.id, e.version])
	);
}

const principalOf = (seed: number) =>
	Ed25519KeyIdentity.generate(new Uint8Array(32).fill(seed)).getPrincipal().toText();

// The department the founder sits in, for the department_docs row.
let library = '';

const ROWS: Row[] = [
	{
		id: 'public_dashboard',
		path: '/extensions/public_dashboard',
		action: 'open the realm home page',
		expected: "the realm's name and welcome message, and its two members",
		run: async ({ app }) => {
			const info = (await readOp(realm, 'realm_info')).realm as {
				name: string;
				welcome_message: string;
			};
			await expect(app.getByRole('heading', { name: info.name, level: 1 })).toBeVisible();
			await expect(app.getByText(info.welcome_message).first()).toBeVisible();
			// The metric itself: its text also matches the card of the latest members, whose principals may begin with a 2.
			const members = app.locator('.realm-status-metric').filter({ hasText: 'Members' });
			await expect(members.locator('.realm-status-metric-value')).toHaveText('2');
		}
	},
	{
		id: 'member_dashboard',
		path: '/extensions/member_dashboard',
		as: 'member',
		action: 'open my membership page as the second member',
		expected: 'my membership steps: the passport check and the invoices',
		run: async ({ app }) => {
			await expect(app.getByText('Membership', { exact: true }).first()).toBeVisible();
			await expect(app.getByRole('button', { name: 'Verify passport' })).toBeVisible();
			await expect(app.getByRole('button', { name: 'View invoices' })).toBeVisible();
		}
	},
	{
		id: 'realm_settings',
		path: '/extensions/realm_settings',
		action: 'change the welcome message and save',
		expected: 'the realm holds the new welcome message',
		run: async ({ app, frame }) => {
			const welcome = `Settings were saved by the e2e ${Date.now()}`;
			await app.locator('#rs-welcome').fill(welcome);
			await app.getByRole('button', { name: 'Save Settings' }).click();
			await frame.getByRole('button', { name: 'Continue' }).click();
			await expect
				.poll(
					async () =>
						((await readOp(realm, 'realm_info')).realm as { welcome_message: string })
							.welcome_message
				)
				.toBe(welcome);
		}
	},
	{
		id: 'voting',
		path: '/extensions/voting',
		action: 'open the proposals list',
		expected: 'the proposals voted by proposals.spec.ts, with their outcomes',
		run: async ({ app }) => {
			await expect(
				app.locator('[data-proposal-id]', { hasText: 'Executed' }).first()
			).toBeVisible();
			await expect(
				app.locator('[data-proposal-id]', { hasText: 'Accepted' }).first()
			).toBeVisible();
		}
	},
	{
		id: 'admin_dashboard',
		path: '/extensions/admin_dashboard',
		action: 'load the users in the data explorer',
		expected: 'both members listed',
		run: async ({ app }) => {
			await app.getByRole('button', { name: 'Load Data' }).click();
			await expect(app.getByText(realm.founder)).toBeVisible();
			await expect(app.getByText(memberIdentity().getPrincipal().toText())).toBeVisible();
		}
	},
	{
		id: 'vault',
		path: '/extensions/vault',
		action: 'send a payment from the treasury fund to the second member',
		expected: 'the member holds the tokens; the treasury lists the transfer',
		run: async ({ app, frame }) => {
			const member = memberIdentity().getPrincipal().toText();
			const before = await balanceOf(realm, { owner: member });
			await app.getByRole('button', { name: 'Send', exact: true }).first().click();
			await app.locator('#v-fund').selectOption('TREASURY');
			await app.locator('#v-to').fill(member);
			await app.locator('#v-amount').fill('0.001');
			await app.getByRole('button', { name: 'Send', exact: true }).last().click();
			await frame.getByRole('button', { name: 'Send', exact: true }).last().click();
			await expect
				.poll(() => balanceOf(realm, { owner: member }), { timeout: 60_000 })
				.toBe(before + 100_000n);
		}
	},
	{
		id: 'codex_viewer',
		path: '/extensions/codex_viewer',
		action: 'open the installed codex',
		expected: 'the codex the wizard installed, active',
		run: async ({ app }) => {
			await expect(app.getByText('syntropia').first()).toBeVisible();
			await expect(app.getByText('Active').first()).toBeVisible();
			const status = await readOp(realm, 'codex_status', { count: 1 }, founderIdentity(realm));
			expect(status.codex).toMatchObject({ status: 'active' });
			expect((status.codex as { id: string }).id).toMatch(/^syntropia@/);
		}
	},
	{
		id: 'import_export',
		path: '/extensions/import_export',
		action: 'export the users as JSON, then import a new one',
		expected: 'a download holding both members; the new user is a member afterwards',
		run: async ({ page, app, frame }) => {
			await app.getByRole('button', { name: 'Entity Export' }).click();
			await app.locator('select').selectOption('User');
			await app.getByRole('button', { name: 'Export User' }).click();
			await closeDialog(frame);
			await expect(app.getByText(/\b2\s*entities/)).toBeVisible();
			const download = page.waitForEvent('download', { timeout: 30_000 });
			await app.getByRole('button', { name: 'Download JSON' }).click();
			await closeDialog(frame);
			const exported = readFileSync((await (await download).path())!, 'utf8');
			expect(exported).toContain(realm.founder);
			expect(exported).toContain(memberIdentity().getPrincipal().toText());

			const newcomer = principalOf(9);
			await app.getByRole('button', { name: 'Entity Import' }).click();
			await app.getByRole('button', { name: 'JSON Editor' }).click();
			await app
				.locator('textarea')
				.fill(JSON.stringify([{ _type: 'User', id: newcomer, nickname: 'Imported' }]));
			await app.getByRole('button', { name: 'Confirm Import' }).click();
			await closeDialog(frame);
			await expect.poll(async () => (await rows('User')).map((u) => u.id)).toContain(newcomer);
		}
	},
	{
		id: 'mundus_explorer',
		path: '/extensions/mundus_explorer',
		action: 'open the realms network page',
		expected: "this realm's name and status, from realm_info",
		run: async ({ app }) => {
			await expect(app.getByRole('heading', { name: 'Realms Network' })).toBeVisible();
			await expect(app.getByText(realm.name).first()).toBeVisible();
			await app.getByRole('button', { name: 'Refresh' }).click();
			await expect(app.getByText(realm.name).first()).toBeVisible();
		}
	},
	{
		id: 'passport_verification',
		path: '/extensions/passport_verification',
		action: 'start the passport check (the realm skips the outside ZK proof for the e2e)',
		expected: 'the page says verified; the realm holds a passport identity for the founder',
		run: async ({ app }) => {
			await app.getByRole('button', { name: 'Start' }).click();
			await expect(app.getByText('Passport Verified Successfully!')).toBeVisible({
				timeout: 60_000
			});
			await expect
				.poll(async () => (await rows('Identity')).map((i) => i.kind))
				.toContain('passport');
		}
	},
	{
		id: 'task_monitor',
		path: '/extensions/task_monitor',
		action: 'refresh the background task list',
		expected: 'the task counters (running, pending, completed, failed) are shown',
		run: async ({ app }) => {
			await app.getByRole('button', { name: 'Refresh' }).click();
			await expect(app.getByText(/\d+ tasks/)).toBeVisible();
			await expect(app.getByText(/\d+ failed/)).toBeVisible();
		}
	},
	{
		id: 'access_manager',
		path: '/extensions/access_manager',
		action: 'create a department',
		expected: 'the realm has the new department',
		run: async ({ app }) => {
			const name = `Parks ${Date.now()}`;
			await app.getByRole('button', { name: /New Department/ }).click();
			await app.getByPlaceholder('Department name').fill(name);
			await app.getByRole('button', { name: 'Create', exact: true }).click();
			await expect
				.poll(async () => {
					const listed = await readOp(realm, 'department_list', {}, founderIdentity(realm));
					return (listed.departments as { id: string }[]).map((d) => d.id);
				})
				.toContain(name);
		}
	},
	{
		id: 'role_manager',
		path: '/extensions/role_manager',
		action: 'generate a registration invitation',
		expected: 'an invitation link on the page; the realm lists one more invite',
		run: async ({ app }) => {
			const before = (await rows('Invite')).length;
			await app.getByRole('button', { name: 'Invitations' }).click();
			await app.getByRole('button', { name: 'Generate Invitation' }).click();
			await expect(app.getByText('Invitation Link').first()).toBeVisible({ timeout: 60_000 });
			await expect.poll(async () => (await rows('Invite')).length).toBe(before + 1);
		}
	},
	{
		id: 'member_manager',
		path: '/extensions/member_manager',
		action: 'send a notification to the second member',
		expected: 'the second member holds the notification',
		run: async ({ app }) => {
			const title = `Welcome aboard ${Date.now()}`;
			await app
				.getByRole('button', { name: new RegExp(memberIdentity().getPrincipal().toText()) })
				.click();
			await app.getByRole('button', { name: 'Messages' }).click();
			await app.getByPlaceholder(/title/i).fill(title);
			await app.getByPlaceholder(/message/i).fill('Glad to have you in the realm.');
			await app.getByRole('button', { name: 'Send notification' }).click();
			await expect
				.poll(async () => {
					const inbox = await readOp(realm, 'notification_list', {}, memberIdentity());
					return (inbox.notifications as { title: string }[]).map((n) => n.title);
				})
				.toContain(title);
		}
	},
	{
		id: 'metrics',
		path: '/extensions/metrics',
		action: 'open the financial reports',
		expected: 'the report picker and the totals of an empty treasury',
		run: async ({ app }) => {
			await expect(app.locator('#report-select')).toBeVisible();
			await expect(app.getByText('Total Assets').first()).toBeVisible();
		}
	},
	{
		id: 'notifications',
		path: '/messages',
		as: 'member',
		action: "open the second member's messages (notifications sends its visitors there)",
		expected: 'the notification the founder sent',
		run: async ({ app }) => {
			const title = `Meeting on Friday ${Date.now()}`;
			await runOp(
				realm,
				'notification_send',
				{
					recipient: `User:${memberIdentity().getPrincipal().toText()}`,
					title,
					body: 'In the square.'
				},
				founderIdentity(realm)
			);
			await expect(app.getByText(title)).toBeVisible({ timeout: 60_000 });
		}
	},
	{
		id: 'managed_services',
		path: '/extensions/managed_services',
		action: 'accept a new Realms GOS release (an administrator does so directly)',
		expected: 'the page and the realm hold the release as accepted',
		run: async ({ app }) => {
			const version = `98.0.${Date.now()}`;
			await app.getByLabel('Release version').fill(version);
			await app.getByLabel('Module hash').fill(`sha256:${'cd'.repeat(32)}`);
			await app.getByRole('button', { name: 'Accept release' }).click();
			await expect(app.getByText(`Release ${version} accepted.`)).toBeVisible({ timeout: 60_000 });
			const monitor = await readOp(realm, 'realm_monitor', {}, founderIdentity(realm));
			expect(monitor.upgrade).toMatchObject({ version });
		}
	},
	{
		id: 'system_info',
		path: '/extensions/system_info',
		action: 'refresh the system information',
		expected: 'the realm, its codex and the count of installed extensions',
		run: async ({ app }) => {
			const listed = await readOp(
				realm,
				'extension_list',
				{ from: 1, count: 500 },
				founderIdentity(realm)
			);
			const count = (listed.extensions as unknown[]).length;
			await app.getByRole('button', { name: 'Refresh' }).click();
			await expect(app.getByText(`${realm.name} (alpha)`)).toBeVisible();
			await expect(app.getByText(/Codex\s+syntropia/)).toBeVisible();
			await expect(app.getByText(new RegExp(`\\b${count}\\s*installed`, 'i')).first()).toBeVisible();
		}
	},
	{
		id: 'land_registry',
		path: '/extensions/land_registry',
		action: 'register a parcel owned by the founder',
		expected: 'the page confirms the parcel; the realm holds it',
		run: async ({ app }) => {
			await app.getByRole('button', { name: 'Register parcel' }).click();
			await app.getByPlaceholder('Parcel name').fill('Town square');
			await app.getByPlaceholder('User ID').fill(realm.founder);
			await app.getByRole('button', { name: 'Register parcel' }).click();
			await expect(app.getByText(/Parcel \S+ registered\./)).toBeVisible();
			const parcels = await rows('Land');
			expect(parcels).toContainEqual(
				expect.objectContaining({
					owner: `User:${realm.founder}`,
					metadata: { name: 'Town square' }
				})
			);
		}
	},
	{
		id: 'justice_litigation',
		path: '/extensions/justice_litigation',
		action: 'file a dispute against the second member',
		expected: 'the page confirms the case; the realm holds the dispute',
		run: async ({ app }) => {
			await app.getByRole('button', { name: 'Create litigation' }).click();
			await app.locator('#jl-defendant').fill(memberIdentity().getPrincipal().toText());
			await app.locator('#jl-title').fill('The fence');
			await app.locator('#jl-desc').fill('The fence of the square stands on the wrong side.');
			await app.getByRole('button', { name: 'Create Litigation' }).last().click();
			await expect(app.getByText(/Litigation case \S+ created!/)).toBeVisible();
			expect((await rows('Dispute')).map((d) => d.title)).toContain('The fence');
		}
	},
	{
		id: 'procurement',
		path: '/extensions/procurement',
		action: 'draft a request for proposal for a department',
		expected: 'the request is listed as a draft; the realm holds the tender',
		run: async ({ app }) => {
			await app.getByRole('button', { name: 'New request for proposal' }).click();
			await app.getByLabel('Title').fill('Benches for the square');
			await app.getByLabel('Description').fill('Twelve wooden benches.');
			await app.getByLabel('Requesting department').selectOption({ index: 1 });
			await app.getByRole('button', { name: 'Create draft' }).click();
			await expect(app.getByText('Benches for the square').first()).toBeVisible();
			expect((await rows('Tender')).map((t) => t.title)).toContain('Benches for the square');
		}
	},
	{
		id: 'department_docs',
		path: '/extensions/department_docs',
		// Documents belong to a department: the founder takes a seat in a new one.
		setup: async () => {
			const founder = founderIdentity(realm);
			library = `Library ${Date.now()}`;
			const department = library;
			await runOp(realm, 'department_create', { department }, founder);
			await runOp(
				realm,
				'position_create',
				{ department, position: `librarian ${department}` },
				founder
			);
			await runOp(
				realm,
				'appoint',
				{ position: `librarian ${department}`, user: realm.founder },
				founder
			);
		},
		action: 'write a document for the department the founder sits in',
		expected: 'the document is listed in the department',
		run: async ({ app }) => {
			const title = `Opening hours ${Date.now()}`;
			await app.getByRole('button', { name: 'New document' }).click();
			await app.getByPlaceholder('Title').fill(title);
			await app.getByPlaceholder(/Document body/).fill('The library opens at nine.');
			await app.getByRole('button', { name: 'Encrypt & share' }).click();
			await expect(app.getByText(title).first()).toBeVisible();
			await expect
				.poll(async () => {
					const listed = await readOp(
						realm,
						'document_list',
						{ department: library },
						founderIdentity(realm)
					);
					return (listed.documents as { title: string }[]).map((d) => d.title);
				})
				.toContain(title);
		}
	},
	{
		id: 'package_manager',
		path: '/extensions/package_manager',
		action: 'install an extension from the registry, then uninstall it',
		expected: 'the realm lists the extension after the install and no longer after the uninstall',
		run: async ({ page, app, frame }) => {
			const id = 'hello_sandboxed';
			const installed = async () => Object.keys(await installedExtensions());
			await app.getByRole('tab', { name: /Available/ }).click();
			await app.getByPlaceholder(/Search by name or ID/).fill(id);
			await app
				.getByRole('row', { name: new RegExp(id) })
				.getByRole('button', { name: 'Install' })
				.click();
			await expect.poll(installed, { timeout: 60_000 }).toContain(id);
			await closeDialog(frame);

			await app.getByRole('tab', { name: /Installed/ }).click();
			// The page asks to type the package id in a browser prompt.
			page.once('dialog', (dialog) => dialog.accept(id));
			await app
				.getByRole('row', { name: new RegExp(id) })
				.getByRole('button', { name: 'More actions' })
				.click();
			await app.getByRole('button', { name: /Uninstall/ }).click();
			await expect.poll(installed, { timeout: 60_000 }).not.toContain(id);
		}
	}
];

// Choosing a zone is a click on a map of OpenFreeMap tiles, which a headless run cannot place.
test.fixme('zone_selector: choose my zone on the map', async () => {});

for (const row of ROWS) {
	test(`${row.id}: ${row.action}`, async ({ page }) => {
		test.setTimeout(3 * 60 * 1000);
		await row.setup?.();
		const who = row.as === 'member' ? memberIdentity() : founderIdentity(realm);
		const { app, frame, close } = await openExtension(page, realm, who, row.id, row.path);
		try {
			await row.run({ page, app, frame });
			await snap(page, row.id);
		} finally {
			await close();
		}
	});
}
