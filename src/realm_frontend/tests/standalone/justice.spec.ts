// The life of a case in the justice_litigation extension, with the realm's real members, on the realm
// wizard.spec.ts launched (the founder and a second member in it) and proposals.spec.ts made the founder an
// administrator of:
//   scripts/e2e_ui.sh
//
// The people: the second member files against the defendant; the founder, an administrator, accepts the
// dispute and assigns the judge; the judge rules. An outsider never joined. The extension's page offers
// to file, accept and reject a dispute and to issue a verdict; assigning a judge, appeals and penalties have
// no page in this version, so those steps are the realm's operations called as the member who would call them.
// What a step did is read back from the realm, not from the page's text.
//
// The tests share one case after the other and run in this order. A verdict can only be made final, and a penalty
// carried out, after a 14-day appeal window the realm fixes; the spec stops where that window begins.
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

const person = (seed: number) => Ed25519KeyIdentity.generate(new Uint8Array(32).fill(seed));
const founder = () => founderIdentity(realm);
const claimant = memberIdentity();
// The defendant is also a court clerk, who holds dispute.assign: when case_assign refuses them, it is the rule
// against a party acting in their own case. (The realm words a party's refusal the same whether or not they hold the
// permission, so the other refusals below do not tell the two apart.)
const defendant = person(41);
const judge = person(42);
const outsider = person(43);
const principal = (who: Ed25519KeyIdentity) => who.getPrincipal().toText();

