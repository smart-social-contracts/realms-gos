/** Realm language catalog and UI locale resolve (issue #361). */

export const LOCALE_CATALOG = [
	{ id: 'en', name: 'English' },
	{ id: 'es', name: 'Español' },
	{ id: 'de', name: 'Deutsch' },
	{ id: 'fr', name: 'Français' },
	{ id: 'it', name: 'Italiano' },
	{ id: 'zh-CN', name: '中文 (简体)' },
	{ id: 'ca-valencia', name: 'Valencià' },
	{ id: 'ca', name: 'Català' },
	{ id: 'eu', name: 'Euskara' },
	{ id: 'gl', name: 'Galego' },
	{ id: 'rm', name: 'Rumantsch' },
	{ id: 'zh-HK', name: '繁體中文（香港）' }
] as const;

export type CatalogLocaleId = (typeof LOCALE_CATALOG)[number]['id'];

export const CATALOG_IDS: readonly string[] = LOCALE_CATALOG.map((item) => item.id);

const CATALOG_ID_SET = new Set<string>(CATALOG_IDS);

export const DEFAULT_LANGUAGE = 'en';
export const FALLBACK_LOCALE = 'en';

export function localeLabel(id: string): string {
	return LOCALE_CATALOG.find((item) => item.id === id)?.name ?? id;
}

export function isCatalogLocale(id: string): boolean {
	return CATALOG_ID_SET.has(id);
}

export function normalizeLanguages(
	languages: unknown,
	primaryLanguage: unknown
): { languages: string[]; primary: string } | { error: string } {
	const parsed = Array.isArray(languages)
		? languages.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
		: null;
	if (!parsed) {
		return { error: 'languages must be a list of locale ids' };
	}
	const normalized: string[] = [];
	const seen = new Set<string>();
	for (const raw of parsed) {
		const id = raw.trim();
		if (!CATALOG_ID_SET.has(id)) {
			return { error: `unsupported locale: ${id}` };
		}
		if (seen.has(id)) continue;
		seen.add(id);
		normalized.push(id);
	}
	if (normalized.length === 0) {
		return { error: 'languages must include at least one locale' };
	}
	const primary = typeof primaryLanguage === 'string' ? primaryLanguage.trim() : '';
	if (!primary) {
		return { error: 'primary_language is required' };
	}
	if (!normalized.includes(primary)) {
		return { error: 'primary_language must be one of the enabled languages' };
	}
	return { languages: normalized, primary };
}

export function defaultRealmLanguages(): { languages: string[]; primary: string } {
	return { languages: [DEFAULT_LANGUAGE], primary: DEFAULT_LANGUAGE };
}

export function coerceRealmLanguages(
	languages: unknown,
	primaryLanguage: unknown
): { languages: string[]; primary: string } {
	const normalized = normalizeLanguages(languages, primaryLanguage);
	if ('error' in normalized) {
		return defaultRealmLanguages();
	}
	return normalized;
}

/**
 * Resolve the UI locale: user override → realm primary → en.
 * User override must be in the realm list; empty means use primary.
 */
export function resolveUiLocale(
	userOverride: string | null | undefined,
	languages: readonly string[] | null | undefined,
	primaryLanguage: string | null | undefined
): string {
	const enabled = (languages ?? []).filter((id) => CATALOG_ID_SET.has(id));
	let primary = (primaryLanguage ?? '').trim();
	if (!enabled.includes(primary)) {
		primary = enabled[0] || FALLBACK_LOCALE;
	}
	const override = (userOverride ?? '').trim();
	if (override && enabled.includes(override)) {
		return override;
	}
	if (CATALOG_ID_SET.has(primary)) {
		return primary;
	}
	return FALLBACK_LOCALE;
}
