import { get } from 'svelte/store';
import { backendStore, backendActorReady } from '$lib/canisters';
import type {
	AvailableCodex,
	SetupActionResult,
	SetupBackendActor,
	SetupDraftPartial,
	SetupDraftSaveResult,
	SetupFleet,
	SetupLaunchResult,
	SetupLaunchState,
	SetupState
} from './types';

const CODEX_CATALOG_POLL_MS = 2_500;
const CODEX_CATALOG_TIMEOUT_MS = 90_000;
const CODEX_INSTALL_POLL_MS = 5_000;
// Mainnet Syntropia codex installs have been observed at ~25 min.
const CODEX_INSTALL_TIMEOUT_MS = 40 * 60 * 1_000;
const RAW_INSTALL_CALL_GRACE_MS = 90_000;
const RAW_COMPLETE_CALL_GRACE_MS = 60_000;
const COMPLETE_SETUP_POLL_MS = 5_000;
const COMPLETE_SETUP_TIMEOUT_MS = 5 * 60 * 1_000;
const LAUNCH_STATUS_POLL_MS = 5_000;

function parseJson<T>(raw: unknown): T {
	if (typeof raw === 'string') return JSON.parse(raw) as T;
	return raw as T;
}

function asSetupActor(actor: unknown): SetupBackendActor {
	return actor as SetupBackendActor;
}

async function getActor(): Promise<SetupBackendActor> {
	// SetupStageGate restores portal auth before the wizard mounts; the store
	// may already hold an authenticated actor while backendReady is still
	// waiting on realmInfo.fetch() (e.g. a slow/hung status() on mainnet).
	await backendActorReady;
	const actor = get(backendStore);
	if (!actor) throw new Error('Backend actor not initialized');
	return asSetupActor(actor);
}

async function fallbackSetupState(actor: SetupBackendActor): Promise<SetupState | null> {
	if (!actor.get_runtime_flags) return null;
	try {
		const flags = parseJson<{ success?: boolean; realm_stage?: string }>(
			await actor.get_runtime_flags()
		);
		if (!flags?.success) return null;
		return {
			status: flags.realm_stage || 'alpha',
			creator: '',
			is_caller_authorized: false,
			codex: null,
			token: null,
			branding: null
		};
	} catch {
		return null;
	}
}

export async function fetchSetupState(): Promise<SetupState> {
	const actor = await getActor();
	try {
		if (typeof actor.get_setup_state === 'function') {
			return parseJson<SetupState>(await actor.get_setup_state());
		}
	} catch (error) {
		console.warn('get_setup_state failed, trying runtime flags fallback:', error);
	}
	const fallback = await fallbackSetupState(actor);
	if (fallback) return fallback;
	throw new Error('Unable to fetch realm setup state');
}

type CodexListEnvelope = {
	success?: boolean;
	codices?: AvailableCodex[];
	error?: string;
};

function unwrapCodexList(
	parsed: AvailableCodex[] | CodexListEnvelope
): AvailableCodex[] | null {
	const list = Array.isArray(parsed) ? parsed : parsed?.codices;
	if (!Array.isArray(list) || list.length === 0) return null;
	return list;
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

async function readCachedCodices(actor: SetupBackendActor): Promise<AvailableCodex[] | null> {
	if (typeof actor.get_available_codices_cached !== 'function') return null;
	try {
		const parsed = parseJson<AvailableCodex[] | CodexListEnvelope>(
			await actor.get_available_codices_cached()
		);
		return unwrapCodexList(parsed);
	} catch {
		return null;
	}
}

async function pollCachedCodices(
	actor: SetupBackendActor,
	timeoutMs = CODEX_CATALOG_TIMEOUT_MS,
	intervalMs = CODEX_CATALOG_POLL_MS
): Promise<AvailableCodex[]> {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		const list = await readCachedCodices(actor);
		if (list) return list;
		await sleep(intervalMs);
	}
	throw new Error('Codex catalog is still loading. Please wait a moment and try again.');
}

export async function listAvailableCodices(): Promise<AvailableCodex[]> {
	const actor = await getActor();

	const cached = await readCachedCodices(actor);
	if (cached) return cached;

	// The update may not settle in the browser even after the canister stores
	// the catalog. If it does settle with an error, show that instead of polling.
	const refresh = actor.list_available_codices().catch((error: unknown) => {
		throw error;
	});
	const outcome = await raceWithGrace(refresh, 20_000);
	if (outcome.settled) {
		if (outcome.error !== undefined && !isAmbiguousInstallError(outcome.error)) {
			throw outcome.error;
		}
		if (outcome.result !== undefined) {
			const parsed = parseJson<CodexListEnvelope>(outcome.result);
			if (parsed && !Array.isArray(parsed) && parsed.success === false) {
				throw new Error(parsed.error || 'Could not load the codex catalog');
			}
		}
	}

	return pollCachedCodices(actor);
}

export async function fetchFleet(): Promise<SetupFleet> {
	const actor = await getActor();
	const parsed = parseJson<{ success?: boolean; fleet?: SetupFleet; error?: string }>(
		await actor.setup_fleet('{}')
	);
	if (!parsed?.success || !parsed.fleet) {
		throw new Error(parsed?.error || 'Could not find the Realms product canisters');
	}
	return parsed.fleet;
}

export function isAmbiguousInstallError(error: unknown): boolean {
	const message = error instanceof Error ? error.message : String(error);
	return /returned undefined|cannot determine if the call was successful/i.test(message);
}

