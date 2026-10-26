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
	entry_access?: { functions?: Record<string, string> };
}

export interface SandboxBridgeDeps {
	extensionId: string;
	manifest: SandboxManifest;
	callSync: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;
	/** Same backend path as in-process `ctx.callAsync` (the realm's extension_call). */
	callAsync: (fn: string, args?: Record<string, unknown>) => Promise<unknown>;
	navigate: (path: string) => Promise<void>;
	getHostState: () => HostState;
	subscribeHostState: (onChange: (state: HostState) => void) => () => void;
	onHandshakeFailed?: (reason: string) => void;
	onHandshakeComplete?: () => void;
}

let nextBridgeTaskSeq = 0;

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
			entryAccessFunctions: deps.manifest.entry_access?.functions,
			getState: deps.getHostState,
			onHandshakeComplete: () => {
				deps.onHandshakeComplete?.();
				resolveReady();
			},
			onHandshakeFailed: (reason) => {
				deps.onHandshakeFailed?.(reason);
				rejectReady(new Error(reason));
			},
			onCallExtension: async (fn, args) => {
				try {
					return await deps.callSync(fn, args);
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