interface Dispute {
	id: string;
	claimant: string;
	respondent: string;
	title: string;
	status: string;
	case: string;
}
interface Case {
	id: string;
	court: string;
	plaintiff: string;
	defendant: string;
	status: string;
}
interface Verdict {
	id: string;
	case: string;
	decision: string;
	status: string;
	issued_by: string;
}
interface Penalty {
	id: string;
	verdict: string;
	kind: string;
	target: string;
	amount: number;
	symbol: string;
	status: string;
	invoice: string;
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
interface Appeal {
	id: string;
	case: string;
	verdict: string;
	court: string;
	status: string;
	new_verdict: string;
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

/** Passes a realm-wide proposal of `op`: the founder files it and every member votes yes. */
async function passByVote(title: string, op: string, args: Record<string, unknown>) {
	const filed = await done(
		'proposal_create',
		{ title: `${title} ${RUN}`, action: { op, args } },
		founder()
	);
	const id = filed.proposal as string;
	for (const voter of [founder(), claimant, judge, defendant]) {
		const vote = await done('proposal_vote', { proposal: id, choice: 'yes' }, voter);
		if (vote.status) break;
	}
	const { proposal } = await readOp(realm, 'proposal_get', { proposal: id }, founder());
	expect((proposal as { status: string }).status, `${title} ran`).toBe('executed');
}

const justiceOf = (page: Page, who: Ed25519KeyIdentity) =>
	openExtension(page, realm, who, 'justice_litigation');

/** Opens the dispute titled `title` from the list. */
async function openDispute(app: ExtensionPage, title: string) {
	await app.getByText(title).first().click();
	await expect(app.getByText('Case', { exact: true })).toBeVisible();
}

/** Writes `decision` in the verdict dialog and presses its Execute Verdict button. */
async function executeVerdict(app: ExtensionPage, decision: string) {
	const execute = app.getByRole('button', { name: 'Execute Verdict', exact: true });
	await execute.first().click();
	await app.locator('#jl-verdict-decision').fill(decision);
	await app.locator('#jl-verdict-reasoning').fill('Ruled by the justice e2e.');
	await execute.last().click();
}

// What the journey goes through, one test after the other.
let communityCourt: string;
let supremeCourt: string;
let judgeSeat: string;
let supremeSeat: string;
const title = `The fence ${RUN}`;
let dispute: string;
let caseId: string;
const penaltyTitle = `The bench ${RUN}`;
let penaltyCase: string;
let penaltyVerdict: string;

test.describe.configure({ mode: 'serial' });

test('the realm seats a judge at each court', async ({ page }) => {
	await done('realm_join', {}, defendant);
	await done('realm_join', {}, judge);
	const courts = await rows<Court>('Court');
	const idOf = (name: string) => courts.find((c) => c.name === name)?.id;
	communityCourt = idOf('Community Court')!;
	supremeCourt = idOf('Constitutional Court')!;
	expect(communityCourt && supremeCourt, 'the codex seeded the realm courts').toBeTruthy();

	await passByVote('Make a judge a judge', 'role_assign', {
		user: principal(judge),
		profile: 'justice.judge'
	});
	await passByVote('Make the defendant a court clerk', 'role_assign', {
		user: principal(defendant),
		profile: 'justice.court_clerk'
	});
	await passByVote('Seat the judge at the community court', 'judge_appoint', {
		user: principal(judge),
		court: communityCourt
	});
	await passByVote('Seat the founder at the constitutional court', 'judge_appoint', {
		user: principal(founder()),
		court: supremeCourt
	});

	const judges = await rows<Judge>('Judge');
	const seat = (who: Ed25519KeyIdentity, court: string) =>
		judges.find((j) => j.user === principal(who) && j.court === court && j.status === 'active')?.id;
	judgeSeat = seat(judge, communityCourt)!;
	supremeSeat = seat(founder(), supremeCourt)!;
	expect(judgeSeat && supremeSeat, 'both judges hold an active seat').toBeTruthy();

	const { app, close } = await justiceOf(page, judge);
	try {
		await expect(app.getByRole('button', { name: 'All litigations', exact: true })).toBeVisible();
		await snap(page, 'the judge opens the justice page');
	} finally {
		await close();
	}
});

test('a member files a case against another member', async ({ page }) => {
	const { app, close } = await justiceOf(page, claimant);
	try {
		await app.getByRole('button', { name: 'Create litigation', exact: true }).click();
		await app.locator('#jl-defendant').fill(principal(defendant));
		await app.locator('#jl-title').fill(title);
		await app.locator('#jl-desc').fill('The fence of the square stands on the wrong side.');
		await app.getByRole('button', { name: 'Create Litigation', exact: true }).click();
		const created = app.getByText(/Litigation case \S+ created!/);
		await expect(created).toBeVisible({ timeout: 120_000 });
		dispute = /case (\S+) created!/.exec(await created.innerText())![1];
		await snap(page, 'filed');

		await expect(app.getByText(title).first()).toBeVisible();
	} finally {
		await close();
	}
	expect(await row<Dispute>('Dispute', dispute)).toMatchObject({
		claimant: principal(claimant),
		respondent: `User:${principal(defendant)}`,
		title,
		status: 'filed',
		case: ''
	});
});

test('a non-member cannot file a case', async () => {
	const before = (await rows<Dispute>('Dispute')).length;
	await refused(
		'dispute_file',
		{ respondent: principal(defendant), title: 'The gate' },
		outsider,
		/needs dispute\.create/
	);
	expect(await rows<Dispute>('Dispute')).toHaveLength(before);
});

// realms-extensions#38: the page says "Private filing is not available right now: the realm's key service did not
// respond" when the realm refused because the visitor is not a member. Nothing is filed.
test.fixme('a non-member who tries to file on the page is told why', async ({ page }) => {
	const { app, close } = await justiceOf(page, outsider);
	try {
		await app.getByRole('button', { name: 'Create litigation', exact: true }).click();
		await app.locator('#jl-defendant').fill(principal(defendant));
		await app.locator('#jl-title').fill('The gate');
		await app.locator('#jl-desc').fill('The gate of the square is locked.');
		await app.getByRole('button', { name: 'Create Litigation', exact: true }).click();
		await expect(app.getByText(/not a member|needs dispute\.create|join/i)).toBeVisible();
	} finally {
		await close();
	}
});

test('the parties and a judge cannot accept the case into a court', async ({ page }) => {
	await leavesUnchanged(['Dispute', 'Case'], async () => {
		const accept = { dispute, court: communityCourt };
		await refused('dispute_accept', accept, claimant, /you are a party/);
		await refused('dispute_accept', accept, defendant, /you are a party/);
		await refused('dispute_accept', accept, judge, /needs dispute\.accept/);
	});

	const { app, close } = await justiceOf(page, claimant);
	try {
		await openDispute(app, title);
		await expect(
			app.getByRole('button', { name: 'Accept', exact: true }),
			'the claimant is not offered to accept their own dispute'
		).toHaveCount(0);
		await snap(page, 'the claimant has no accept button');
	} finally {
		await close();
	}
	expect((await row<Dispute>('Dispute', dispute)).status).toBe('filed');
});

test('the administrator accepts the case into the community court', async ({ page }) => {
	const { app, close } = await justiceOf(page, founder());
	try {
		await openDispute(app, title);
		await app.getByLabel('Court ID').fill(communityCourt);
		await app.getByRole('button', { name: 'Accept', exact: true }).click();
		await expect(app.getByRole('button', { name: 'Accept', exact: true })).toHaveCount(0);
		await snap(page, 'accepted');
	} finally {
		await close();
	}
	const accepted = await row<Dispute>('Dispute', dispute);
	expect(accepted.status).toBe('accepted');
	caseId = accepted.case;
	expect(await row<Case>('Case', caseId)).toMatchObject({
		court: communityCourt,
		plaintiff: `User:${principal(claimant)}`,
		defendant: `User:${principal(defendant)}`,
		status: 'filed'
	});
});

test('a verdict before a judge is assigned is refused', async ({ page }) => {
	const { app, close } = await justiceOf(page, founder());
	try {
		await openDispute(app, title);
		await executeVerdict(app, 'for the plaintiff');
		await expect(app.getByText(new RegExp(`${caseId} is filed`))).toBeVisible();
		await expect(app.getByText('Verdict issued!')).toHaveCount(0);
		await snap(page, 'refused');
	} finally {
		await close();
	}
	expect((await rows<Verdict>('Verdict')).filter((v) => v.case === caseId)).toHaveLength(0);
	expect((await row<Case>('Case', caseId)).status).toBe('filed');
});

test('the defendant cannot assign a judge to their own case', async () => {
	await leavesUnchanged(['Case'], async () => {
		await refused('case_assign', { case: caseId, judge: judgeSeat }, defendant, /you are a party/);
		await refused('case_assign', { case: caseId, judge: judgeSeat }, claimant, /you are a party/);
		// A judge of another court cannot be given the case either.
		await refused(
			'case_assign',
			{ case: caseId, judge: supremeSeat },
			founder(),
			/does not sit at/
		);
	});
	expect((await row<Case>('Case', caseId)).status).toBe('filed');
});

test('the administrator assigns the judge of the court to the case', async () => {
	await done('case_assign', { case: caseId, judge: judgeSeat }, founder());
	expect((await row<Case>('Case', caseId)).status).toBe('assigned');
});

test('someone who is not the case judge cannot issue a verdict', async ({ page }) => {
	// The administrator may draft a verdict but no seat of the court has been given to them.
	const { app, close } = await justiceOf(page, founder());
	try {
		await openDispute(app, title);
		await executeVerdict(app, 'for the defendant');
		await expect(app.getByText(/not a judge assigned to/)).toBeVisible();
		await expect(app.getByText('Verdict issued!')).toHaveCount(0);
		await snap(page, 'refused');
	} finally {
		await close();
	}
	const verdicts = (await rows<Verdict>('Verdict')).filter((v) => v.case === caseId);
	expect(verdicts.filter((v) => v.status === 'issued')).toHaveLength(0);

	// The parties are not offered a verdict and the realm would refuse them.
	await leavesUnchanged(['Verdict', 'Case'], async () => {
		const draft = { case: caseId, decision: 'x' };
		await refused('verdict_draft', draft, claimant, /you are a party/);
		await refused('verdict_draft', draft, defendant, /you are a party/);
	});
});

// realms-extensions#37: Execute Verdict drafts the verdict, then issues it. When the issue is refused the draft stays
// and the case moves to in progress.
test.fixme('a refused verdict leaves nothing behind', async () => {
	const verdicts = (await rows<Verdict>('Verdict')).filter((v) => v.case === caseId);
	expect(verdicts).toHaveLength(0);
	expect((await row<Case>('Case', caseId)).status).toBe('assigned');
});

test('the judge issues the verdict', async ({ page }) => {
	const { app, close } = await justiceOf(page, judge);
	try {
		await openDispute(app, title);
		await executeVerdict(app, 'for the plaintiff');
		await expect(app.getByText('Verdict issued!')).toBeVisible({ timeout: 120_000 });
		await snap(page, 'issued');
	} finally {
		await close();
	}
	const issued = (await rows<Verdict>('Verdict')).filter(
		(v) => v.case === caseId && v.status === 'issued'
	);
	expect(issued).toHaveLength(1);
	expect(issued[0]).toMatchObject({ decision: 'for the plaintiff', issued_by: judgeSeat });
	expect((await row<Case>('Case', caseId)).status).toBe('verdict_issued');
});

// realms-extensions#36: the page reads verdicts with resolution_query, which needs resolution.query. Neither the
// judge's seat nor a member holds it, so the judge who just ruled sees "No verdict yet." (and so do the parties).
test.fixme('the case page shows its verdict to the judge', async ({ page }) => {
	const { app, close } = await justiceOf(page, judge);
	try {
		await openDispute(app, title);
		await expect(app.getByText('For the plaintiff')).toBeVisible();
	} finally {
		await close();
	}
});

test('a second verdict on a decided case is refused', async ({ page }) => {
	const before = (await rows<Verdict>('Verdict')).filter((v) => v.case === caseId);
	const { app, close } = await justiceOf(page, judge);
	try {
		await openDispute(app, title);
		await executeVerdict(app, 'for the defendant');
		await expect(app.getByText(new RegExp(`${caseId} is verdict_issued`))).toBeVisible();
		await snap(page, 'refused');
	} finally {
		await close();
	}
	expect((await rows<Verdict>('Verdict')).filter((v) => v.case === caseId)).toHaveLength(
		before.length
	);
});

// A fine is only carried out once its verdict is final, and a verdict is final 14 days after it was issued. The
// penalty therefore waits: it is recorded, billed to no one and refused if asked for early. The second case runs
// through the realm's operations because the verdict dialog has no penalty fields.
test('a verdict with a fine records it and does not carry it out', async ({ page }) => {
	const filed = await done(
		'dispute_file',
		{ respondent: `User:${principal(defendant)}`, title: penaltyTitle },
		claimant
	);
	const second = filed.dispute as string;
	penaltyCase = (
		await done('dispute_accept', { dispute: second, court: communityCourt }, founder())
	).case as string;
	await done('case_assign', { case: penaltyCase, judge: judgeSeat }, founder());
	const drafted = await done(
		'verdict_draft',
		{
			case: penaltyCase,
			decision: 'for the plaintiff, with a fine',
			penalties: [{ kind: 'fine', target: principal(defendant), amount: 500, symbol: 'E2E' }]
		},
		judge
	);
	penaltyVerdict = drafted.verdict as string;
	await done('verdict_issue', { verdict: penaltyVerdict }, judge);

	const [fine] = (await rows<Penalty>('Penalty')).filter((p) => p.verdict === penaltyVerdict);
	expect(fine).toMatchObject({
		kind: 'fine',
		target: `User:${principal(defendant)}`,
		amount: 500,
		symbol: 'E2E',
		status: 'pending',
		invoice: ''
	});
	expect(
		(await rows<{ reason: string }>('Invoice')).filter((i) => i.reason === `Penalty:${fine.id}`),
		'no invoice yet'
	).toEqual([]);

	await refused('penalty_execute', { penalty: fine.id }, founder(), /is not final yet/);
	await refused(
		'verdict_finalize',
		{ verdict: penaltyVerdict },
		founder(),
		/can be appealed until/
	);
	expect((await row<Penalty>('Penalty', fine.id)).status).toBe('pending');
	expect((await row<Verdict>('Verdict', penaltyVerdict)).status).toBe('issued');

	const { app, close } = await justiceOf(page, founder());
	try {
		await openDispute(app, penaltyTitle);
		await expect(app.getByText('For the plaintiff, with a fine')).toBeVisible();
		await expect(app.getByText('Fine', { exact: true })).toBeVisible();
		await expect(app.getByText('500 E2E')).toBeVisible();
		await snap(page, 'the penalty waits');
	} finally {
		await close();
	}
});

test('a party appeals the verdict and the court above grants it', async ({ page }) => {
	await leavesUnchanged(['Appeal', 'Case'], () =>
		refused('appeal_file', { verdict: penaltyVerdict }, founder(), /only a party/)
	);
	const appealed = await done('appeal_file', { verdict: penaltyVerdict }, defendant);
	const appeal = appealed.appeal as string;
	expect(appealed.court).toBe(supremeCourt);
	expect((await row<Case>('Case', penaltyCase)).status).toBe('appealed');
	await leavesUnchanged(['Appeal', 'Case'], () =>
		refused('appeal_file', { verdict: penaltyVerdict }, claimant, /cannot be appealed/)
	);
	expect((await rows<Appeal>('Appeal')).filter((a) => a.case === penaltyCase)).toHaveLength(1);

	// A judge of the court below, who does not hold appeal.allow, cannot decide the appeal. (The rule that only a
	// judge of the appeal court decides it is not exercised: the permission is checked first.)
	await leavesUnchanged(['Appeal', 'Verdict', 'Case'], () =>
		refused('appeal_decide', { appeal, granted: true, decision: 'x' }, judge, /needs appeal\.allow/)
	);
	await done('appeal_decide', { appeal, granted: true, decision: 'for the defendant' }, founder());

	const decided = await row<Appeal>('Appeal', appeal);
	expect(decided).toMatchObject({ status: 'granted', court: supremeCourt });
	const verdicts = (await rows<Verdict>('Verdict')).filter((v) => v.case === penaltyCase);
	expect(verdicts.find((v) => v.id === penaltyVerdict)?.status).toBe('overturned');
	expect(verdicts.find((v) => v.id === decided.new_verdict)).toMatchObject({
		decision: 'for the defendant',
		status: 'issued',
		issued_by: supremeSeat
	});
	expect((await row<Case>('Case', penaltyCase)).status).toBe('verdict_issued');

	// The page lists the verdicts of the case; it has no page of appeals in this version.
	const { app, close } = await justiceOf(page, founder());
	try {
		await openDispute(app, penaltyTitle);
		await expect(app.getByText('For the defendant')).toBeVisible();
		await expect(app.getByText('Overturned')).toBeVisible();
		await expect(app.getByText('Appeals are not listed in this version.')).toBeVisible();
		await snap(page, 'overturned');
	} finally {
		await close();
	}
});

// realms-gos#461: the fine of an overturned verdict is still pending, though its verdict can no longer become final.
test.fixme('the fine of an overturned verdict is no longer pending', async () => {
	const [fine] = (await rows<Penalty>('Penalty')).filter((p) => p.verdict === penaltyVerdict);
	expect(fine.status).not.toBe('pending');
});
