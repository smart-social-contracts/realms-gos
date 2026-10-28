import { backendActorReady } from '$lib/canisters';
import { read, run, must, type Reply } from '$lib/realm';
import {
	getExtensionManifest,
	getFileRegistryCanisterIds,
	listRegistryCodices,
	listRegistryExtensions
} from '$lib/file-registry-client';
import coreExtensions from '../../../../../core-extensions.json';
import { lookupLedgerSymbol } from './ledgerSymbol';
import { catalogTokenOptions, configureTokenPayload, type SharedTokenOption } from './sharedTokens';
import { LAUNCH_PHASES } from './wizardLogic';
import type {
	AvailableCodex,
	SetupActionResult,
	SetupDraft,
	SetupDraftPartial,
	SetupDraftSaveResult,
	SetupLaunchResult,
	SetupLaunchState,
	SetupState
} from './types';

const CODEX_INSTALL_POLL_MS = 5_000;
const CODEX_INSTALL_TIMEOUT_MS = 40 * 60 * 1_000;
const RAW_INSTALL_CALL_GRACE_MS = 90_000;
const RAW_COMPLETE_CALL_GRACE_MS = 60_000;
const COMPLETE_SETUP_POLL_MS = 5_000;
const COMPLETE_SETUP_TIMEOUT_MS = 5 * 60 * 1_000;
const LAUNCH_STATUS_POLL_MS = 5_000;
const HIDDEN_CODICES = new Set(['common', 'westminster', '_common']);
const DRAFT_KEY = 'realm_setup_draft';
/** Extensions every realm gets, whatever its codex: the repository's core-extensions.json. */
const CORE_EXTENSIONS: readonly string[] = coreExtensions.core_extensions;
/** Extensions not yet ported to the slim realm, which setup leaves out (core-extensions.json). */
const NOT_PORTED: ReadonlySet<string> = new Set(coreExtensions.not_ported_to_slim);

// SetupStageGate restores portal auth before the wizard mounts; the store may already hold an
// authenticated actor while backendReady is still waiting on realmInfo.fetch(). Wait for the actor only.
async function setupRead(name: string, args: object = {}): Promise<Reply> {
	await backendActorReady;
	return read(name, args);
}

async function setupRun(name: string, args: object = {}): Promise<Reply> {
	await backendActorReady;
	return run(name, args);
}

function actionResult(reply: Reply, extra: Partial<SetupActionResult> = {}): SetupActionResult {
	return reply.ok ? { success: true, ...extra } : { success: false, error: reply.error };
}

// ── The wizard draft: kept in this browser, applied to the realm at launch ──

type StoredDraft = { draft: SetupDraft; assets: { logo?: string; background?: string } };

function draftKey(): string {
	const ids = (globalThis as { __CANISTER_IDS?: { realm_backend?: string } }).__CANISTER_IDS;
	return `${DRAFT_KEY}:${ids?.realm_backend ?? ''}`;
}

function loadDraft(): StoredDraft {
	try {
		return JSON.parse(localStorage.getItem(draftKey()) || 'null') ?? { draft: {}, assets: {} };
	} catch {
		return { draft: {}, assets: {} };
	}
}

export async function saveSetupDraft(partial: SetupDraftPartial): Promise<SetupDraftSaveResult> {
	const { draft, assets } = loadDraft();
	const { branding, ...rest } = partial;
	const next: SetupDraft = { ...draft, ...(rest as SetupDraft) };
	if (branding) {
		const markers = { ...(draft.branding ?? {}) };
		if (branding.logo_data_url !== undefined) {
			assets.logo = branding.logo_data_url;
			Object.assign(markers, { logo: true, logo_size: branding.logo_data_url.length });
		}
		if (branding.background_data_url !== undefined) {
			assets.background = branding.background_data_url;
			Object.assign(markers, { background: true, background_size: branding.background_data_url.length });
		}
		if (branding.colors) markers.colors = branding.colors;
		next.branding = markers;
	}
	try {
		localStorage.setItem(draftKey(), JSON.stringify({ draft: next, assets }));
	} catch (err) {
		return { success: false, error: err instanceof Error ? err.message : 'Could not save the draft' };
	}
	return { success: true, draft: next };
}

export async function fetchSetupDraftAsset(kind: 'logo' | 'background'): Promise<string | null> {
	return loadDraft().assets[kind] ?? null;
}

// ── Setup state ──

