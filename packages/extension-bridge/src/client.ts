import {
	BRIDGE_PROTOCOL_VERSION,
	BRIDGE_SOURCE,
	type BridgeErrorPayload,
	type HostState,
	type HostToExtMessage,
	type ModalAction,
	type NotifyLevel,
	isBridgeMessage,
} from './protocol.js';

export interface OpenModalOptions {
	title: string;
	body: string;
	actions: ModalAction[];
}

/** A realm operation's reply. */
export type RealmReply = { ok: true; [key: string]: unknown } | { ok: false; error: string };

export interface CallExtensionAsyncOptions {
	/** Max wait for task_result after taskId is assigned. Default 60_000 ms. */
	timeoutMs?: number;
}

export interface ExtensionClient {
	extensionId: string;
	capabilities: string[];
	callExtension<T = unknown>(fn: string, args?: Record<string, unknown>): Promise<T>;
	/** Call a realm read operation as the user; resolves to its reply (``{ ok, ... }``). */
	read<T = RealmReply>(name: string, args?: Record<string, unknown>): Promise<T>;
	/** Call a realm operation that changes the realm, as the user; resolves to its reply (``{ ok, ... }``). */
	run<T = RealmReply>(name: string, args?: Record<string, unknown>): Promise<T>;
	callExtensionAsync<T = unknown>(
		fn: string,
		args?: Record<string, unknown>,
		options?: CallExtensionAsyncOptions,
	): Promise<T>;
	navigate(path: string): void;
	notify(level: NotifyLevel, message: string): void;
	openModal(options: OpenModalOptions): Promise<{ actionId: string }>;
	onStateChange(listener: (state: HostState) => void): () => void;
	reportHeight(height: number): void;
	destroy(): void;
}

export interface ExtensionClientOptions {
	/** Bridge protocol version the extension was built for. Defaults to BRIDGE_PROTOCOL_VERSION. */
	sdkVersion?: string;
	/** Override postMessage target (defaults to window.parent). */
	target?: Window;
}

type PendingRequest = {
	resolve: (value: unknown) => void;
	reject: (error: Error) => void;
};

type PendingTask = {
	resolve: (value: unknown) => void;
	reject: (error: Error) => void;
	timer: ReturnType<typeof setTimeout>;
};

const HANDSHAKE_TIMEOUT_MS = 30_000;
const DEFAULT_CALL_EXTENSION_ASYNC_TIMEOUT_MS = 60_000;

function bridgeError(err: BridgeErrorPayload): Error {
	const e = new Error(err.message);
	(e as Error & { code: string }).code = err.code;
	return e;
}

function applyHostCssVariables(state: HostState): void {
	if (typeof document === 'undefined') return;
	const vars = state.cssVariables;
	if (!vars) return;
	const root = document.documentElement;
	for (const [key, value] of Object.entries(vars)) {
		if (key.startsWith('--') && typeof value === 'string') {
			root.style.setProperty(key, value);
		}
	}
}

/**
 * Create an extension-side bridge client and wait for the host handshake.
 */
