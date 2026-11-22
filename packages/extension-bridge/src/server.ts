import {
	BRIDGE_PROTOCOL_VERSION,
	BRIDGE_SOURCE,
	type BridgeErrorPayload,
	type HostState,
	type ExtToHostMessage,
	type HostToExtMessage,
	type TaskResultPayload,
	isBridgeMessage,
	bridgeVersionsCompatible,
} from './protocol.js';
import { validateExtToHostMessage } from './validators.js';

export interface BridgeServerOptions {
	extensionId: string;
	/** Expected sdk_version from the extension manifest. */
	requiredSdkVersion?: string;
	/** Declared manifest capabilities — closed-world enforcement. */
	capabilities: string[];
	getState: () => HostState;
	/** Call ``fn`` of the extension's own backend as the user: as a query when ``read`` (``extension_read``), else as an
	 * update (``extension_call``). The realm runs it in its sandbox, where it reaches only the operations the extension
	 * declares; the screen has no other way to the realm. */
	onCallExtension?: (fn: string, args: Record<string, unknown>, read: boolean) => Promise<unknown>;
	/** Submit an async backend call; must return a task id promptly. Completion is pushed via {@link BridgeServer.pushTaskResult}. */
	onCallExtensionAsync?: (fn: string, args: Record<string, unknown>) => Promise<{ taskId: string }>;
	onNavigate?: (path: string) => void | Promise<void>;
	onNotify?: (level: 'info' | 'success' | 'error', message: string) => void;
	onOpenModal?: (payload: {
		title: string;
		body: string;
		actions: Array<{ id: string; label: string; tone?: string }>;
	}) => Promise<{ actionId: string }>;
	onResize?: (height: number) => void;
	onHandshakeComplete?: () => void;
	onHandshakeFailed?: (reason: string) => void;
}

export interface BridgeServer {
	pushState(state: HostState): void;
	/** Push completion of an async extension call to the sandbox (one-shot). */
	pushTaskResult(taskId: string, payload: TaskResultPayload): void;
	destroy(): void;
}

/** Max serialized inbound message size (JSON.stringify). */
const MAX_INBOUND_MESSAGE_BYTES = 256 * 1024;
const MAX_CONCURRENT_CALL_EXTENSION = 10;

function deny(message: string): BridgeErrorPayload {
	return { code: 'denied', message };
}

function failed(message: string): BridgeErrorPayload {
	return { code: 'failed', message };
}

function badRequest(message: string): BridgeErrorPayload {
	return { code: 'bad_request', message };
}

function rateLimited(message: string): BridgeErrorPayload {
	return { code: 'rate_limited', message };
}

/** Per-key sliding-window rate limiter for fire-and-forget bridge ops. */
function createRateLimiter(maxEvents: number, windowMs: number) {
	const timestamps: number[] = [];
	return (): boolean => {
		const now = Date.now();
		while (timestamps.length > 0 && timestamps[0] <= now - windowMs) {
			timestamps.shift();
		}
		if (timestamps.length >= maxEvents) return false;
		timestamps.push(now);
		return true;
	};
}

