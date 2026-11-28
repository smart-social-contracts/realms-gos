// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { demoNotice, name } = vi.hoisted(() => ({ demoNotice: { set: (_: boolean) => {} }, name: {} }));
vi.mock('$lib/stores/realmInfo', async () => {
	const { writable } = await import('svelte/store');
	Object.assign(demoNotice, writable(true));
	Object.assign(name, writable('València'));
	return { testModeDemoNotice: demoNotice, realmName: name };
});

const portal = vi.hoisted(() => ({ embedded: false }));
vi.mock('$lib/portal-bridge.ts', () => ({ isEmbeddedInPortal: () => portal.embedded }));

import { bootI18n } from '$lib/i18n/boot';
import DemoDataBanner from './DemoDataBanner.svelte';

beforeEach(() => {
	bootI18n();
	localStorage.clear();
	demoNotice.set(true);
	portal.embedded = false;
});
afterEach(() => cleanup());

const banner = () => screen.queryByTestId('demo-data-banner');

describe('the demo banner of a realm with simulated data', () => {
	it('says the data is simulated, names the realm and links privacy, terms and reporting', () => {
		render(DemoDataBanner);
		const text = banner()?.textContent ?? '';
		expect(text).toContain('Demo with simulated data.');
		expect(text).toContain('València here is simulated, not a service of any real city or public body.');
		expect(text).toContain("Please don't enter real personal data.");
		const href = (name: string) => screen.getByRole('link', { name }).getAttribute('href');
		expect(href('Privacy')).toBe('https://demo.gos.earth/privacy');
		expect(href('Terms')).toBe('https://demo.gos.earth/terms');
		expect(href('Report content')).toBe('mailto:contact@realmsgos.org');
	});

	it('is not shown on a realm whose demo notice is off', () => {
		demoNotice.set(false);
		render(DemoDataBanner);
		expect(banner()).toBeNull();
	});

	it('leaves the notice to the portal when the realm is embedded in it', () => {
		portal.embedded = true;
		render(DemoDataBanner);
		expect(banner()).toBeNull();
	});

	it('folds to one line that keeps the notice and the links, and never goes away', async () => {
		render(DemoDataBanner);
		expect(screen.queryByRole('button', { name: /dismiss|close/i })).toBeNull();
		await fireEvent.click(screen.getByRole('button', { name: 'Hide details' }));
		expect(banner()?.textContent).toContain('Demo with simulated data.');
		expect(banner()?.textContent).not.toContain('play money');
		expect(screen.getByRole('link', { name: 'Privacy' })).toBeTruthy();
		expect(screen.getByRole('button', { name: 'Show details' }).getAttribute('aria-expanded')).toBe('false');
	});

	it('remembers that it was folded, and is still there on the next page', async () => {
		render(DemoDataBanner);
		await fireEvent.click(screen.getByRole('button', { name: 'Hide details' }));
		cleanup();
		render(DemoDataBanner);
		expect(banner()?.textContent).toContain('Demo with simulated data.');
		expect(screen.getByRole('button', { name: 'Show details' })).toBeTruthy();
	});
});
