/**
 * Tabler icon resolver — maps `ti-<name>` strings to Svelte icon components.
 * Manifest `icon` fields use the bare name (e.g. "gavel"); the sidebar
 * prepends `ti-` before lookup.
 */
import {
	IconUserCircle,
	IconMail,
	IconSettings,
	IconUserShield,
	IconLayoutDashboard,
	IconIdBadge,
	IconBallpen,
	IconGavel,
	IconMap2,
	IconReceipt,
	IconFileCode,
	IconUserPlus,
	IconLayersSubtract,
	IconChartBar,
	IconShieldDollar,
	IconBuildingStore,
	IconPackage,
	IconServer,
	IconActivity,
	IconTopologyStar3,
	IconDatabase,
	IconMessageChatbot,
	IconBell,
	IconHome,
	IconShieldLock,
	IconFlask,
	IconWand,
	IconBug,
	IconWorld,
	IconAdjustments,
} from '@tabler/icons-svelte';

const tablerIconMap: Record<string, any> = {
	'ti-user-circle': IconUserCircle,
	'ti-mail': IconMail,
	'ti-settings': IconSettings,
	'ti-user-shield': IconUserShield,
	'ti-layout-dashboard': IconLayoutDashboard,
	'ti-id-badge': IconIdBadge,
	'ti-ballpen': IconBallpen,
	'ti-gavel': IconGavel,
	'ti-map-2': IconMap2,
	'ti-receipt': IconReceipt,
	'ti-file-code': IconFileCode,
	'ti-user-plus': IconUserPlus,
	'ti-layers-subtract': IconLayersSubtract,
	'ti-chart-bar': IconChartBar,
	'ti-safe': IconShieldDollar,
	'ti-building-store': IconBuildingStore,
	'ti-package': IconPackage,
	'ti-server': IconServer,
	'ti-activity': IconActivity,
	'ti-topology-star-3': IconTopologyStar3,
	'ti-database': IconDatabase,
	'ti-message-chatbot': IconMessageChatbot,
	'ti-bell': IconBell,
	'ti-home': IconHome,
	'ti-shield-lock': IconShieldLock,
	'ti-flask': IconFlask,
	'ti-wand': IconWand,
	'ti-bug': IconBug,
	'ti-world': IconWorld,
	'ti-shield-dollar': IconShieldDollar,
	'ti-adjustments': IconAdjustments,
};

/**
 * Resolve a `ti-<name>` icon string to its Tabler Svelte component.
 * Returns IconLayoutDashboard as default fallback.
 */
export function getTablerIcon(iconName: string): any {
	return tablerIconMap[iconName] ?? IconLayoutDashboard;
}
