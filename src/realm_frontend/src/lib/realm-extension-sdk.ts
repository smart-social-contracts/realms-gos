/**
 * RealmExtensionContext — the shared SDK surface exposed by the host app
 * to every runtime-loaded extension bundle.
 *
 * Extension bundles receive this as the second argument to their mount()
 * function and use it instead of reaching into host internals ($lib/*, $app/*).
 *
 * The stores follow the Svelte store contract: { subscribe(cb): unsubscribe }.
 * Plain-JS bundles can call ctx.principal.subscribe(v => …) directly.
 */
import { quarter, type Reply } from '$lib/realm';

// ── Host side: extension calls go through the realm's extension_call ──

/** Run ``fn`` of ``extension``'s backend with ``args`` on the active quarter. */
export function callRealmExtension(
	extension: string,
	fn: string,
	args: Record<string, unknown> = {},
): Promise<Reply> {
	return quarter.run('extension_call', { extension, function: fn, args });
}

/** An ``extension_call`` result, parsed when the extension returned JSON text. */
export function extensionResult(reply: { [key: string]: any }): unknown {
	if (typeof reply.result !== 'string') return reply.result;
	try {
		return JSON.parse(reply.result);
	} catch {
		return reply.result;
	}
}

async function legacyExtensionCall(extension: string, fn: string, argsJson = '{}') {
	const reply = await callRealmExtension(extension, fn, JSON.parse(argsJson || '{}'));
	if (!reply.ok) return { success: false, response: reply.error };
	const result = reply.result;
	return { success: true, response: typeof result === 'string' ? result : JSON.stringify(result) };
}

/**
 * ``ctx.backend`` for in-process bundles: ``read`` / ``run`` of the active quarter, plus
 * ``extension_sync_call`` / ``extension_async_call`` with their old ``{success, response}`` reply.
 */
export const realmExtensionBackend = {
	read: quarter.read,
	run: quarter.run,
	extension_sync_call: legacyExtensionCall,
	extension_async_call: legacyExtensionCall,
};

// ── Re-exported helper types ───────────────────────────────────────────

export interface Readable<T> {
	subscribe(run: (value: T) => void): () => void;
}

export interface NotificationItem {
	id: string;
	title: string;
	message: string;
	timestamp_ms: number;
	read: boolean;
	sender?: string;
	sender_name?: string;
	audience_type?: string;
	department?: string;
	icon?: string;
	href?: string;
	color?: string;
}

export interface RealmInfo {
	name: string;
	welcomeMessage: string;
	manifesto: string;
	registries: { canister_id: string; canister_type: string }[];
	quarters: { name: string; canister_id: string; population: number; status: string }[];
	isQuarter: boolean;
	parentRealmCanisterId: string;
	logoUrl?: string;
	backgroundImageUrl?: string;
	primaryColor?: string;
	loading: boolean;
	error: string | null;
}

export interface DocumentFocusSnapshot {
	languageId: string;
	range: { startLine: number; endLine: number };
	text: string;
}

/** Passive pub/sub: what the user is currently viewing in an extension. */
export interface DocumentFocus {
	source: string;
	uri: string;
	label?: string;
	snapshot?: DocumentFocusSnapshot;
}

export type HostAction =
	| { type: 'assistant.open' }
	| { type: 'assistant.prompt'; message?: string; autoSend?: boolean }
	| { type: 'clipboard.write'; text: string }
	/** Go to the realm member home (sidebar default path). Extensions must not name the target. */
	| { type: 'navigate.home' };

export interface PendingPrompt {
	message: string;
	autoSend: boolean;
	id: number;
}

// ── Main context interface ─────────────────────────────────────────────

export interface RealmExtensionContext {
	/** The extension's ID as registered in the realm backend. */
	extensionId: string;
	/** The installed version string. */
	version: string;

	/**
	 * The realm backend of the active quarter (`realmExtensionBackend`): `read(name, args)` and
	 * `run(name, args)` call its operations and return `{ ok, ... }`; `extension_sync_call` /
	 * `extension_async_call` remain for older bundles. The market_place extension gets the
	 * marketplace adapter here instead.
	 */
	backend: any;

	/**
	 * Adapter for the shared marketplace canister (browse listings, purchases).
	 * Currently only provided to package_manager (and the deprecated
	 * market_place bundle). Null when the realm has no marketplace configured.
	 */
	marketplace?: any;

	/**
	 * Call an extension function on the backend (the realm's `extension_call`).
	 *
	 * @param fn    The function name exposed by the backend extension.
	 * @param args  A plain JS object passed as the function's keyword arguments.
	 * @returns     The function's result (parsed if it is JSON text), or throws on failure.
	 */
	callSync: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;

	/** Same as callSync; kept for bundles that call inter-canister functions this way. */
	callAsync: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;

	/** Call a realm read operation as the user; only the operations the extension's manifest declares. */
	read: (name: string, args?: Record<string, unknown>) => Promise<Reply>;

	/** Call a realm operation that changes the realm, as the user; only the operations the manifest declares. */
	run: (name: string, args?: Record<string, unknown>) => Promise<Reply>;

	/** The logged-in user's principal as a reactive store. Empty string when anonymous. */
	principal: Readable<string>;
	/** Whether the user has authenticated via Internet Identity. */
	isAuthenticated: Readable<boolean>;
	/** The user's profile tags (e.g. ["member", "admin"]). Empty while loading or anonymous. */
	userProfiles: Readable<string[]>;

