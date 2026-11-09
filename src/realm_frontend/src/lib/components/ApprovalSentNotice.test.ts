// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/stores/delegation.js', async () => {
	const { writable } = await import('svelte/store');
	return { delegationsAsDelegate: writable([{ id: 'D-1', grantor: 'alice-principal-long-id', label: 'Alice' }]) };
});

import { bootI18n } from '$lib/i18n/boot';
import { sentForApproval } from '$lib/stores/approvalSent';
import ApprovalSentNotice from './ApprovalSentNotice.svelte';

beforeEach(() => bootI18n());
afterEach(() => {
	cleanup();
	sentForApproval.set(null);
});

describe('the notice that an act was sent to a member to approve', () => {
	it('shows nothing until an act is sent', () => {
		render(ApprovalSentNotice);
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('names the member by the label of the grant and links the action', () => {
		sentForApproval.set({ action: 'AC-3', href: '/delegations/confirm/AC-3', grantor: 'alice-principal-long-id' });
		render(ApprovalSentNotice);
		expect(screen.getByRole('status').textContent).toContain('Sent to Alice for approval.');
		expect(screen.getByRole('link', { name: 'See it' }).getAttribute('href')).toBe('/delegations/confirm/AC-3');
	});

	it('names a member it has no label for by their principal, shortened', () => {
		sentForApproval.set({ action: 'AC-3', href: '/delegations/confirm/AC-3', grantor: 'zzzzzzzzzzzzzzzzzzzzzzzz' });
		render(ApprovalSentNotice);
		expect(screen.getByRole('status').textContent).toContain('Sent to zzzzzzzz…zzzzzz for approval.');
	});

	it('goes away when dismissed', async () => {
		sentForApproval.set({ action: 'AC-3', href: '/delegations/confirm/AC-3', grantor: 'alice-principal-long-id' });
		render(ApprovalSentNotice);
		await fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
		expect(screen.queryByRole('status')).toBeNull();
	});
});