export async function createExtensionClient(
	options: ExtensionClientOptions = {},
): Promise<ExtensionClient> {
	if (typeof window === 'undefined') {
		throw new Error('createExtensionClient requires a browser environment');
	}

	const sdkVersion = options.sdkVersion ?? BRIDGE_PROTOCOL_VERSION;
	const target = options.target ?? window.parent;

	let nextId = 1;
	let handshakeComplete = false;
	let destroyed = false;
	const pending = new Map<number, PendingRequest>();
	const pendingTasks = new Map<string, PendingTask>();
	const queuedOutgoing: Record<string, unknown>[] = [];
	const stateListeners = new Set<(state: HostState) => void>();
	const cleanupFns: Array<() => void> = [];

	let extensionId = '';
	let capabilities: string[] = [];
	let latestState: HostState | null = null;

	let resolveHandshake!: () => void;
	let rejectHandshake!: (err: Error) => void;
	const handshake = new Promise<void>((resolve, reject) => {
		resolveHandshake = resolve;
		rejectHandshake = reject;
	});

	const handshakeTimer = setTimeout(() => {
		rejectHandshake(new Error('Bridge handshake timed out'));
	}, HANDSHAKE_TIMEOUT_MS);
	cleanupFns.push(() => clearTimeout(handshakeTimer));

	function post(msg: Record<string, unknown>): void {
		if (destroyed) return;
		target.postMessage({ source: BRIDGE_SOURCE, ...msg }, '*');
	}

	function flushQueue(): void {
		while (queuedOutgoing.length > 0) {
			post(queuedOutgoing.shift()!);
		}
	}

	function sendOrQueue(msg: Record<string, unknown>): void {
		if (handshakeComplete) {
			post(msg);
		} else {
			queuedOutgoing.push(msg);
		}
	}

	function sendRequest<T>(msg: Record<string, unknown>): Promise<T> {
		const id = nextId++;
		return new Promise<T>((resolve, reject) => {
			pending.set(id, {
				resolve: (v) => resolve(v as T),
				reject,
			});
			sendOrQueue({ ...msg, id });
		});
	}

	const messageHandler = (event: MessageEvent) => {
		// Ignore messages not from the embedding host (bundle reuse outside intended host).
		if (event.source !== window.parent) return;
		if (!isBridgeMessage(event.data)) return;
		const msg = event.data as HostToExtMessage;

		if (!handshakeComplete && msg.kind !== 'hello_ack' && msg.kind !== 'hello_nack') {
			return;
		}

		if (msg.kind === 'hello_ack') {
			extensionId = msg.extensionId;
			capabilities = msg.capabilities;
			latestState = msg.state;
			handshakeComplete = true;
			clearTimeout(handshakeTimer);
			applyHostCssVariables(msg.state);
			for (const listener of stateListeners) {
				listener(msg.state);
			}
			flushQueue();
			resolveHandshake();
			return;
		}

		if (msg.kind === 'hello_nack') {
			clearTimeout(handshakeTimer);
			rejectHandshake(new Error(msg.reason));
			return;
		}

		if (msg.kind === 'state') {
			latestState = msg.state;
			applyHostCssVariables(msg.state);
			for (const listener of stateListeners) {
				listener(msg.state);
			}
			return;
		}

		if (msg.kind === 'call_result') {
			const req = pending.get(msg.id);
			if (req) {
				pending.delete(msg.id);
				req.resolve(msg.result);
			}
			return;
		}

		if (msg.kind === 'error') {
			const req = pending.get(msg.id);
			if (req) {
				pending.delete(msg.id);
				req.reject(bridgeError(msg.error));
			}
			return;
		}

		if (msg.kind === 'modal_result') {
			const req = pending.get(msg.id);
			if (req) {
				pending.delete(msg.id);
				req.resolve({ actionId: msg.actionId });
			}
			return;
		}

		if (msg.kind === 'task_result') {
			const req = pendingTasks.get(msg.taskId);
			if (!req) return;
			pendingTasks.delete(msg.taskId);
			clearTimeout(req.timer);
			if (msg.status === 'completed') {
				req.resolve(msg.result);
			} else {
				const err = new Error(msg.error ?? 'call_extension_async failed');
				(err as Error & { code: string }).code = 'failed';
				req.reject(err);
			}
		}
	};

	window.addEventListener('message', messageHandler);
	cleanupFns.push(() => window.removeEventListener('message', messageHandler));

	post({ kind: 'hello', sdkVersion });
	await handshake;

	const client: ExtensionClient = {
		get extensionId() {
			return extensionId;
		},
		get capabilities() {
			return capabilities;
		},
		callExtension<T>(fn: string, args: Record<string, unknown> = {}): Promise<T> {
			return sendRequest<T>({ kind: 'call_extension', fn, args });
		},
		read<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
			return sendRequest<T>({ kind: 'call_operation', name, args, read: true });
		},
		run<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
			return sendRequest<T>({ kind: 'call_operation', name, args, read: false });
		},
		async callExtensionAsync<T>(
			fn: string,
			args: Record<string, unknown> = {},
			options: CallExtensionAsyncOptions = {},
		): Promise<T> {
			const timeoutMs = options.timeoutMs ?? DEFAULT_CALL_EXTENSION_ASYNC_TIMEOUT_MS;
			const ack = await sendRequest<{ taskId: string }>({
				kind: 'call_extension_async',
				fn,
				args,
			});
			const taskId = ack?.taskId;
			if (typeof taskId !== 'string' || !taskId) {
				throw new Error('call_extension_async: host did not return taskId');
			}
			return new Promise<T>((resolve, reject) => {
				const timer = setTimeout(() => {
					pendingTasks.delete(taskId);
					reject(new Error(`call_extension_async timed out after ${timeoutMs}ms`));
				}, timeoutMs);
				pendingTasks.set(taskId, {
					resolve: (v) => resolve(v as T),
					reject,
					timer,
				});
			});
		},
		navigate(path: string): void {
			sendOrQueue({ kind: 'navigate', id: nextId++, path });
		},
		notify(level: NotifyLevel, message: string): void {
			sendOrQueue({ kind: 'notify', id: nextId++, level, message });
		},
		openModal(options: OpenModalOptions): Promise<{ actionId: string }> {
			return sendRequest<{ actionId: string }>({
				kind: 'open_modal',
				title: options.title,
				body: options.body,
				actions: options.actions,
			});
		},
		onStateChange(listener: (state: HostState) => void): () => void {
			stateListeners.add(listener);
			if (latestState) {
				listener(latestState);
			}
			return () => stateListeners.delete(listener);
		},
		reportHeight(height: number): void {
			sendOrQueue({ kind: 'resize', id: nextId++, height });
		},
		destroy(): void {
			destroyed = true;
			for (const fn of cleanupFns) fn();
			pending.clear();
			for (const [taskId, task] of pendingTasks) {
				clearTimeout(task.timer);
				task.reject(new Error('Bridge client destroyed'));
				pendingTasks.delete(taskId);
			}
			stateListeners.clear();
		},
	};

	return client;
}