	/** Realm metadata (name, logo, manifesto, quarters, etc.). */
	realmInfo: Readable<RealmInfo>;
	/** Host config. Ledger ids are not here: read `backend.read('treasury_view')`. */
	config: {
		/** @deprecated never populated; use `backend.read('treasury_view')`. */
		ckbtc_ledger_canister_id?: string;
		/** @deprecated never populated; use `backend.read('treasury_view')`. */
		ckbtc_indexer_canister_id?: string;
		/** @deprecated never populated; use `backend.read('treasury_view')`. */
		token_backend_canister_id?: string;
		/** The realm_backend canister ID itself. */
		canisterId: string;
		/** File registry canister ID: this build's, when it has one. */
		fileRegistryCanisterId?: string;
		/** Marketplace canister ID. Not reported by this realm version. */
		marketplaceCanisterId?: string;
	};

	/** Navigate to a path within the realm SPA (wraps SvelteKit's goto). */
	navigate: (path: string) => Promise<void>;

	/** Show a host modal (notice for success/info/warning, alert for error). */
	notify: (
		level: 'success' | 'info' | 'warning' | 'error',
		message: string,
	) => void;

	/** Open a host confirmation modal; resolves with the chosen action id. */
	openModal: (payload: {
		title: string;
		body: string;
		actions: { id: string; label: string; tone?: 'primary' | 'secondary' | 'danger' }[];
	}) => Promise<{ actionId: string }>;

	/** The i18n translate function as a reactive store. Usage: $t('key', { values }). */
	t: Readable<(key: string, vars?: Record<string, unknown>) => string>;
	/** The current locale code (e.g. "en", "de"). */
	locale: Readable<string | null | undefined>;

	/** Notification subsystem. */
	notifications: {
		items: Readable<NotificationItem[]>;
		unreadCount: Readable<number>;
		load: () => Promise<void>;
		markAsRead: (id: string, read?: boolean) => Promise<void>;
	};

	/** Theme utility for combining Tailwind class strings. */
	theme: {
		cn: (...classes: (string | undefined | null | false)[]) => string;
	};

	/**
	 * Crypto helpers backed by the host's bundled `@dfinity/vetkeys`, so
	 * extensions can decrypt consent-shared member data without bundling the
	 * (large) vetKeys library themselves.
	 */
	crypto: {
		/**
		 * Decrypt a payload the caller has been granted access to, given the
		 * caller's IBE-wrapped DEK directly (e.g. from a KeyEnvelope returned by
		 * an extension backend).
		 *
		 * @param wrappedDekHex  The caller's IBE-wrapped DEK.
		 * @param ciphertext     The `enc:v=2:...` ciphertext.
		 * @returns The decrypted key/value object, or null if decryption fails.
		 */
		decryptWithEnvelope: (
			wrappedDekHex: string,
			ciphertext: string,
		) => Promise<Record<string, string> | null>;

		/**
		 * Encrypt `data` with a fresh DEK and IBE-wrap it for each recipient
		 * principal (deduplicated). The shared root key is fetched once and all
		 * wraps happen locally. Returns the ciphertext to persist plus the
		 * per-recipient wrapped DEKs to pass to {@link grantScope}.
		 */
		encryptForRecipients: (
			recipients: string[],
			data: unknown,
		) => Promise<{ ciphertext: string; wrappedDeks: Record<string, string> }>;

		/**
		 * Persist access grants for `scope` (one batch grant + a diffed batch
		 * revoke). Server-side authorization depends on the scope kind
		 * (e.g. `dept:<name>:...` requires the department head or a realm admin).
		 *
		 * @returns the principals now granted access.
		 */
		grantScope: (
			scope: string,
			wrappedDeks: Record<string, string>,
			opts?: { previousRecipients?: string[]; keep?: string[] },
		) => Promise<string[]>;

		/**
		 * Decrypt a scope's payload for the current caller: fetches the DEK
		 * envelope wrapped for them, derives their sharing vetKey, and decrypts.
		 * Returns null if the caller has no access.
		 */
		decryptScope: <T = Record<string, string>>(
			scope: string,
			ciphertext: string,
		) => Promise<T | null>;
	};

	/**
	 * Generic host bridge: document focus pub/sub and cross-extension actions.
	 * Extensions publish focus; consumers (e.g. llm_chat) react without coupling.
	 */
	host: {
		focus: Readable<DocumentFocus | null>;
		setFocus: (focus: DocumentFocus | null) => void;
		dispatch: (action: HostAction) => void;
		pendingPrompt: Readable<PendingPrompt | null>;
	};

	/** Shared UI helpers provided by the host app. */
	ui: {
		AccessDenied: import('svelte').Component<{ operation?: string }>;
		/** Returns the denied operation name, or null if the error is not access-denied. */
		accessDeniedOperation: (error: unknown) => string | null;
		/** Read-only Monaco code editor (Visual Studio light theme, Python by default). */
		MonacoEditor: import('svelte').Component<{
			code?: string;
			language?: string;
			readOnly?: boolean;
			theme?: string;
		}>;
		/** Read-only Monaco diff editor (current codex vs proposed). */
		MonacoDiffEditor: import('svelte').Component<{
			original?: string;
			modified?: string;
			language?: string;
			readOnly?: boolean;
			theme?: string;
		}>;
	};
}

// ── Mount function signature ───────────────────────────────────────────

export interface MountResult {
	unmount?: () => void;
}

export type ExtensionMountFn = (
	target: HTMLElement,
	ctx: RealmExtensionContext,
) => MountResult | void | Promise<MountResult | void>;
