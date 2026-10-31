import demoNoticeDefaults from './demo_notice_defaults.json';

export type DemoNoticeBodies = Record<string, string>;

/** The host's Legal English notice, shown when the realm stores no English body of its own. */
export const DEFAULT_DEMO_NOTICE_EN: string = demoNoticeDefaults.en;

export const NOTICE_LOCALE_SLOTS = [
	'en',
	'es',
	'de',
	'fr',
	'it',
	'zh-CN',
	'ca-valencia'
] as const;

export function noticeTranslationSlots(
	slots: readonly string[] = NOTICE_LOCALE_SLOTS
): string[] {
	return slots.filter((loc) => loc !== 'en');
}

export function filledNoticeTranslationCount(
	bodies: DemoNoticeBodies | null | undefined
): number {
	const map = bodies && typeof bodies === 'object' ? bodies : {};
	return noticeTranslationSlots().filter((loc) => String(map[loc] || '').trim()).length;
}

export function defaultDisableMonetaryTokens(network?: string | null): boolean {
	const n = (network || '').trim().toLowerCase();
	return n === 'staging' || n === 'demo' || n === 'test';
}

export function defaultDemoNotice(network?: string | null): boolean {
	const n = (network || '').trim().toLowerCase();
	return n === 'staging' || n === 'demo';
}

export function resolveDisableMonetaryTokens(
	explicit: boolean | null | undefined,
	network?: string | null
): boolean {
	if (typeof explicit === 'boolean') return explicit;
	return defaultDisableMonetaryTokens(network);
}

export function resolveDemoNoticeEnabled(
	explicit: boolean | null | undefined,
	network?: string | null
): boolean {
	if (typeof explicit === 'boolean') return explicit;
	return defaultDemoNotice(network);
}

/** Join shows the notice unless skip_terms. II bypass does not skip it. */
export function shouldShowJoinNotice(
	demoNotice: boolean,
	skipTerms: boolean
): boolean {
	return demoNotice && !skipTerms;
}

/** A custom ledger is the founder's own choice, so only the catalog's monetary tokens are ever disabled. */
export function isTokenChoiceSelectable(monetary: boolean, monetaryDisabled: boolean): boolean {
	return !(monetary && monetaryDisabled);
}

export function monetaryUnavailableLabel(locale: string): string {
	const id = (locale || '').trim().toLowerCase();
	if (id === 'es' || id.startsWith('es-')) {
		return 'No disponible en esta demo';
	}
	return 'Not available in this demo';
}

/** Locale → body: the stored bodies, with English seeded from Legal. Translations are never invented. */
export function resolveDemoNoticeBodies(stored: unknown): DemoNoticeBodies {
	const map: DemoNoticeBodies = {};
	if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
		for (const [loc, body] of Object.entries(stored)) {
			if (typeof body === 'string') map[loc] = body;
		}
	}
	return { ...map, en: map.en?.trim() ? map.en : DEFAULT_DEMO_NOTICE_EN };
}

export function resolveDemoNoticeView(
	bodies: DemoNoticeBodies | null | undefined,
	primaryLanguage: string
): {
	english: string;
	primary: string;
	primaryLanguage: string;
	showPrimary: boolean;
} {
	const map = bodies && typeof bodies === 'object' ? bodies : {};
	const english = String(map.en || '').trim();
	const primary = (primaryLanguage || 'en').trim() || 'en';
	const primaryBody = primary === 'en' ? '' : String(map[primary] || '').trim();
	return {
		english,
		primary: primaryBody,
		primaryLanguage: primary,
		showPrimary: Boolean(primaryBody) && primary !== 'en'
	};
}
