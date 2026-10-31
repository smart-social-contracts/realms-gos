// The voting extension's pages, driven as a member: file a proposal, then vote on it.
import { expect } from '@playwright/test';
import type { ExtensionPage } from './extension';

export interface ProposalForm {
	/** The form's "Proposal type" choice. */
	type: 'poll' | 'transaction' | 'upgrade';
	title: string;
	description: string;
	/** The "Upgrade" type installs a package: an extension, or a codex. */
	upgrade?: { target: 'extension' | 'codex'; packageId: string; version: string };
}

/** Files `form` through the "Submit Proposal" page; returns the new proposal's id. */
export async function submitProposal(voting: ExtensionPage, form: ProposalForm): Promise<string> {
	await voting.getByRole('button', { name: 'Submit Proposal' }).click();
	await voting.locator('#rt-type').selectOption(form.type);
	await voting.locator('#rt-title').fill(form.title);
	await voting.locator('#rt-desc').fill(form.description);
	if (form.upgrade) {
		await voting.locator('#rt-upgrade-target').selectOption(form.upgrade.target);
		await voting.locator('#rt-package').fill(form.upgrade.packageId);
		await voting.locator('#rt-version').fill(form.upgrade.version);
	}
	await voting.getByRole('button', { name: 'Submit Proposal' }).click();
	const card = voting.locator('[data-proposal-id]', { hasText: form.title });
	await expect(card, 'the new proposal is listed').toBeVisible({ timeout: 60_000 });
	return (await card.getAttribute('data-proposal-id'))!;
}

/** Votes yes on the open proposal `id` from its card in the list. */
export async function voteYes(voting: ExtensionPage, id: string) {
	const card = voting.locator(`[data-proposal-id="${id}"]`);
	await expect(card).toBeVisible({ timeout: 60_000 });
	await card.getByRole('button', { name: 'Yes', exact: true }).click();
}
