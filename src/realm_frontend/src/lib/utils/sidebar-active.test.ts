import { describe, expect, it } from 'vitest';
import type { SidebarConfig } from '../config/sidebar';
import { isNavItemActive } from './breadcrumb';
import {
	FOLD_ME,
	FOLD_MUNDUS,
	FOLD_REALM,
	activeSidebarFoldIds,
	navigationTargetKey,
	sidebarFoldExpandKey,
	sidebarItemIsActive,
} from './sidebar-active';

const justiceConfig: SidebarConfig = {
	welcomeItems: [
		{
			label: 'My Dashboard',
			icon: 'ti-home',
			extension_id: 'member_dashboard',
			href: '/extensions/member_dashboard',
		},
	],
	mundusItems: [
		{
			label: 'Marketplace',
			icon: 'ti-building-store',
			href: '/extensions/marketplace',
		},
	],
	categories: [
		{
			id: 'public_services',
			label: 'Public Services',
			items: [
				{
					label: 'Justice',
					icon: 'ti-gavel',
					extension_id: 'justice_litigation',
					href: '/extensions/justice_litigation',
				},
			],
		},
		{
			id: 'realm_management',
			label: 'Realm Management',
			items: [
				{
					label: 'Import & Export',
					icon: 'ti-transfer',
					extension_id: 'import_export',
					href: '/extensions/import_export',
				},
			],
		},
	],
	defaultPath: '/extensions/member_dashboard',
};

describe('isNavItemActive (Justice deep link)', () => {
	it('selects the Justice href on /extensions/justice_litigation', () => {
		expect(isNavItemActive('/extensions/justice_litigation', '/extensions/justice_litigation')).toBe(
			true,
		);
		expect(
			isNavItemActive(
				'/extensions/justice_litigation',
				'/extensions/justice_litigation/cases/1',
			),
		).toBe(true);
		expect(isNavItemActive('/extensions/member_dashboard', '/extensions/justice_litigation')).toBe(
			false,
		);
	});

	it('matches a portal-prefixed path the host bar uses', () => {
		expect(
			isNavItemActive(
				'/extensions/justice_litigation',
				'/r/agorastaging/extensions/justice_litigation',
			),
		).toBe(true);
	});
});

describe('sidebarItemIsActive', () => {
	it('matches by extension_id when href is missing the usual prefix', () => {
		expect(
			sidebarItemIsActive(
				{ href: '/justice', extension_id: 'justice_litigation' },
				'/extensions/justice_litigation',
			),
		).toBe(true);
	});
});

describe('activeSidebarFoldIds', () => {
	it('opens MY REALM + Public Services for Justice', () => {
		expect(
			activeSidebarFoldIds(justiceConfig, '/extensions/justice_litigation', '', ['admin']),
		).toEqual([FOLD_REALM, 'public_services']);
	});

	it('opens ME for Account / Messages / Settings', () => {
		expect(activeSidebarFoldIds(justiceConfig, '/identities', '', ['member'])).toEqual([FOLD_ME]);
	});

	it('opens only MY MUNDUS for a mundus item (not MY REALM)', () => {
		expect(activeSidebarFoldIds(justiceConfig, '/extensions/marketplace', '', ['member'])).toEqual([
			FOLD_MUNDUS,
		]);
	});

	it('hides realm_management until membership profiles arrive', () => {
		expect(activeSidebarFoldIds(justiceConfig, '/extensions/import_export', '', [])).toEqual([]);
		expect(activeSidebarFoldIds(justiceConfig, '/extensions/import_export', '', ['member'])).toEqual(
			[FOLD_REALM, 'realm_management'],
		);
	});

	it('returns no folds when the live menu has not arrived yet', () => {
		const cacheMiss: SidebarConfig = {
			welcomeItems: [],
			mundusItems: [],
			categories: [],
			defaultPath: '/',
		};
		expect(activeSidebarFoldIds(cacheMiss, '/extensions/justice_litigation', '', ['admin'])).toEqual(
			[],
		);
	});
});

describe('sidebarFoldExpandKey', () => {
	it('changes when a cached empty menu later contains Justice', () => {
		const path = '/extensions/justice_litigation';
		const before = sidebarFoldExpandKey(path, '', 'member', []);
		const after = sidebarFoldExpandKey(
			path,
			'',
			'member',
			activeSidebarFoldIds(justiceConfig, path, '', ['admin']),
		);
		expect(before).toBe('/extensions/justice_litigation|member|');
		expect(after).toBe('/extensions/justice_litigation|member|__section_realm__,public_services');
		expect(before).not.toBe(after);
	});
});

describe('navigationTargetKey', () => {
	it('tolerates the initial enter navigation (from.url is null)', () => {
		expect(navigationTargetKey({ url: null })).toBe('');
		expect(navigationTargetKey(null)).toBe('');
		expect(navigationTargetKey(undefined)).toBe('');
	});

	it('joins pathname and search', () => {
		expect(
			navigationTargetKey({ url: { pathname: '/extensions/voting', search: '?tab=1' } }),
		).toBe('/extensions/voting?tab=1');
	});
});
