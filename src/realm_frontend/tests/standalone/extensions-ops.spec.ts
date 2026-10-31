// The main operation of each installed extension, one test per row of the table below, on the realm
// wizard.spec.ts launched (with the founder and a second member in it):
//   scripts/e2e_ui.sh
import { expect, test, type Page } from '@playwright/test';
import { openExtension, type ExtensionPage } from './fixtures/extension';
import {
	founderIdentity,
	loadRealm,
	memberIdentity,
	readOp,
	runOp,
	type StandaloneRealm
} from './fixtures/realm';
import { snap } from './fixtures/snap';

let realm: StandaloneRealm;

test.beforeAll(() => {
	realm = loadRealm();
});

interface Tools {
	page: Page;
	app: ExtensionPage;
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
	run: (tools: Tools) => Promise<void>;
}

const ROWS: Row[] = [
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
	}
];

for (const row of ROWS) {
	test(`${row.id}: ${row.action}`, async ({ page }) => {
		const who = row.as === 'member' ? memberIdentity() : founderIdentity(realm);
		const { app, close } = await openExtension(page, realm, who, row.id);
		try {
			await row.run({ page, app });
			await snap(page, `${row.id}-${row.expected.slice(0, 20).replace(/\W+/g, '-')}`);
		} finally {
			await close();
		}
	});
}
