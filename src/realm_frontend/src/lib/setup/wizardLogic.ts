import type { SetupState } from './types';
import { configureTokenPayload, type CatalogTokenDraftInput } from './sharedTokens';

export type WizardStep = 'welcome' | 'codex' | 'token' | 'branding' | 'languages' | 'review';

export type WizardStepUrlToken = 'welcome' | 'codex' | 'token' | 'branding' | 'languages' | 'launch';

export const WIZARD_STEPS: WizardStep[] = [
	'welcome',
	'codex',
	'token',
	'branding',
	'languages',
	'review'
];

export const WIZARD_STEP_URL_TOKENS: WizardStepUrlToken[] = [
	'welcome',
	'codex',
	'token',
	'branding',
	'languages',
	'launch'
];

/** Shared / unfinished packages that must not appear in the setup catalog. */
export const HIDDEN_SETUP_CODICES = new Set(['common', 'westminster', '_common']);

export function isSetupCatalogCodex(id: string): boolean {
	return Boolean(id.trim()) && !HIDDEN_SETUP_CODICES.has(id.trim());
}

export const LAUNCH_PHASES = [
	{ name: 'install_extensions', label: 'Install extensions' },
	{ name: 'install_codex', label: 'Install codex' },
	{ name: 'configure_token', label: 'Configure token' },
	{ name: 'upload_branding', label: 'Upload branding' },
	{ name: 'apply_identity', label: 'Apply identity' },
	{ name: 'complete', label: 'Complete setup' }
] as const;

export function firstFailedLaunchStepError(
	launch?: {
		status?: string;
		steps?: Array<{ status?: string; error?: string | null }>;
	} | null
): string {
	if (!launch || launch.status !== 'failed' || !Array.isArray(launch.steps)) {
		return '';
	}
	const failed = launch.steps.find((step) => step.status === 'failed' && step.error);
	return String(failed?.error || '');
}

export function stepToUrlToken(step: WizardStep): WizardStepUrlToken {
	if (step === 'review') return 'launch';
	return step;
}

export function urlTokenToStep(token: string): WizardStep | null {
	const trimmed = token.trim();
	if (!trimmed || !(WIZARD_STEP_URL_TOKENS as string[]).includes(trimmed)) {
		return null;
	}
	if (trimmed === 'launch') return 'review';
	return trimmed as WizardStep;
}

const CODEX_ADVANCE_ERROR = 'Choose a codex before continuing to later steps';

/** True when backend setup state already has a codex installed. */
export function isCodexInstalled(state: SetupState | null | undefined): boolean {
	return Boolean(state?.codex?.package && state?.codex?.version);
}

/** True when the draft (or legacy installed codex) has a codex chosen. */
export function isCodexChosen(state: SetupState | null | undefined): boolean {
	const draftCodex = state?.draft?.codex;
	if (draftCodex?.package?.trim() && draftCodex?.version?.trim()) {
		return true;
	}
	return isCodexInstalled(state);
}

/**
 * Resolve the version to send for draft save / continue checks.
 * Prefer explicit UI selection, then draft, then installed backend version.
 */
export function resolveSelectedCodexVersion(
	selectedVersion: string,
	setupState: SetupState | null | undefined
): string {
	const trimmed = selectedVersion.trim();
	if (trimmed) return trimmed;
	const draftVersion = setupState?.draft?.codex?.version?.trim();
	if (draftVersion) return draftVersion;
	return setupState?.codex?.version?.trim() ?? '';
}

/**
 * Whether the codex step can advance without saving again.
 * Uses draft choice first, then legacy installed codex.
 */
export function canAdvanceFromCodexStep(setupState: SetupState | null | undefined): boolean {
	return isCodexChosen(setupState);
}

/**
 * Pick a default version for the codex picker without clobbering a saved version.
 */
export function reconcileCodexVersion(
	codexVersions: string[],
	selectedVersion: string,
	savedVersion: string | undefined,
	latestVersion: (versions: string[]) => string
): string {
	const saved = savedVersion?.trim() ?? '';
	if (saved) {
		return saved;
	}
	if (selectedVersion && codexVersions.includes(selectedVersion)) {
		return selectedVersion;
	}
	return latestVersion(codexVersions);
}

export function getPreviousWizardStep(current: WizardStep): WizardStep | null {
	const index = WIZARD_STEPS.indexOf(current);
	if (index <= 0) return null;
	return WIZARD_STEPS[index - 1] ?? null;
}

export function getNextWizardStep(current: WizardStep): WizardStep | null {
	const index = WIZARD_STEPS.indexOf(current);
	if (index < 0 || index >= WIZARD_STEPS.length - 1) return null;
	return WIZARD_STEPS[index + 1] ?? null;
}

/** Welcome always advances to codex without a backend call. */
export function getWelcomeAdvanceStep(): WizardStep {
	return 'codex';
}

export function canAdvanceFromWelcomeStep(): boolean {
	return true;
}

export function getCodexStepPrimaryLabel(_setupState: SetupState | null | undefined, busy: boolean): string {
	if (busy) return 'Continuing…';
	return 'Continue';
}

export function isCodexPrimaryActionDisabled(
	busy: boolean,
	selectedCodexId: string,
	selectedVersion: string,
	setupState: SetupState | null | undefined
): boolean {
	if (busy) return true;
	if (canAdvanceFromCodexStep(setupState)) return false;
	return !selectedCodexId.trim() || !selectedVersion.trim();
}