export async function pollUntilCodexInstalled(
	packageName: string,
	version: string,
	timeoutMs = CODEX_INSTALL_TIMEOUT_MS,
	intervalMs = CODEX_INSTALL_POLL_MS
): Promise<void> {
	const deadline = Date.now() + timeoutMs;
	const actor = await getActor();
	while (Date.now() < deadline) {
		const state = await fetchSetupState();
		if (state.codex?.package === packageName && (!version || state.codex?.version === version)) {
			return;
		}
		const { settled, result: raw, error: callError } = await raceWithGrace(
			actor.setup_install_codex(JSON.stringify({ package: packageName, version })),
			Math.min(intervalMs, RAW_INSTALL_CALL_GRACE_MS)
		);
		if (settled) {
			if (callError !== undefined && !isAmbiguousInstallError(callError)) {
				throw callError;
			}
			if (raw !== undefined) {
				const parsed = parseJson<SetupActionResult>(raw);
				if (!parsed.success) {
					throw new Error(parsed.error || 'Codex install failed');
				}
				if (parsed.setup_codex?.package === packageName) {
					return;
				}
			}
		}
		await sleep(intervalMs);
	}

	const finalState = await fetchSetupState();
	if (
		finalState.codex?.package === packageName &&
		finalState.codex?.version === version
	) {
		return;
	}

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
		const state = await fetchSetupState();
		if (state.status !== 'setup') {
			return;
		}
		await sleep(intervalMs);
	}
	throw new Error(
		`Setup completion timed out after ${Math.round(timeoutMs / 60_000)} minutes. Please refresh and check realm status.`
	);
}

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
	const actor = await getActor();
	let resolvedVersion = payload.version;
	const rawCallGraceMs = options?.rawCallGraceMs ?? RAW_INSTALL_CALL_GRACE_MS;

	const { settled, result: raw, error: callError } = await raceWithGrace(
		actor.setup_install_codex(JSON.stringify(payload)),
		rawCallGraceMs
	);

	if (settled) {
		if (callError !== undefined) {
			if (!isAmbiguousInstallError(callError)) {
				throw callError;
			}
		} else if (raw !== undefined) {
			const parsed = parseJson<SetupActionResult>(raw);
			if (!parsed.success) {
				return parsed;
			}
			if (parsed.resolved_version) {
				resolvedVersion = parsed.resolved_version;
			}
		}
	}

	await pollUntilCodexInstalled(
		payload.package,
		resolvedVersion,
		options?.pollTimeoutMs,
		options?.pollIntervalMs
	);

	return { success: true, resolved_version: resolvedVersion };
}

export async function configureSetupToken(payload: Record<string, unknown>): Promise<SetupActionResult> {
	const actor = await getActor();
	return parseJson<SetupActionResult>(
		await actor.setup_configure_token(JSON.stringify(payload))
	);
}

export async function applySetupDraftToken(): Promise<SetupActionResult> {
	const actor = await getActor();
	if (typeof actor.setup_apply_draft_token !== 'function') {
		return { success: false, error: 'Could not apply treasury ledger' };
	}
	try {
		return parseJson<SetupActionResult>(await actor.setup_apply_draft_token());
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
	const actor = await getActor();
	return parseJson<SetupActionResult>(
		await actor.setup_set_branding(JSON.stringify(payload))
	);
}

export async function saveSetupDraft(partial: SetupDraftPartial): Promise<SetupDraftSaveResult> {
	const actor = await getActor();
	return parseJson<SetupDraftSaveResult>(
		await actor.setup_save_draft(JSON.stringify(partial))
	);
}

export async function fetchSetupDraftAsset(kind: 'logo' | 'background'): Promise<string | null> {
	const actor = await getActor();
	const parsed = parseJson<{ success?: boolean; data_url?: string; error?: string }>(
		await actor.get_setup_draft_asset(kind)
	);
	if (!parsed.success || !parsed.data_url) {
		return null;
	}
	return parsed.data_url;
}

export async function fetchSetupLaunchStatus(): Promise<SetupLaunchState> {
	const actor = await getActor();
	const parsed = parseJson<{ success?: boolean; launch?: SetupLaunchState; error?: string }>(
		await actor.get_setup_launch_status()
	);
	if (!parsed.success || !parsed.launch) {
		throw new Error(parsed.error || 'Unable to fetch launch status');
	}
	return parsed.launch;
}

export async function startSetupLaunch(): Promise<SetupLaunchResult> {
	const actor = await getActor();
	return parseJson<SetupLaunchResult>(await actor.setup_launch());
}

export { LAUNCH_STATUS_POLL_MS };

export async function completeSetup(options?: {
	rawCallGraceMs?: number;
	pollTimeoutMs?: number;
	pollIntervalMs?: number;
}): Promise<SetupActionResult> {
	const actor = await getActor();
	const rawCallGraceMs = options?.rawCallGraceMs ?? RAW_COMPLETE_CALL_GRACE_MS;

	const { settled, result: raw, error: callError } = await raceWithGrace(
		actor.complete_setup(),
		rawCallGraceMs
	);

	if (settled) {
		if (callError !== undefined) {
			if (!isAmbiguousInstallError(callError)) {
				throw callError;
			}
		} else if (raw !== undefined) {
			const parsed = parseJson<SetupActionResult>(raw);
			if (!parsed.success) {
				return parsed;
			}
			return parsed;
		}
	}

	await pollUntilSetupComplete(options?.pollTimeoutMs, options?.pollIntervalMs);
	return { success: true };
}