let launch: SetupLaunchState | null = null;

/** The tokens the realm offers as its treasury token (``token_catalog``). */
export async function fetchTokenCatalog(): Promise<SharedTokenOption[]> {
	return catalogTokenOptions((await must(setupRead('token_catalog'))).tokens);
}

export async function fetchSetupState(): Promise<SetupState> {
	const [state, status, catalog] = await Promise.all([
		setupRun('setup_state'),
		setupRead('codex_status', { count: 1 }),
		setupRead('token_catalog')
	]);
	if (!state.ok) {
		const info = await must(setupRead('realm_info'));
		return {
			status: info.realm.status || 'alpha',
			creator: '',
			is_caller_authorized: false,
			codex: null,
			token: null,
			branding: null
		};
	}
	const realm = state.realm;
	const codex = status.ok && status.codex;
	const branded = realm.logo_url || realm.background_image_url || realm.primary_color;
	return {
		status: state.status,
		creator: state.founder,
		is_caller_authorized: true,
		codex: codex ? { package: codex.id.split('@')[0], version: codex.version } : null,
		token: realm.token_canister_id
			? {
					token_canister_id: realm.token_canister_id,
					symbol: realm.accounting_currency,
					decimals: realm.accounting_currency_decimals
				}
			: null,
		branding: branded
			? {
					logo_data_url: realm.logo_url || undefined,
					background_data_url: realm.background_image_url || undefined,
					colors: { primary: realm.primary_color || undefined }
				}
			: null,
		identity: { manifesto: realm.manifesto, welcome_message: realm.welcome_message },
		realm_name: realm.name,
		realm_manifesto: realm.manifesto,
		realm_welcome_message: realm.welcome_message,
		realm_token_canister_id: realm.token_canister_id || null,
		token_catalog: catalog.ok ? catalogTokenOptions(catalog.tokens) : [],
		draft: loadDraft().draft,
		launch: launch ?? undefined
	};
}

/** The realm's file registry (this build's when the caller cannot see the realm's), or '' when it has none. */
async function setupRegistry(): Promise<string> {
	const state = await setupRun('setup_state');
	const registry = (state.ok && state.realm.file_registry_canister_id) || getFileRegistryCanisterIds()[0];
	if (!registry && state.ok) throw new Error('This realm has no file registry configured');
	return registry || '';
}

/** The codices published in the realm's file registry (this build's when the caller cannot see the realm's). */
export async function listAvailableCodices(): Promise<AvailableCodex[]> {
	const registry = await setupRegistry();
	if (!registry) return [];
	const codices = (await listRegistryCodices(registry)).filter((c) => !HIDDEN_CODICES.has(c.codex_id));
	return Promise.all(
		codices.map(async (c) => {
			const manifest = c.latest ? await getExtensionManifest(registry, c.codex_id, c.latest) : null;
			return {
				id: c.codex_id,
				versions: c.versions,
				name: String(manifest?.name || c.codex_id),
				description: String(manifest?.description || ''),
				repository: String(manifest?.repository || '')
			};
		})
	);
}

export function isAmbiguousInstallError(error: unknown): boolean {
	const message = error instanceof Error ? error.message : String(error);
	return /returned undefined|cannot determine if the call was successful/i.test(message);
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

type RaceOutcome<T> = {
	settled: boolean;
	result?: T;
	error?: unknown;
};

async function raceWithGrace<T>(promise: Promise<T>, graceMs: number): Promise<RaceOutcome<T>> {
	let settled = false;
	let result: T | undefined;
	let error: unknown;

	const settlement = promise.then(
		(value) => {
			settled = true;
			result = value;
		},
		(err) => {
			settled = true;
			error = err;
		}
	);

	await Promise.race([sleep(graceMs), settlement]);
	return { settled, result, error };
}

export async function pollUntilCodexInstalled(
	packageName: string,
	version: string,
	timeoutMs = CODEX_INSTALL_TIMEOUT_MS,
	intervalMs = CODEX_INSTALL_POLL_MS
): Promise<void> {
	const installed = (state: SetupState) =>
		state.codex?.package === packageName && (!version || state.codex?.version === version);
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		if (installed(await fetchSetupState())) return;
		await sleep(intervalMs);
	}
	if (installed(await fetchSetupState())) return;
	throw new Error(
		`Codex installation timed out after ${Math.round(timeoutMs / 60_000)} minutes. Please refresh and check setup state.`
	);
}