export function createBridgeServer(
	iframe: HTMLIFrameElement,
	options: BridgeServerOptions,
): BridgeServer {
	const requiredSdk =
		options.requiredSdkVersion ?? BRIDGE_PROTOCOL_VERSION;
	let handshakeDone = false;
	let destroyed = false;
	let inFlightCallExtension = 0;

	const RATE_WINDOW_MS = 10_000;
	const allowNotify = createRateLimiter(10, RATE_WINDOW_MS);
	const allowNavigate = createRateLimiter(10, RATE_WINDOW_MS);
	const allowResize = createRateLimiter(30, RATE_WINDOW_MS);
	const allowCallExtension = createRateLimiter(30, RATE_WINDOW_MS);
	const allowOpenModal = createRateLimiter(5, RATE_WINDOW_MS);
	const allowGetState = createRateLimiter(30, RATE_WINDOW_MS);

	function targetWindow(): Window | null {
		return iframe.contentWindow;
	}

	function post(msg: HostToExtMessage): void {
		if (destroyed) return;
		const win = targetWindow();
		if (!win) return;
		// Sandboxed extension iframes have opaque origin `'null'`; there is no
		// concrete targetOrigin to pass. Safe here: iframe HTML is host-served,
		// the extension only sees data the host already injected, and identity
		// never enters the sandbox.
		win.postMessage(msg, '*');
	}

	function replyError(id: number, error: BridgeErrorPayload): void {
		post({ source: BRIDGE_SOURCE, kind: 'error', id, error });
	}

	function hasCapability(cap: string): boolean {
		return options.capabilities.includes(cap);
	}

	// What the backend may do is the realm's to decide: it runs it as the user, with only the operations the
	// extension declares. The bridge only paces the calls.
	function gateCallExtension(): BridgeErrorPayload | null {
		if (!handshakeDone) {
			return deny('Handshake not complete');
		}
		if (!allowCallExtension()) {
			return rateLimited('call_extension rate limit exceeded');
		}
		if (inFlightCallExtension >= MAX_CONCURRENT_CALL_EXTENSION) {
			return rateLimited('Too many concurrent call_extension requests');
		}
		return null;
	}

	async function handleRequest(msg: ExtToHostMessage): Promise<void> {
		switch (msg.kind) {
			case 'hello': {
				if (handshakeDone) return;
				if (!bridgeVersionsCompatible(msg.sdkVersion, requiredSdk)) {
					const reason = `SDK version mismatch: extension requires ${msg.sdkVersion}, host supports ${BRIDGE_PROTOCOL_VERSION}`;
					post({ source: BRIDGE_SOURCE, kind: 'hello_nack', reason });
					options.onHandshakeFailed?.(reason);
					return;
				}
				const state = options.getState();
				handshakeDone = true;
				post({
					source: BRIDGE_SOURCE,
					kind: 'hello_ack',
					sdkVersion: BRIDGE_PROTOCOL_VERSION,
					extensionId: options.extensionId,
					capabilities: options.capabilities,
					state,
				});
				options.onHandshakeComplete?.();
				return;
			}

			case 'call_extension': {
				const gateErr = gateCallExtension();
				if (gateErr) {
					replyError(msg.id, gateErr);
					return;
				}
				if (!options.onCallExtension) {
					replyError(msg.id, { code: 'unsupported', message: 'call_extension handler not configured' });
					return;
				}
				inFlightCallExtension++;
				try {
					const result = await options.onCallExtension(msg.fn, msg.args ?? {}, msg.read === true);
					post({ source: BRIDGE_SOURCE, kind: 'call_result', id: msg.id, result });
				} catch (e) {
					const message = e instanceof Error ? e.message : String(e);
					replyError(msg.id, failed(message));
				} finally {
					inFlightCallExtension--;
				}
				return;
			}

			case 'call_extension_async': {
				const gateErr = gateCallExtension();
				if (gateErr) {
					replyError(msg.id, gateErr);
					return;
				}
				if (!options.onCallExtensionAsync) {
					replyError(msg.id, {
						code: 'unsupported',
						message: 'call_extension_async handler not configured',
					});
					return;
				}
				inFlightCallExtension++;
				try {
					const { taskId } = await options.onCallExtensionAsync(msg.fn, msg.args ?? {});
					post({ source: BRIDGE_SOURCE, kind: 'call_result', id: msg.id, result: { taskId } });
				} catch (e) {
					const message = e instanceof Error ? e.message : String(e);
					replyError(msg.id, failed(message));
				} finally {
					inFlightCallExtension--;
				}
				return;
			}

			case 'navigate': {
				if (!handshakeDone) return;
				if (!hasCapability('navigate')) return;
				if (!allowNavigate()) return;
				await options.onNavigate?.(msg.path);
				return;
			}

			case 'notify': {
				if (!handshakeDone) return;
				if (!hasCapability('notify')) return;
				if (!allowNotify()) return;
				options.onNotify?.(msg.level, msg.message);
				return;
			}

			case 'open_modal': {
				if (!handshakeDone) {
					replyError(msg.id, deny('Handshake not complete'));
					return;
				}
				if (!allowOpenModal()) {
					replyError(msg.id, rateLimited('open_modal rate limit exceeded'));
					return;
				}
				if (!hasCapability('modal')) {
					replyError(msg.id, deny("Capability 'modal' not declared"));
					return;
				}
				if (!options.onOpenModal) {
					replyError(msg.id, { code: 'unsupported', message: 'open_modal handler not configured' });
					return;
				}
				try {
					const { actionId } = await options.onOpenModal({
						title: msg.title,
						body: msg.body,
						actions: msg.actions,
					});
					post({ source: BRIDGE_SOURCE, kind: 'modal_result', id: msg.id, actionId });
				} catch (e) {
					const message = e instanceof Error ? e.message : String(e);
					replyError(msg.id, failed(message));
				}
				return;
			}

			case 'resize': {
				if (!handshakeDone) return;
				if (!allowResize()) return;
				options.onResize?.(msg.height);
				return;
			}

			case 'get_state': {
				if (!handshakeDone) {
					replyError(msg.id, deny('Handshake not complete'));
					return;
				}
				if (!allowGetState()) return;
				post({
					source: BRIDGE_SOURCE,
					kind: 'state',
					state: options.getState(),
				});
				return;
			}

			case 'goodbye':
				return;
		}
	}

	const messageHandler = (event: MessageEvent) => {
		if (destroyed) return;
		if (event.source !== iframe.contentWindow) return;

		let serialized: string;
		try {
			serialized = JSON.stringify(event.data);
		} catch {
			return;
		}
		if (serialized.length > MAX_INBOUND_MESSAGE_BYTES) return;

		if (!isBridgeMessage(event.data)) return;

		const validated = validateExtToHostMessage(event.data);
		if (!validated.ok) {
			if (validated.responseId !== undefined) {
				replyError(validated.responseId, badRequest(validated.reason));
			}
			return;
		}

		void handleRequest(validated.message);
	};

	window.addEventListener('message', messageHandler);

	return {
		pushState(state: HostState): void {
			if (!handshakeDone || destroyed) return;
			post({ source: BRIDGE_SOURCE, kind: 'state', state });
		},
		pushTaskResult(taskId: string, payload: TaskResultPayload): void {
			if (destroyed || !handshakeDone) return;
			if (typeof taskId !== 'string' || !taskId) return;
			post({
				source: BRIDGE_SOURCE,
				kind: 'task_result',
				taskId,
				status: payload.status,
				result: payload.result,
				error: payload.error,
			});
		},
		destroy(): void {
			destroyed = true;
			window.removeEventListener('message', messageHandler);
		},
	};
}
