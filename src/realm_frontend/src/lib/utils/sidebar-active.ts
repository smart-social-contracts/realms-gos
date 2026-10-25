import type { SidebarConfig, SidebarNavItem } from '../config/sidebar';
import { topUtilityItems } from '../config/sidebar';
import { isNavItemActive } from './breadcrumb';
import { visibleSidebarCategories } from './sidebar-member-chrome';

export const FOLD_ME = '__section_me__';
export const FOLD_REALM = '__section_realm__';
export const FOLD_MUNDUS = '__section_mundus__';

/** Sidebar row from get_sidebar — href and/or extension id. */
export type SidebarActiveItem = Pick<SidebarNavItem, 'href'> &
	Partial<Pick<SidebarNavItem, 'extensionId' | 'extension_id'>>;

export function sidebarItemIsActive(
	item: SidebarActiveItem,
	pathname: string,
	search = '',
): boolean {
	if (isNavItemActive(item.href, pathname, search)) return true;
	const extId = item.extensionId || item.extension_id;
	return Boolean(extId) && isNavItemActive(`/extensions/${extId}`, pathname, search);
}

/**
 * Fold ids that should be open so the current route is visible.
 * MY MUNDUS is a sibling of MY REALM — a mundus page must not open realm folds.
 */
export function activeSidebarFoldIds(
	config: SidebarConfig,
	pathname: string,
	search = '',
	profiles: readonly string[] | null | undefined = null,
): string[] {
	const open: string[] = [];

	if (topUtilityItems.some((item) => isNavItemActive(item.href, pathname, search))) {
		open.push(FOLD_ME);
	}

	const categories = visibleSidebarCategories(config.categories, profiles);
	const activeCategory = categories.find((category) =>
		category.items.some((item) => sidebarItemIsActive(item, pathname, search)),
	);
	const inRealm =
		config.welcomeItems.some((item) => sidebarItemIsActive(item, pathname, search)) ||
		Boolean(activeCategory);

	if (inRealm) {
		open.push(FOLD_REALM);
	}
	if (activeCategory) {
		open.push(activeCategory.id);
	}
	if (config.mundusItems.some((item) => sidebarItemIsActive(item, pathname, search))) {
		open.push(FOLD_MUNDUS);
	}

	return open;
}

/** Cache → live get_sidebar must re-expand when the active item appears. */
export function sidebarFoldExpandKey(
	pathname: string,
	search: string,
	membership: 'member' | 'guest',
	foldIds: readonly string[],
): string {
	return `${pathname}${search}|${membership}|${foldIds.join(',')}`;
}

/**
 * Path+search of an afterNavigate endpoint. SvelteKit passes
 * `from: { url: null }` on the initial `enter` navigation; throwing there
 * aborts client start-up before the router is installed, turning every
 * link click into a full page reload.
 */
export function navigationTargetKey(
	target: { url: { pathname: string; search: string } | null } | null | undefined,
): string {
	return `${target?.url?.pathname ?? ''}${target?.url?.search ?? ''}`;
}
