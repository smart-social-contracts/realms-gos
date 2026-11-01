// The life of a parcel in the land_registry extension, with the realm's real members, on the realm
// wizard.spec.ts launched (the founder and a second member in it) and proposals.spec.ts made the founder an
// administrator of:
//   scripts/e2e_ui.sh
//
// The people: the administrator registers a parcel for the second member (the owner); a neighbour claims it, the
// court rules for the neighbour and the administrator moves the parcel under the verdict. A stranger never joined.
// The page offers to register a parcel, to move it under a court order, to mark it disputed and to end the dispute.
// No page files a dispute about a parcel, accepts it, assigns a judge or issues a verdict for it, so those steps are
// the realm's operations called as the member who would call them. What a step did is read back from the realm, not
// from the page's text; a refusal shows its message and leaves the realm's parcels as they were.
//
// The tests share one parcel after the other and run in this order.
import { expect, test, type Page } from '@playwright/test';
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
import { snap } from './fixtures/snap';

let realm: StandaloneRealm;

test.beforeAll(() => {
	realm = loadRealm();
});

// Titles carry the run, so the runs of a realm's rows can be told apart.
const RUN = Date.now().toString(36);

// New people each run, so the spec can run again on the realm it ran on.
const founder = () => founderIdentity(realm);
const owner = memberIdentity();
const neighbour = Ed25519KeyIdentity.generate();
const judge = Ed25519KeyIdentity.generate();
const stranger = Ed25519KeyIdentity.generate();
const principal = (who: Ed25519KeyIdentity) => who.getPrincipal().toText();

interface Land {
	id: string;
	x: number;
	y: number;
	width: number;
	height: number;
	kind: string;
	owner: string;
	status: string;
	token_id: string;
	registered_by: string;
	metadata: { name?: string };
}
interface Dispute {
	id: string;
	subject: string;
	status: string;
	case: string;
}
interface Case {
	id: string;
	court: string;
	status: string;
}
interface Verdict {
	id: string;
	case: string;
	status: string;
}
interface Judge {
	id: string;
	user: string;
	court: string;
	status: string;
}
interface Court {
	id: string;
	name: string;
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

/** The ids of the parcels the realm lists for `who`. */
async function parcelsOf(who: Ed25519KeyIdentity): Promise<string[]> {
	const out = await readOp(realm, 'land_list', { owner: principal(who), count: 500 }, founder());
	return (out.lands as Land[]).map((l) => l.id);
}

type Reply = Record<string, unknown>;

/** Calls `op` as `who` and expects the realm to have done it. */
async function done(op: string, args: Record<string, unknown>, who: Ed25519KeyIdentity) {
	const reply: Reply = await runOp(realm, op, args, who);
	expect(reply.ok, `${op}: ${reply.error}`).toBe(true);
	return reply;
}

/** Runs `act` and expects the realm's rows of `entities` to be what they were: a refusal changes nothing. */
async function leavesUnchanged(entities: string[], act: () => Promise<void>) {
	const snapshot = () => Promise.all(entities.map((entity) => rows(entity)));
	const before = await snapshot();
	await act();
	expect(await snapshot(), `${entities.join(', ')} are unchanged`).toEqual(before);
}

const landOf = (page: Page, who: Ed25519KeyIdentity) =>
	openExtension(page, realm, who, 'land_registry');

// The page waits for the realm's answer before it shows the result.
const REPLY = { timeout: 120_000 };

interface Parcel {
	name: string;
	kind: string;
	x: number;
	y: number;
	width: number;
	height: number;
	owner: string;
}

/** Fills the register form and presses its Register parcel button. */
async function register(app: ExtensionPage, parcel: Parcel) {
	await app.getByRole('button', { name: 'Register parcel', exact: true }).click();
	await app.getByPlaceholder('Parcel name').fill(parcel.name);
	await app.getByLabel('Type').selectOption(parcel.kind);
	await app.getByLabel('X', { exact: true }).fill(String(parcel.x));
	await app.getByLabel('Y', { exact: true }).fill(String(parcel.y));
	await app.getByLabel('Width').fill(String(parcel.width));
	await app.getByLabel('Height').fill(String(parcel.height));
	await app.getByPlaceholder('User ID').fill(parcel.owner);
	await app.getByRole('button', { name: 'Register parcel', exact: true }).click();
}

/** Opens the parcel `id` from the list. */
async function openParcel(app: ExtensionPage, id: string) {
	await app.getByRole('button', { name: new RegExp(`^${id}\\b`) }).click();
	await expect(app.getByRole('heading', { name: id, exact: true })).toBeVisible();
}

/** Opens the parcel, names the new owner and the court order in its Ownership form and presses Update ownership. */
async function moveParcel(app: ExtensionPage, id: string, to: string, order: string) {
	await openParcel(app, id);
	await app.getByPlaceholder('User ID').fill(to);
	await app.getByPlaceholder('Court order (Verdict ID)').fill(order);
	await app.getByRole('button', { name: 'Update ownership', exact: true }).click();
}

// What the journey goes through, one test after the other.
let communityCourt: string;
let judgeSeat: string;
let parcel: string;
let bystander: string;
let dispute: string;
let caseId: string;
let verdict: string;
const square: Parcel = {
	name: `Town square ${RUN}`,
	kind: 'residential',
	x: 300,
	y: 300,
	width: 3,
	height: 2,
	owner: principal(owner)
};

test.describe.configure({ mode: 'serial' });

test('the neighbour and a judge of the community court join the realm', async () => {
	await done('realm_join', {}, neighbour);
	await done('realm_join', {}, judge);
	await done('role_assign', { user: principal(judge), profile: 'justice.judge' }, founder());
	communityCourt = (await rows<Court>('Court')).find((c) => c.name === 'Community Court')!.id;
	expect(communityCourt, 'the codex seeded the community court').toBeTruthy();
	await done('judge_appoint', { user: principal(judge), court: communityCourt }, founder());

	const seat = (await rows<Judge>('Judge')).find(
		(j) => j.user === principal(judge) && j.court === communityCourt && j.status === 'active'
	);
	expect(seat, 'the judge holds an active seat').toBeDefined();
	judgeSeat = seat!.id;
});

test('the administrator registers a parcel for the second member', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await register(app, square);
		const registered = app.getByText(/Parcel \S+ registered\./);
		await expect(registered).toBeVisible(REPLY);
		parcel = /Parcel (\S+) registered/.exec(await registered.innerText())![1];
		await snap(page, 'registered');
	} finally {
		await close();
	}
	expect(await row<Land>('Land', parcel)).toMatchObject({
		x: square.x,
		y: square.y,
		width: square.width,
		height: square.height,
		kind: square.kind,
		owner: `User:${principal(owner)}`,
		status: 'active',
		token_id: '',
		registered_by: principal(founder()),
		metadata: { name: square.name }
	});
	expect(await parcelsOf(owner)).toContain(parcel);
	expect(await parcelsOf(neighbour)).toEqual([]);
});

