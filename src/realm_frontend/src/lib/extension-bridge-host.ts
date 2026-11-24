/**
 * Host-side bridge service for sandboxed extensions.
 *
 * Wraps createBridgeServer, wires callbacks to real host capabilities, and
 * pushes principal/locale/theme/realmInfo via injected state providers.
 */
import {
	createBridgeServer,
	BRIDGE_PROTOCOL_VERSION,
	type BridgeServer,
	type HostState,
	type TaskResultPayload,
	type VaultSealed,
	type FileSaved,
} from '@realmsgos/extension-bridge';
import {
	requestBridgeModal,
	showBridgeNotify,
	type BridgeModalAction,
} from '$lib/stores/bridge-modal';
import { parseAccessError, AccessDeniedError } from '$lib/utils/errors';

const NAVIGATE_SCHEME_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

function checkNavigatePathSegment(path: string): boolean {
	if (!path.startsWith('/')) return false;
	if (path.startsWith('//')) return false;
	if (path.includes('\\')) return false;
	if (NAVIGATE_SCHEME_RE.test(path)) return false;
	return true;
}

/** Host-side belt for in-app navigate requests from sandboxed extensions. */
export function isValidNavigatePath(path: string): boolean {
	if (!checkNavigatePathSegment(path)) return false;
	try {
		const decoded = decodeURIComponent(path);
		if (decoded !== path && !checkNavigatePathSegment(decoded)) return false;
	} catch {
		return false;
	}
	return true;
}

export interface SandboxManifest {
	sdk_version?: string;
	capabilities?: string[];
}

export interface SandboxBridgeDeps {
	extensionId: string;
	manifest: SandboxManifest;
	callSync: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;
	/** Same backend path as in-process `ctx.callAsync` (the realm's extension_call). */
	callAsync: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;
	/** A function of the backend as a query (the realm's extension_read): free, and it can only read. */
	callRead: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;
	navigate: (path: string) => Promise<void>;
	/** Capability ``vault``: the host's own vetKeys crypto (what an in-page screen has as ``ctx.crypto``). The member's
	 * vetKey stays in the host; the frame gets back what it sealed or asked to open. */
	vault?: {
		seal: (recipients: string[], data: unknown) => Promise<VaultSealed>;
		open: (wrappedKey: string, ciphertext: string) => Promise<unknown>;
	};
	/** Capability ``download``: hands the bytes to the browser to save. Defaults to a download link in the host page. */
	deliverFile?: (name: string, mimeType: string, bytes: Uint8Array) => void;
	getHostState: () => HostState;
	subscribeHostState: (onChange: (state: HostState) => void) => () => void;
	onHandshakeFailed?: (reason: string) => void;
	onHandshakeComplete?: () => void;
}

let nextBridgeTaskSeq = 0;

/** The backend function the host asks before a file leaves (capability ``download``). */
export const DOWNLOAD_ALLOWED_FN = 'download_allowed';

function deniedError(message: string): Error {
	const err = new Error(message);
	(err as Error & { code: string }).code = 'denied';
	return err;
}

