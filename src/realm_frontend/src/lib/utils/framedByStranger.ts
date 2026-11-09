// Same parents as frame-ancestors in static/.ic-assets.json5; keep in sync.
const PORTAL_PARENT =
	/^(https:\/\/([a-z0-9-]+\.)*gos\.earth|https:\/\/realmsgos\.org|http:\/\/([a-z0-9-]+\.)*localhost(:\d+)?|http:\/\/127\.0\.0\.1(:\d+)?)$/;

/**
 * Whether this page sits inside a frame of a site that is neither this realm nor the portal. A page where a member
 * decides must not be shown there: the site around it could cover the buttons or click them.
 */
export function framedByStranger(): boolean {
	if (typeof window === 'undefined' || window.self === window.top) return false;
	const allowed = new Set([window.location.origin]);
	const portal = (globalThis as any).__CANISTER_IDS?.portal_url;
	if (portal) {
		try {
			allowed.add(new URL(portal).origin);
		} catch {
			/* ignore */
		}
	}
	const ancestors = window.location.ancestorOrigins;
	let parent = ancestors?.length ? ancestors[0] : '';
	if (!parent && document.referrer) {
		try {
			parent = new URL(document.referrer).origin;
		} catch {
			parent = '';
		}
	}
	return !parent || !(allowed.has(parent) || PORTAL_PARENT.test(parent));
}