test('a parcel on top of another is refused', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await register(app, { ...square, name: 'The overlap', x: square.x + 1, y: square.y + 1 });
			await expect(app.getByText(`the parcel overlaps ${parcel}`)).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('a parcel for someone who is not a member is refused', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await register(app, { ...square, name: 'The stranger', x: 400, owner: principal(stranger) });
			await expect(app.getByText(/unknown User/)).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('a member cannot register a parcel', async ({ page }) => {
	const { app, close } = await landOf(page, owner);
	try {
		await expect(app.getByText(parcel).first()).toBeVisible();
		await leavesUnchanged(['Land'], async () => {
			await register(app, { ...square, name: 'My own', x: 500, owner: principal(owner) });
			await expect(app.getByText('land_register needs nft.mint')).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('someone who is not a member is turned away from the page', async ({ page }) => {
	const { app, close } = await landOf(page, stranger);
	try {
		await expect(app.getByText('Additional permissions needed')).toBeVisible();
		await expect(app.getByText('realm.data_view')).toBeVisible();
		await expect(app.getByRole('button', { name: 'Register parcel', exact: true })).toHaveCount(0);
		await snap(page, 'turned away');
	} finally {
		await close();
	}
});

test('the owner cannot move or mark disputed their own parcel', async ({ page }) => {
	const { app, close } = await landOf(page, owner);
	try {
		await leavesUnchanged(['Land'], async () => {
			await moveParcel(app, parcel, principal(neighbour), 'Verdict:VER-1');
			await expect(app.getByText('land_transfer: you are a party to this matter')).toBeVisible(
				REPLY
			);
			await app.getByRole('button', { name: 'Mark disputed', exact: true }).click();
			await expect(app.getByText('land_freeze: you are a party to this matter')).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('a member without the permission cannot move a parcel', async () => {
	const order = { land: parcel, to: principal(neighbour), order: 'Verdict:VER-1' };
	await leavesUnchanged(['Land'], async () => {
		for (const who of [judge, stranger]) {
			const reply: Reply = await runOp(realm, 'land_transfer', order, who);
			expect(reply.ok).toBe(false);
			expect(reply.error).toBe('land_transfer needs nft.force_transfer');
		}
	});
});

test('a parcel is not moved without a court order', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await moveParcel(app, parcel, principal(neighbour), 'VER-0');
			await expect(app.getByText("unknown Verdict 'VER-0'")).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

// realms-extensions#42: no page sends a subject when it files a dispute (justice_litigation's dispute_file call has
// none), so the court order the land page asks for cannot be had from any screen: the next steps call the operations.
test.fixme('a member files a dispute about the parcel on the justice page', async () => {});

test('the neighbour files a dispute about the parcel', async () => {
	dispute = (
		await done(
			'dispute_file',
			{ respondent: principal(owner), subject: `Land:${parcel}`, title: `The boundary ${RUN}` },
			neighbour
		)
	).dispute as string;
	expect(await row<Dispute>('Dispute', dispute)).toMatchObject({
		subject: `Land:${parcel}`,
		status: 'filed'
	});
});

test('the administrator accepts the dispute and assigns the judge', async () => {
	caseId = (await done('dispute_accept', { dispute, court: communityCourt }, founder()))
		.case as string;
	await done('case_assign', { case: caseId, judge: judgeSeat }, founder());
	expect(await row<Case>('Case', caseId)).toMatchObject({
		court: communityCourt,
		status: 'assigned'
	});
});

test('a draft verdict does not move the parcel', async ({ page }) => {
	verdict = (await done('verdict_draft', { case: caseId, decision: 'for the plaintiff' }, judge))
		.verdict as string;
	expect(await row<Verdict>('Verdict', verdict)).toMatchObject({ case: caseId, status: 'draft' });

	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await moveParcel(app, parcel, principal(neighbour), `Verdict:${verdict}`);
			await expect(app.getByText('moving a parcel needs an issued court verdict')).toBeVisible(
				REPLY
			);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('the judge issues the verdict', async () => {
	await done('verdict_issue', { verdict }, judge);
	expect(await row<Verdict>('Verdict', verdict)).toMatchObject({ status: 'issued' });
});

test('the verdict moves neither another parcel nor the parcel to a stranger', async ({ page }) => {
	bystander = (
		await done(
			'land_register',
			{ owner: principal(owner), x: square.x + 10, y: square.y, kind: 'agricultural' },
			founder()
		)
	).land as string;

	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await moveParcel(app, bystander, principal(neighbour), `Verdict:${verdict}`);
			await expect(
				app.getByText(
					new RegExp(`${verdict} does not settle ${bystander} with User:\\S+ as a party`)
				)
			).toBeVisible(REPLY);

			await moveParcel(app, parcel, principal(judge), `Verdict:${verdict}`);
			await expect(
				app.getByText(
					new RegExp(
						`${verdict} does not settle ${parcel} with User:${principal(judge)} as a party`
					)
				)
			).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('the administrator marks the parcel disputed', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await openParcel(app, parcel);
		await app.getByPlaceholder('Reason (optional)').fill('The boundary is in dispute.');
		await app.getByRole('button', { name: 'Mark disputed', exact: true }).click();
		await expect(app.getByText('Parcel marked as disputed.')).toBeVisible(REPLY);
		await expect(app.getByText('Only active parcels can be minted.')).toBeVisible();
		await snap(page, 'disputed');
	} finally {
		await close();
	}
	expect(await row<Land>('Land', parcel)).toMatchObject({ status: 'disputed', token_id: '' });
});

test('the owner cannot end the dispute', async ({ page }) => {
	const { app, close } = await landOf(page, owner);
	try {
		await leavesUnchanged(['Land'], async () => {
			await openParcel(app, parcel);
			await app.getByRole('button', { name: 'End dispute', exact: true }).click();
			await expect(app.getByText('land_unfreeze: you are a party to this matter')).toBeVisible(
				REPLY
			);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('the administrator moves the disputed parcel to the neighbour under the verdict', async ({
	page
}) => {
	const { app, close } = await landOf(page, founder());
	try {
		await moveParcel(app, parcel, principal(neighbour), `Verdict:${verdict}`);
		await expect(app.getByText('Ownership updated!')).toBeVisible(REPLY);
		await snap(page, 'moved');
	} finally {
		await close();
	}
	expect(await row<Land>('Land', parcel)).toMatchObject({
		owner: `User:${principal(neighbour)}`,
		status: 'disputed'
	});
	expect(await parcelsOf(neighbour)).toEqual([parcel]);
	expect(await parcelsOf(owner)).not.toContain(parcel);
	expect(await parcelsOf(owner)).toContain(bystander);
});

test('the new owner sees the parcel as theirs', async ({ page }) => {
	const { app, close } = await landOf(page, neighbour);
	try {
		const short = `${principal(neighbour).slice(0, 5)}…${principal(neighbour).slice(-3)}`;
		await expect(
			app.getByRole('button', { name: new RegExp(`^${parcel}\\b.*${short}`) })
		).toBeVisible();
		await snap(page, 'the neighbour');
	} finally {
		await close();
	}
});

test('neither the old nor the new owner can move the parcel again', async ({ page }) => {
	const { app, close } = await landOf(page, owner);
	try {
		await leavesUnchanged(['Land'], async () => {
			await moveParcel(app, parcel, principal(owner), `Verdict:${verdict}`);
			await expect(app.getByText('land_transfer: you are a party to this matter')).toBeVisible(
				REPLY
			);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
	await leavesUnchanged(['Land'], async () => {
		const reply: Reply = await runOp(
			realm,
			'land_transfer',
			{ land: parcel, to: principal(owner), order: `Verdict:${verdict}` },
			neighbour
		);
		expect(reply.error).toBe('land_transfer: you are a party to this matter');
	});
});

test('a parcel is not moved to the member who already owns it', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await moveParcel(app, parcel, principal(neighbour), `Verdict:${verdict}`);
			await expect(
				app.getByText(`${parcel} cannot be moved to User:${principal(neighbour)}`)
			).toBeVisible(REPLY);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
});

test('the administrator ends the dispute', async ({ page }) => {
	const { app, close } = await landOf(page, founder());
	try {
		await openParcel(app, parcel);
		await app.getByRole('button', { name: 'End dispute', exact: true }).click();
		await expect(app.getByText('Dispute ended; the parcel is active again.')).toBeVisible(REPLY);
		await snap(page, 'ended');
	} finally {
		await close();
	}
	expect(await row<Land>('Land', parcel)).toMatchObject({
		owner: `User:${principal(neighbour)}`,
		status: 'active'
	});
});

test('a parcel is not minted as an NFT where the realm has no LAND collection', async ({
	page
}) => {
	const { app, close } = await landOf(page, founder());
	try {
		await leavesUnchanged(['Land'], async () => {
			await openParcel(app, parcel);
			await app.getByRole('button', { name: 'Mint NFT', exact: true }).click();
			await expect(app.getByText("unknown NFTToken 'LAND'")).toBeVisible(REPLY);
			await expect(app.getByText(/NFT minted!/)).toHaveCount(0);
			await snap(page, 'refused');
		});
	} finally {
		await close();
	}
	expect((await row<Land>('Land', parcel)).token_id).toBe('');
});

// realms-extensions#43: the page says "unknown NFTToken 'LAND'", the realm's raw lookup error, and offers Mint NFT on
// every active parcel of a realm that has no LAND collection.
test.fixme('the page tells the administrator the realm has no LAND collection', async ({
	page
}) => {
	const { app, close } = await landOf(page, founder());
	try {
		await openParcel(app, parcel);
		await expect(app.getByRole('button', { name: 'Mint NFT', exact: true })).toHaveCount(0);
	} finally {
		await close();
	}
});

// realms-extensions#44: the name typed in Register parcel is stored with the parcel but the page never shows it.
test.fixme('the page shows the name of a registered parcel', async () => {});

// A local realm has no ICRC-7 ledger for its LAND collection (nft_token_register names one), so the mint cannot be
// run here.
test.fixme('the administrator mints the parcel as an NFT for its owner', async () => {});