async function pollUntilSetupComplete(
	timeoutMs = COMPLETE_SETUP_TIMEOUT_MS,
	intervalMs = COMPLETE_SETUP_POLL_MS
): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		if ((await fetchSetupState()).status !== 'setup') return;
		await sleep(intervalMs);
	}
	throw new Error(
		`Setup completion timed out after ${Math.round(timeoutMs / 60_000)} minutes. Please refresh and check realm status.`
	);
}

/**
 * Install ``payload.package`` at ``payload.version`` (``codex_install``). The realm does not take
 * codex ``params``. When the call's outcome is lost, poll the setup state instead.
 */
export async function installSetupCodex(
	payload: {
		package: string;
		version: string;
		params?: Record<string, unknown>;
	},
	options?: {
		rawCallGraceMs?: number;
		pollTimeoutMs?: number;
		pollIntervalMs?: number;
	}
): Promise<SetupActionResult> {
	const { settled, result, error } = await raceWithGrace(
		setupRun('codex_install', { codex: payload.package, version: payload.version }),
		options?.rawCallGraceMs ?? RAW_INSTALL_CALL_GRACE_MS
	);
	if (settled && error === undefined) {
		return actionResult(result!, {
			resolved_version: payload.version,
			setup_codex: { package: payload.package, version: payload.version }
		});
	}
	if (settled && !isAmbiguousInstallError(error)) throw error;
	await pollUntilCodexInstalled(
		payload.package,
		payload.version,
		options?.pollTimeoutMs,
		options?.pollIntervalMs
	);
	return { success: true, resolved_version: payload.version };
}

async function tokenStep(payload: Record<string, unknown>) {
	const ledger = String(payload.token_canister_id);
	const symbol = String(payload.symbol || '') || (await lookupLedgerSymbol(ledger).catch(() => ''));
	return {
		step: 'tokens',
		token_canister_id: ledger,
		accounting_currency: symbol || undefined,
		accounting_currency_decimals: payload.decimals
	};
}

/** Adopt a treasury ledger: ``token_canister_id``, with ``symbol`` (read from the ledger when absent) and ``decimals``. */
export async function configureSetupToken(payload: Record<string, unknown>): Promise<SetupActionResult> {
	const step = await tokenStep(payload);
	return actionResult(await setupRun('setup_step', step), {
		token: { token_canister_id: step.token_canister_id, symbol: step.accounting_currency }
	});
}

export async function applySetupDraftToken(): Promise<SetupActionResult> {
	try {
		const payload = configureTokenPayload(loadDraft().draft.token, await fetchTokenCatalog());
		if (!payload) return { success: false, error: 'token_canister_id is required' };
		return await configureSetupToken(payload);
	} catch (err) {
		return {
			success: false,
			error: err instanceof Error ? err.message : 'Could not apply treasury ledger'
		};
	}
}

export async function setSetupBranding(payload: {
	logo_data_url?: string;
	background_data_url?: string;
	colors?: { primary?: string };
	manifesto?: string;
	welcome_message?: string;
}): Promise<SetupActionResult> {
	const branding = await setupRun('setup_step', {
		step: 'branding',
		logo_url: payload.logo_data_url,
		background_image_url: payload.background_data_url,
		primary_color: payload.colors?.primary
	});
	if (!branding.ok) return actionResult(branding);
	return actionResult(
		await setupRun('setup_step', {
			step: 'realm',
			manifesto: payload.manifesto,
			welcome_message: payload.welcome_message
		})
	);
}

// ── Launch: apply the draft, then open the realm ──

function setLaunchStep(name: string, status: 'running' | 'completed' | 'failed', error: string | null = null) {
	launch = {
		...launch!,
		phase: name,
		updated_at: String(Date.now()),
		steps: launch!.steps.map((s) => (s.name === name ? { name, status, error } : s))
	};
}

/**
 * Install the core extensions and the codex's dependencies that the realm does not have yet, each at its newest
 * published version. The portal installs none (codex and extensions are chosen here), and the codex's hooks and the
 * realm's pages expect them, so they go in before the codex while the founder may still install extensions.
 */
