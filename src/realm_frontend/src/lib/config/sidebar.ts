/**
 * Sidebar configuration types and constants.
 *
 * The sidebar is resolved by the backend (get_sidebar endpoint) which merges
 * extension manifests, default category ordering, database overrides, and
 * department visibility rules. The frontend simply renders the result.
 */

export interface SidebarNavItem {
	label: string;
	icon: string;
	extensionId?: string;
	/** Backend get_sidebar uses snake_case. */
	extension_id?: string;
	href: string;
	tooltip?: string;
}

/** Slim row from realm_backend.get_sidebar_manifests(). */
export interface SidebarManifest {
	id: string;
	name?: string;
	sidebar_label?: string | Record<string, string>;
	is_default?: boolean;
	show_in_sidebar?: boolean;
	categories?: string[];
}

export interface SidebarCategory {
	id: string;
	label: string;
	items: SidebarNavItem[];
}

export interface SidebarConfig {
	welcomeItems: SidebarNavItem[];
	mundusItems: SidebarNavItem[];
	categories: SidebarCategory[];
	defaultPath: string;
	/** Codex extension overrides: base system extension id -> replacement id. */
	extensionOverrides?: Record<string, string>;
	/** Same get_sidebar_manifests() list the sidebar paints MY REALM from. */
	manifests?: SidebarManifest[];
}

export interface TopUtilityItem {
	labelKey: string;
	tooltipKey?: string;
	icon: string;
	href: string;
}

export const topUtilityItems: TopUtilityItem[] = [
	{
		labelKey: 'chrome.account',
		tooltipKey: 'chrome.account_tooltip',
		icon: 'ti-user-circle',
		href: '/identities',
	},
	{
		labelKey: 'chrome.messages',
		tooltipKey: 'chrome.messages_tooltip',
		icon: 'ti-mail',
		href: '/messages',
	},
	{
		labelKey: 'chrome.power_of_attorney',
		tooltipKey: 'chrome.power_of_attorney_tooltip',
		icon: 'ti-user-shield',
		href: '/delegations',
	},
	{
		labelKey: 'chrome.settings',
		tooltipKey: 'chrome.settings_tooltip',
		icon: 'ti-settings',
		href: '/settings',
	},
];