function bytesOf(content: string, base64: boolean): Uint8Array {
	if (!base64) return new TextEncoder().encode(content);
	const raw = atob(content);
	const bytes = new Uint8Array(raw.length);
	for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
	return bytes;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
	const digest = await crypto.subtle.digest('SHA-256', bytes);
	return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** The browser saves ``bytes`` as ``name``: a link in the host page, clicked once. */
export function deliverInPage(name: string, mimeType: string, bytes: Uint8Array): void {
	const url = URL.createObjectURL(new Blob([bytes], { type: mimeType }));
	const link = document.createElement('a');
	link.href = url;
	link.download = name;
	link.rel = 'noopener';
	link.style.display = 'none';
	document.body.appendChild(link);
	link.click();
	link.remove();
	setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** A file a sandboxed screen hands the member (capability ``download``). The realm decides whether it may leave: the
 * host asks the extension's own backend, as a query, ``download_allowed(name, sha256, size)`` over the bytes it is about
 * to save, and saves them only on ``{ ok: true, allowed: true }``. A backend that records files before they leave (the
 * hash of a payment file committed in the realm) answers from the realm's records, so the frame cannot hand out a file
 * the realm has not recorded. */
export async function saveSandboxFile(
	deps: Pick<SandboxBridgeDeps, 'callRead' | 'deliverFile'>,
	file: { name: string; mimeType: string; content: string; base64: boolean },
): Promise<FileSaved> {
	const bytes = bytesOf(file.content, file.base64);
	const sha256 = await sha256Hex(bytes);
	let verdict: unknown;
	try {
		verdict = await deps.callRead(DOWNLOAD_ALLOWED_FN, { name: file.name, sha256, size: bytes.length });
	} catch (e) {
		throw deniedError(`the extension's backend did not allow ${file.name}: ${e instanceof Error ? e.message : String(e)}`);
	}
	const answer = (verdict ?? {}) as { ok?: boolean; allowed?: boolean; reason?: string; error?: string };
	if (answer.ok !== true || answer.allowed !== true) {
		throw deniedError(`the extension's backend did not allow ${file.name}: ${answer.reason ?? answer.error ?? 'no reason given'}`);
	}
	(deps.deliverFile ?? deliverInPage)(file.name, file.mimeType, bytes);
	return { sha256, size: bytes.length };
}

function formatCallExtensionError(e: unknown): Error {
	if (e instanceof AccessDeniedError) return e;
	const denied = parseAccessError(e);
	if (denied) {
		const err = new Error(denied.operation);
		(err as Error & { code: string }).code = 'denied';
		return err;
	}
	return e instanceof Error ? e : new Error(String(e));
}

export class SandboxBridgeService {
	private server: BridgeServer;
	private unsubState: () => void;
	private destroyed = false;
	private inFlightAsyncTasks = new Set<string>();
	readonly ready: Promise<void>;

	constructor(iframe: HTMLIFrameElement, deps: SandboxBridgeDeps) {
		let resolveReady!: () => void;
		let rejectReady!: (err: Error) => void;
		this.ready = new Promise((resolve, reject) => {
			resolveReady = resolve;
			rejectReady = reject;
		});

		const capabilities = deps.manifest.capabilities ?? [];
		const serverRef: { current?: BridgeServer } = {};

		const runAsyncExtensionCall = (taskId: string, fn: string, args: Record<string, unknown>) => {
			this.inFlightAsyncTasks.add(taskId);
			void (async () => {
				let payload: TaskResultPayload;
				try {
					const result = await deps.callAsync(fn, args);
					payload = { status: 'completed', result };
				} catch (e) {
					const err = formatCallExtensionError(e);
					payload = { status: 'failed', error: err.message };
				}
				this.inFlightAsyncTasks.delete(taskId);
				if (this.destroyed) return;
				serverRef.current?.pushTaskResult(taskId, payload);
			})();
		};

		this.server = createBridgeServer(iframe, {
			extensionId: deps.extensionId,
			requiredSdkVersion: deps.manifest.sdk_version ?? BRIDGE_PROTOCOL_VERSION,
			capabilities,
			getState: deps.getHostState,
			onHandshakeComplete: () => {
				deps.onHandshakeComplete?.();
				resolveReady();
			},
			onHandshakeFailed: (reason) => {
				deps.onHandshakeFailed?.(reason);
				rejectReady(new Error(reason));
			},
			onCallExtension: async (fn, args, read) => {
				try {
					return await (read ? deps.callRead(fn, args) : deps.callSync(fn, args));
				} catch (e) {
					throw formatCallExtensionError(e);
				}
			},
			onCallExtensionAsync: async (fn, args) => {
				const taskId = `${deps.extensionId}-${++nextBridgeTaskSeq}-${Date.now()}`;
				runAsyncExtensionCall(taskId, fn, args ?? {});
				return { taskId };
			},
			onNavigate: (path) => {
				if (!isValidNavigatePath(path)) {
					console.warn('[extension-bridge-host] Invalid navigate path dropped:', path);
					return;
				}
				return deps.navigate(path);
			},
			onNotify: (level, message) => {
				void showBridgeNotify(level, message);
			},
			onOpenModal: (payload) =>
				requestBridgeModal({
					title: payload.title,
					body: payload.body,
					actions: payload.actions.map(
						(a): BridgeModalAction => ({
							id: a.id,
							label: a.label,
							tone: a.tone as BridgeModalAction['tone'],
						}),
					),
				}),
			onVaultSeal: deps.vault ? (recipients, data) => deps.vault!.seal(recipients, data) : undefined,
			onVaultOpen: deps.vault ? (wrappedKey, ciphertext) => deps.vault!.open(wrappedKey, ciphertext) : undefined,
			onSaveFile: (file) => saveSandboxFile(deps, file),
			onResize: (height) => {
				const MIN_HEIGHT = 100;
				const MAX_HEIGHT = 4000;
				if (
					Number.isFinite(height) &&
					height >= MIN_HEIGHT &&
					height <= MAX_HEIGHT
				) {
					iframe.style.height = `${Math.ceil(height)}px`;
					iframe.style.width = '100%';
					iframe.style.border = 'none';
					iframe.style.display = 'block';
				}
			},
		});
		serverRef.current = this.server;

		this.unsubState = deps.subscribeHostState((state) => {
			this.server.pushState(state);
		});
	}

	destroy(): void {
		this.destroyed = true;
		this.inFlightAsyncTasks.clear();
		this.unsubState();
		this.server.destroy();
	}
}
