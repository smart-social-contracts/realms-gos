export type SetupLifecycleStatus = 'setup' | 'alpha' | string;

export type SetupDraftStep = 'welcome' | 'codex' | 'token' | 'branding' | 'languages' | 'review';

export interface SetupCodexSelection {
	package: string;
	version: string;
	params?: Record<string, unknown>;
}

export interface SetupIdentity {
	manifesto?: string;
	welcome_message?: string;
	languages?: string[];
	primary_language?: string;
}

export interface SetupLanguages {
	languages?: string[];
	primary_language?: string;
}

export interface SetupDraftToken {
	token_canister_id?: string;
	symbol?: string;
	id?: string;
	existing?: string;
	decimals?: number;
	indexer_canister_id?: string;
	token_type?: string;
}

export type SetupDraftTokenValue = SetupDraftToken | string | null;

export interface SetupDraftBranding {
	logo?: boolean;
	logo_size?: number;
	background?: boolean;
	background_size?: number;
	colors?: { primary?: string };
}

export interface SetupDraft {
	step?: SetupDraftStep;
	codex?: SetupCodexSelection;
	token?: SetupDraftTokenValue;
	branding?: SetupDraftBranding | null;
	identity?: SetupIdentity | null;
	languages?: SetupLanguages | null;
}

export type SetupLaunchStatus = 'idle' | 'running' | 'failed' | 'completed';
export type SetupLaunchStepStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface SetupLaunchStep {
	name: string;
	status: SetupLaunchStepStatus;
	error: string | null;
}

export interface SetupLaunchState {
	status: SetupLaunchStatus;
	phase: string | null;
	steps: SetupLaunchStep[];
	updated_at: string | null;
}

export interface SetupState {
	status: SetupLifecycleStatus;
	creator: string;
	is_caller_authorized: boolean;
	codex: SetupCodexSelection | null;
	token: Record<string, unknown> | null;
	branding: Record<string, unknown> | null;
	identity?: SetupIdentity | null;
	languages?: string[];
	primary_language?: string;
	draft?: SetupDraft | null;
	launch?: SetupLaunchState;
	realm_name?: string;
	realm_manifesto?: string;
	realm_welcome_message?: string;
	realm_token_canister_id?: string | null;
	/** Logical GaaS environment (`staging`, `production`, …), not the IC network. */
	gos_environment?: string | null;
	/** Product orchestra ids the realm pinned after discovering them through Casals. */
	fleet?: SetupFleet | null;
	/** {symbol: {ledger, indexer, decimals}} — the shared ledgers this realm may adopt (from casals.json via the installer). */
	shared_tokens?: Record<string, { ledger: string; indexer?: string; decimals?: number; name?: string }>;
}

export interface SetupFleet {
	conductor?: string;
	file_registry?: string;
	token_ledger?: string;
}

export interface AvailableCodex {
	id: string;
	versions: string[];
	name?: string;
	description?: string;
	repository?: string;
}

export interface SetupActionResult {
	success: boolean;
	error?: string;
	resolved_version?: string;
	status?: string;
	setup_codex?: { package?: string; version?: string } | null;
	token?: { token_canister_id?: string; symbol?: string } | null;
}

export interface SetupDraftSaveBranding {
	logo_data_url?: string;
	background_data_url?: string;
	colors?: { primary?: string };
}

export type SetupDraftPartial = {
	step?: SetupDraftStep;
	codex?: SetupCodexSelection | null;
	token?: SetupDraftTokenValue;
	branding?: SetupDraftSaveBranding | null;
	identity?: SetupIdentity | null;
	languages?: SetupLanguages | null;
};

export interface SetupDraftSaveResult {
	success: boolean;
	draft?: SetupDraft;
	error?: string;
}

export interface SetupLaunchResult {
	success: boolean;
	launch?: SetupLaunchState;
	error?: string;
}

export interface SetupBackendActor {
	get_setup_state: () => Promise<string>;
	get_available_codices_cached?: () => Promise<string>;
	list_available_codices: () => Promise<string>;
	setup_fleet: (json: string) => Promise<string>;
	setup_install_codex: (json: string) => Promise<string>;
	setup_configure_token: (json: string) => Promise<string>;
	setup_set_branding: (json: string) => Promise<string>;
	complete_setup: () => Promise<string>;
	setup_save_draft: (json: string) => Promise<string>;
	setup_apply_draft_token: () => Promise<string>;
	get_setup_draft_asset: (kind: string) => Promise<string>;
	get_setup_launch_status: () => Promise<string>;
	setup_launch: () => Promise<string>;
	get_runtime_flags?: () => Promise<string>;
}
