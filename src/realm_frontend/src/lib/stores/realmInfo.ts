import { writable, derived, get } from 'svelte/store';
import { browser } from '$app/environment';
import { resolveRealmMarkSrc } from '$lib/branding/realmMark';
import { backendActorReady } from '$lib/canisters';
import { read } from '$lib/realm';
import { quarterFederation } from '$lib/stores/quarters';
import { applyBrandingPrimary, DEFAULT_PRIMARY_COLOR, parsePrimaryColor } from '$lib/theme/brandingPrimary';
import { applyResolvedLocale } from '$lib/i18n';
import { defaultRealmLanguages } from '$lib/i18n/realmLocales';
import { userLocale } from '$lib/stores/userLocale';
import { resolveDemoNoticeBodies, resolveDemoNoticeEnabled, resolveDisableMonetaryTokens } from '$lib/config/hostTestFlags';

const STATUS_QUERY_TIMEOUT_MS = 12_000;

/** Prevent hung IC queries from blocking backendReady forever during boot. */
function withQueryTimeout<T>(
	promise: Promise<T>,
	ms: number,
	label: string
): Promise<T | null> {
	return new Promise((resolve) => {
		let settled = false;
		const timer = setTimeout(() => {
			if (!settled) {
				console.warn(`[realmInfo] ${label} timed out after ${ms}ms`);
				settled = true;
				resolve(null);
			}
		}, ms);
		promise
			.then((value) => {
				if (!settled) {
					settled = true;
					clearTimeout(timer);
					resolve(value);
				}
			})
			.catch((err) => {
				if (!settled) {
					settled = true;
					clearTimeout(timer);
					console.warn(`[realmInfo] ${label} failed:`, err);
					resolve(null);
				}
			});
	});
}

interface CanisterInfo {
	canister_id: string;
	canister_type: string;
}

interface QuarterInfo {
	name: string;
	canister_id: string;
	population: number;
	status: string;
}

interface RealmInfo {
	name: string;
	welcomeMessage: string;
	manifesto: string;
	openRegistration: boolean;
	aiAssistantEnabled: boolean;
	registries: CanisterInfo[];
	quarters: QuarterInfo[];
	isQuarter: boolean;
	parentRealmCanisterId: string;
	logoUrl: string;
	backgroundImageUrl: string;
	primaryColor: string;
	languages: string[];
	primaryLanguage: string;
	testMode: boolean;
	testModeIIBypass: boolean;
	testModeUserSelfRegistration: boolean;
	testModeDemoData: boolean;
	testModeSkipTerms: boolean;
	testModeSkipPassportZkproof: boolean;
	testModeDisableMonetaryTokens: boolean;
	testModeDemoNotice: boolean;
	demoNoticeBody: Record<string, string>;
	network: string;
	loading: boolean;
	error: string | null;
}

