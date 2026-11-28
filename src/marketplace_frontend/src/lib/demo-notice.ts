import { CONTACT_MAILTO } from './contact.ts';

// The legal pages live on the portal; the marketplace has none of its own. Both texts cover gos.earth and
// realmsgos.org, so each marketplace links its own environment's portal.
export const PRIVACY_URL = 'https://demo.gos.earth/privacy';
export const TERMS_URL = 'https://demo.gos.earth/terms';
export const PRODUCTION_PRIVACY_URL = 'https://gos.earth/privacy';
export const PRODUCTION_TERMS_URL = 'https://gos.earth/terms';
export const REPORT_URL = CONTACT_MAILTO;

/**
 * One bundle serves every environment, so the host is all that tells production apart. A deny-list, not an
 * allow-list: staging, its demo alias and any environment added later share open, simulated data and get the
 * notice by default. Forgetting a host here shows the notice where it is not needed, never hides it where it is.
 */
const PRODUCTION_HOSTS = ['realmsgos.org', 'www.realmsgos.org'];

function isProductionHost(hostname: string): boolean {
	return PRODUCTION_HOSTS.includes(hostname.toLowerCase());
}

export function showsDemoNotice(hostname: string): boolean {
	return !isProductionHost(hostname);
}

/** The footer's legal links, on every host: production's own pages there, the demo's everywhere else. */
export function legalLinks(hostname: string): { privacy: string; terms: string } {
	return isProductionHost(hostname)
		? { privacy: PRODUCTION_PRIVACY_URL, terms: PRODUCTION_TERMS_URL }
		: { privacy: PRIVACY_URL, terms: TERMS_URL };
}

// Collapsed is the only state a visitor can keep. There is no dismissed state: the notice always renders.
const COLLAPSED_KEY = 'marketplaceDemoNoticeCollapsed';

/** `storage` is a getter because reading `localStorage` itself throws where site data is blocked. */
export function readCollapsed(storage: () => Pick<Storage, 'getItem'>): boolean {
	try {
		return storage().getItem(COLLAPSED_KEY) === '1';
	} catch {
		return false;
	}
}

export function writeCollapsed(storage: () => Pick<Storage, 'setItem'>, collapsed: boolean): void {
	try {
		storage().setItem(COLLAPSED_KEY, collapsed ? '1' : '0');
	} catch {
		/* the choice lasts for this page only */
	}
}
