import { REALM_DEFAULT_MARK, resolveRealmMarkSrc } from './realmMark';

/**
 * Point the browser tab's icon at the realm's mark, or at the default one when the realm has none that loads.
 * app.html names /custom/logo.png, which a realm without a logo does not have: the tab then showed a blank page icon.
 */
export function applyFavicon(logoUrl: string | null | undefined): void {
	if (typeof document === 'undefined' || typeof Image === 'undefined') return;
	const wanted = resolveRealmMarkSrc(logoUrl);
	const set = (href: string) => {
		let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
		if (!link) {
			link = document.createElement('link');
			link.rel = 'icon';
			document.head.appendChild(link);
		}
		if (link.getAttribute('href') !== href) link.setAttribute('href', href);
	};
	const probe = new Image();
	probe.onload = () => set(wanted);
	probe.onerror = () => set(REALM_DEFAULT_MARK);
	probe.src = wanted;
}