async function installRealmExtensions(codex: { package: string; version: string }): Promise<Reply | null> {
	const registry = await setupRegistry();
	if (!registry) return { ok: false, error: 'This realm has no file registry to install extensions from' };
	const manifest = await getExtensionManifest(registry, codex.package, codex.version);
	const dependencies = Array.isArray(manifest?.dependencies) ? (manifest.dependencies as string[]) : [];
	const installed = new Set<string>();
	for (let from: number | null = 1; from !== null; ) {
		const page = await setupRead('extension_list', { from, count: 500 });
		if (!page.ok) return page;
		for (const ext of page.extensions) installed.add(ext.id);
		from = page.next ?? null;
	}
	const latest = new Map((await listRegistryExtensions(registry)).map((e) => [e.ext_id, e.latest]));
	for (const id of new Set([...CORE_EXTENSIONS, ...dependencies])) {
		if (installed.has(id) || NOT_PORTED.has(id)) continue;
		const version = latest.get(id);
		if (!version) return { ok: false, error: `${id} is not published in the realm's file registry` };
		const reply = await setupRun('extension_install', { extension: id, version });
		if (!reply.ok) return { ok: false, error: `${id} ${version}: ${reply.error}` };
	}
	return null;
}

async function runLaunch({ draft, assets }: StoredDraft): Promise<void> {
	const codex = draft.codex!;
	const phases: Record<string, () => Promise<Reply | null>> = {
		install_extensions: () => installRealmExtensions(codex),
		install_codex: async () => {
			const status = await setupRead('codex_status', { count: 1 });
			const active = status.ok && status.codex?.id === `${codex.package}@${codex.version}`;
			return active ? null : setupRun('codex_install', { codex: codex.package, version: codex.version });
		},
		configure_token: async () => {
			const payload = configureTokenPayload(draft.token, await fetchTokenCatalog());
			return payload ? setupRun('setup_step', await tokenStep(payload)) : null;
		},
		upload_branding: () =>
			setupRun('setup_step', {
				step: 'branding',
				logo_url: assets.logo,
				background_image_url: assets.background,
				primary_color: draft.branding?.colors?.primary
			}),
		apply_identity: () =>
			setupRun('setup_step', {
				step: 'realm',
				manifesto: draft.identity?.manifesto,
				welcome_message: draft.identity?.welcome_message
			}),
		complete: () => setupRun('setup_launch')
	};
	for (const { name } of LAUNCH_PHASES) {
		setLaunchStep(name, 'running');
		const reply = await phases[name]().catch((e: unknown) => ({
			ok: false as const,
			error: e instanceof Error ? e.message : String(e)
		}));
		if (reply && !reply.ok) {
			setLaunchStep(name, 'failed', reply.error);
			launch = { ...launch!, status: 'failed' };
			return;
		}
		setLaunchStep(name, 'completed');
	}
	launch = { ...launch!, status: 'completed' };
}

export async function fetchSetupLaunchStatus(): Promise<SetupLaunchState> {
	if (launch) return launch;
	const state = await setupRun('setup_state');
	const status = state.ok && state.status === 'setup' ? 'idle' : 'completed';
	return { status, phase: null, steps: [], updated_at: null };
}

/** Start applying the draft in the background; fetchSetupLaunchStatus reports its progress. */
export async function startSetupLaunch(): Promise<SetupLaunchResult> {
	const stored = loadDraft();
	if (!stored.draft.codex?.package) {
		return { success: false, error: 'Choose a codex before launching' };
	}
	if (launch?.status === 'running') return { success: true, launch };
	launch = {
		status: 'running',
		phase: null,
		updated_at: String(Date.now()),
		steps: LAUNCH_PHASES.map(({ name }) => ({ name, status: 'pending' as const, error: null }))
	};
	void runLaunch(stored);
	return { success: true, launch };
}

export { LAUNCH_STATUS_POLL_MS };

/** Open the realm (``setup_launch``); when the call's outcome is lost, poll the setup state instead. */
export async function completeSetup(options?: {
	rawCallGraceMs?: number;
	pollTimeoutMs?: number;
	pollIntervalMs?: number;
}): Promise<SetupActionResult> {
	const { settled, result, error } = await raceWithGrace(
		setupRun('setup_launch'),
		options?.rawCallGraceMs ?? RAW_COMPLETE_CALL_GRACE_MS
	);
	if (settled && error === undefined) return actionResult(result!, { status: result!.ok ? result!.status : undefined });
	if (settled && !isAmbiguousInstallError(error)) throw error;
	await pollUntilSetupComplete(options?.pollTimeoutMs, options?.pollIntervalMs);
	return { success: true };
}