export interface WizardStepNavigationResult {
	allowed: boolean;
	showError?: boolean;
	errorMessage?: string;
}

/** True when launch has started and the founder may still edit earlier steps. */
export function isFailedOrRunningLaunch(setupState: SetupState | null | undefined): boolean {
	const status = setupState?.launch?.status;
	return status === 'running' || status === 'failed';
}

/**
 * Stepper / programmatic navigation rules. Back navigation never mutates backend state.
 * The codex banner only appears when attempting to skip ahead from step 1.
 */
export function canNavigateToWizardStep(
	from: WizardStep,
	to: WizardStep,
	setupState: SetupState | null | undefined
): WizardStepNavigationResult {
	if (from === to) {
		return { allowed: true };
	}

	const fromIndex = WIZARD_STEPS.indexOf(from);
	const toIndex = WIZARD_STEPS.indexOf(to);
	if (fromIndex < 0 || toIndex < 0) {
		return { allowed: false };
	}

	if (toIndex <= fromIndex) {
		return { allowed: true };
	}

	if (from === 'welcome' && to === 'codex') {
		return { allowed: true };
	}

	// A failed/running launch must not trap the founder on review. Codex was
	// already required to start launch, so they may return to Token (or any
	// earlier step) to persist a ledger and retry.
	if (isFailedOrRunningLaunch(setupState)) {
		return { allowed: true };
	}

	if (!canAdvanceFromCodexStep(setupState)) {
		return {
			allowed: false,
			showError: from === 'codex',
			errorMessage: CODEX_ADVANCE_ERROR
		};
	}

	return { allowed: true };
}

export function shouldClearCodexAdvanceError(currentStep: WizardStep, error: string): boolean {
	return currentStep === 'codex' && error === CODEX_ADVANCE_ERROR;
}

export function resolveInitialWizardStep(
	setupState: SetupState,
	urlStepToken: string | null,
	canNavigate: typeof canNavigateToWizardStep = canNavigateToWizardStep
): WizardStep {
	if (urlStepToken) {
		const step = urlTokenToStep(urlStepToken);
		if (step) {
			if (canNavigate('welcome', step, setupState).allowed) {
				return step;
			}
			// After a failed/running launch the founder may deep-link back to
			// Token (or any earlier step) even if the first-run welcome gate
			// would still block skip-ahead.
			if (isFailedOrRunningLaunch(setupState)) {
				return step;
			}
		}
	}

	if (isFailedOrRunningLaunch(setupState)) {
		return 'review';
	}

	const draftStep = setupState.draft?.step;
	if (draftStep && draftStep !== 'welcome') {
		if (canNavigate('welcome', draftStep, setupState).allowed) {
			return draftStep;
		}
	}

	return 'welcome';
}

function tokenSymbolFromStoredValue(token: unknown): string {
	if (token == null) return '';
	if (typeof token === 'string') return token.trim();
	if (typeof token !== 'object') return '';
	const rec = token as { symbol?: unknown; id?: unknown; existing?: unknown };
	for (const key of ['symbol', 'id', 'existing'] as const) {
		const value = rec[key];
		if (typeof value === 'string' && value.trim()) return value.trim();
	}
	return '';
}

/**
 * Token label for Launch review. Uses persisted draft / applied token only,
 * so a skipped token stays skipped.
 * Accepts {symbol}, {id}, {existing}, or a bare symbol string.
 */
export function resolveReviewTokenSymbol(setupState: SetupState | null | undefined): string {
	const draftToken = setupState?.draft?.token;
	if (draftToken === null) {
		return '';
	}
	const fromDraft = tokenSymbolFromStoredValue(draftToken);
	if (fromDraft) return fromDraft;
	return tokenSymbolFromStoredValue(setupState?.token);
}

/**
 * Founder-auth ``setup_configure_token`` payload from persisted draft / applied
 * token. Token Continue and Launch start must call this so realm.token_canister_id
 * is written now — do not wait for the canister-identity launch tick.
 * An explicit skipped token (null) stays fail-closed.
 */
export function founderConfigureTokenFromSetupState(
	setupState: SetupState | null | undefined
): Record<string, string | number> | null {
	if (setupState?.draft?.token === null) return null;
	const token = (setupState?.draft?.token ?? setupState?.token ?? null) as CatalogTokenDraftInput;
	return configureTokenPayload(token, setupState?.token_catalog ?? []);
}

export function persistedRealmLedger(setupState: SetupState | null | undefined): string {
	const fromRealm = String(setupState?.realm_token_canister_id || '').trim();
	if (fromRealm) return fromRealm;
	const applied = setupState?.token;
	if (applied && typeof applied === 'object') {
		return String(
			(applied as { token_canister_id?: unknown }).token_canister_id || ''
		).trim();
	}
	return '';
}

/** True only when apply both claimed success and the ledger is on realm/setup.token. */
export function applyDraftTokenDidPersist(
	expectedLedger: string,
	applyResult: { success?: boolean; token?: { token_canister_id?: string } | null } | null,
	setupState?: SetupState | null
): boolean {
	const expected = (expectedLedger || '').trim();
	if (!expected) return false;
	if (!applyResult?.success) return false;
	const fromApply = String(applyResult.token?.token_canister_id || '').trim();
	if (fromApply !== expected) return false;
	return persistedRealmLedger(setupState) === expected;
}
