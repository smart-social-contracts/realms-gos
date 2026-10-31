import { writable } from 'svelte/store';
import { applyResolvedLocale } from '$lib/i18n';
import { read, must } from '$lib/realm';

/** Personal locale override from user private_data. Empty = use realm primary. */
export const userLocale = writable('');

export function localeFromPrivateData(privateData: unknown): string {
	if (!privateData || typeof privateData !== 'object' || Array.isArray(privateData)) {
		return '';
	}
	const value = (privateData as { locale?: unknown }).locale;
	return typeof value === 'string' ? value.trim() : '';
}

export function applyStoredUserLocale(
	locale: string,
	languages: readonly string[] = ['en'],
	primaryLanguage = 'en'
): void {
	userLocale.set(locale);
	applyResolvedLocale({
		userLocale: locale,
		languages,
		primaryLanguage
	});
}

/** ``_actor`` is ignored: the locale comes from ``read('me')`` on the realm backend. */
export async function refreshUserLocale(_actor?: unknown): Promise<void> {
	try {
		const raw = (await must(read('me'))).private_data;
		const locale = localeFromPrivateData(typeof raw === 'string' && raw ? JSON.parse(raw) : {});
		userLocale.set(locale);
		const { get } = await import('svelte/store');
		const { realmInfo } = await import('$lib/stores/realmInfo');
		const realm = get(realmInfo);
		applyResolvedLocale({
			userLocale: locale,
			languages: realm.languages,
			primaryLanguage: realm.primaryLanguage
		});
	} catch {
		// Anonymous or unjoined callers have no personal locale.
	}
}
