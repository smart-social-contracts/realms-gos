/** Same-origin branding uploaded by install_branding_from_registry. */
export const REALM_CUSTOM_LOGO = '/custom/logo.png';

/**
 * The mark of a realm that has none of its own. It is shown only after the realm's own mark has failed to load,
 * never while that one is still loading: a realm with a logo must not flash this one first.
 */
export const REALM_DEFAULT_MARK = '/images/logo_sphere_only.svg';
/** The same mark for a dark background. */
export const REALM_DEFAULT_MARK_ON_DARK = '/images/logo_sphere_only_white.svg';

/** Platform marks that must never appear in live-realm chrome. */
export const PLATFORM_PLACEHOLDER_LOGOS = [
	'/images/logo_sphere_only.svg',
	'/images/logo_sphere_only_white.svg',
	'/images/logo.png',
	'/images/logo.svg',
	'/images/logo_mark.svg',
	'/images/logo_mark_white.svg'
] as const;

function logoPath(url: string): string {
	const withoutQuery = url.split('?')[0] ?? url;
	try {
		return new URL(withoutQuery, 'https://realm.invalid').pathname;
	} catch {
		return withoutQuery;
	}
}

export function isPlatformPlaceholderLogo(url: string | null | undefined): boolean {
	if (!url) return true;
	const path = logoPath(url);
	return (PLATFORM_PLACEHOLDER_LOGOS as readonly string[]).includes(path);
}

/** Immediate live-realm mark: custom branding, never the GOS planet or clover (see REALM_DEFAULT_MARK for a failed load). */
export function resolveRealmMarkSrc(logoUrl?: string | null): string {
	if (logoUrl && !isPlatformPlaceholderLogo(logoUrl)) {
		return logoUrl;
	}
	return REALM_CUSTOM_LOGO;
}