const createRealmInfoStore = () => {
	const { subscribe, set, update } = writable<RealmInfo>({
		name: '',
		welcomeMessage: '',
		manifesto: '',
		openRegistration: false,
		aiAssistantEnabled: true,
		registries: [],
		quarters: [],
		isQuarter: false,
		parentRealmCanisterId: '',
		logoUrl: resolveRealmMarkSrc(''),
		backgroundImageUrl: '',
		primaryColor: DEFAULT_PRIMARY_COLOR,
		languages: ['en'],
		primaryLanguage: 'en',
		testMode: false,
		testModeIIBypass: false,
		testModeUserSelfRegistration: false,
		testModeDemoData: false,
		testModeSkipTerms: false,
		testModeSkipPassportZkproof: false,
		testModeDisableMonetaryTokens: false,
		testModeDemoNotice: false,
		demoNoticeBody: {},
		network: '',
		loading: true,
		error: null
	});

	return {
		subscribe,
		fetch: async () => {
			update(state => ({ ...state, loading: true, error: null }));
			
			try {
			// Mount-time callers (sidebar layout, navbar) race the provisional
			// actor setup in canisters.js. Wait for the provisional actor instead of
			// erroring with "Actor not initialized" on a cold load. Must NOT await
			// backendReady here: initializeBackendStore() awaits this fetch, so
			// awaiting backendReady would deadlock the boot (circular await).
			await backendActorReady;

				// Languages and the AI assistant switch are not public on this realm version: they keep
				// their defaults.
				const [info, directory] = await Promise.all([
					withQueryTimeout(read('realm_info'), STATUS_QUERY_TIMEOUT_MS, 'realm_info'),
					withQueryTimeout(read('quarter_directory'), STATUS_QUERY_TIMEOUT_MS, 'quarter_directory')
				]);
				if (!info?.ok) {
					throw new Error('Failed to fetch realm info');
				}
				const realm = info.realm;
				const flags = realm.test_flags || {};
				const network = realm.network || '';
				const primaryColor = parsePrimaryColor(realm.primary_color) ?? DEFAULT_PRIMARY_COLOR;
				const languageConfig = defaultRealmLanguages();
				const federation = directory?.ok
					? quarterFederation(directory)
					: { quarters: [], isQuarter: false, parentRealmCanisterId: '' };
				update(state => ({
					...state,
					name: realm.name || '',
					welcomeMessage: realm.welcome_message || '',
					manifesto: realm.manifesto || '',
					openRegistration: realm.open_registration ?? false,
					...federation,
					logoUrl: resolveRealmMarkSrc(realm.logo_url || ''),
					backgroundImageUrl: realm.background_image_url || '',
					primaryColor,
					languages: languageConfig.languages,
					primaryLanguage: languageConfig.primary,
					network,
					testMode: !!realm.test_mode,
					testModeIIBypass: !!flags.test_mode_ii_bypass,
					testModeUserSelfRegistration: !!flags.test_mode_user_self_registration,
					testModeDemoData: !!flags.test_mode_demo_data,
					testModeSkipTerms: !!flags.test_mode_skip_terms,
					testModeSkipPassportZkproof: !!flags.test_mode_skip_passport_zkproof,
					testModeDisableMonetaryTokens: resolveDisableMonetaryTokens(flags.test_mode_disable_monetary_tokens, network),
					testModeDemoNotice: resolveDemoNoticeEnabled(flags.test_mode_demo_notice, network),
					demoNoticeBody: resolveDemoNoticeBodies(flags.demo_notice_body),
					loading: false
				}));
				if (browser) {
					applyBrandingPrimary(primaryColor);
					applyResolvedLocale({
						userLocale: get(userLocale),
						languages: languageConfig.languages,
						primaryLanguage: languageConfig.primary
					});
				}
			} catch (error) {
				console.error('Error fetching realm info:', error);
				update(state => ({
					...state,
					loading: false,
					error: error instanceof Error ? error.message : 'Unknown error'
				}));
			}
		}
	};
};

export const realmInfo = createRealmInfoStore();

// Derived store for just the realm name
export const realmName = derived(realmInfo, $realmInfo => $realmInfo.name);

// Derived store for welcome message
export const realmWelcomeMessage = derived(realmInfo, $realmInfo => $realmInfo.welcomeMessage);

// Derived store for realm manifesto
export const realmManifesto = derived(realmInfo, $realmInfo => $realmInfo.manifesto);

// Derived store for open registration flag
export const realmOpenRegistration = derived(realmInfo, $realmInfo => $realmInfo.openRegistration);

export const realmLanguages = derived(realmInfo, $realmInfo => $realmInfo.languages);

export const realmPrimaryLanguage = derived(realmInfo, $realmInfo => $realmInfo.primaryLanguage);

export const aiAssistantEnabled = derived(realmInfo, $realmInfo => $realmInfo.aiAssistantEnabled);

// Derived stores for test mode flags (source of truth: backend status)
export const testMode = derived(realmInfo, $r => $r.testMode);
/** Backend status is source of truth; fall back to /canister_ids.js sync hint during boot. */
export const testModeIIBypass = derived(
	realmInfo,
	$r =>
		__REALMS_TEST_BUILD__ &&
		($r.testModeIIBypass || !!globalThis.__CANISTER_IDS?.test_mode_ii_bypass),
);
export const testModeUserSelfRegistration = derived(realmInfo, $r => $r.testModeUserSelfRegistration);
export const testModeDemoData = derived(realmInfo, $r => $r.testModeDemoData);
export const testModeSkipTerms = derived(realmInfo, $r => $r.testModeSkipTerms);
export const testModeSkipPassportZkproof = derived(realmInfo, $r => $r.testModeSkipPassportZkproof);
export const testModeDisableMonetaryTokens = derived(realmInfo, $r => $r.testModeDisableMonetaryTokens);
export const testModeDemoNotice = derived(realmInfo, $r => $r.testModeDemoNotice);
export const demoNoticeBody = derived(realmInfo, $r => $r.demoNoticeBody);
export const realmNetwork = derived(realmInfo, $r => $r.network);
