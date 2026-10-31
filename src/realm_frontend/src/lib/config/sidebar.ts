/**
 * Sidebar configuration types and constants.
 *
 * The sidebar is built by loadSidebar (stores/sidebar.ts) from the realm's
 * extension_list: the extensions available to the caller, with each one's
 * sidebar settings (label, icon, category, order, show).
 */

export interface SidebarNavItem {
	label: string;
	icon: string;
	extensionId?: string;
	/** loadSidebar uses snake_case. */
	extension_id?: string;
	href: string;
	tooltip?: string;
}

/** Slim row per installed extension, as loadSidebar builds it from extension_list. */
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
	/** The rows the sidebar paints MY REALM from. */
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
