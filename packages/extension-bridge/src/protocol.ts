/** Bridge protocol major version — must match manifest `sdk_version`. */
export const BRIDGE_PROTOCOL_VERSION = '1';

/** Discriminant on every bridge message. */
export const BRIDGE_SOURCE = 'realm-bridge' as const;

export type BridgeErrorCode =
	| 'denied'
	| 'unsupported'
	| 'failed'
	| 'bad_request'
	| 'rate_limited';

export interface BridgeErrorPayload {
	code: BridgeErrorCode;
	message: string;
}

/** Subset of realm metadata pushed to sandboxed extensions. */
export interface HostRealmInfo {
	name: string;
	welcomeMessage: string;
	manifesto: string;
	isQuarter: boolean;
	parentRealmCanisterId: string;
	logoUrl?: string;
	backgroundImageUrl?: string;
	primaryColor?: string;
}

/** Host-pushed identity and chrome state. */
export interface HostState {
	principal: string;
	locale: string;
	theme: 'light' | 'dark';
	realmInfo: HostRealmInfo;
	cssVariables?: Record<string, string>;
}

export type NotifyLevel = 'info' | 'success' | 'error';

export interface ModalAction {
	id: string;
	label: string;
	tone?: 'primary' | 'secondary' | 'danger';
}

interface BridgeEnvelope {
	source: typeof BRIDGE_SOURCE;
}

// ── Extension → host ────────────────────────────────────────────────────

export interface HelloMessage extends BridgeEnvelope {
	kind: 'hello';
	sdkVersion: string;
}

/** A function of the extension's own backend, the only way a screen reaches the realm: ``read`` runs it as a query
 * (``extension_read``: free, it can only read), otherwise as an update (``extension_call``). */
export interface CallExtensionMessage extends BridgeEnvelope {
	kind: 'call_extension';
	id: number;
	fn: string;
	args: Record<string, unknown>;
	read?: boolean;
}

export interface CallExtensionAsyncMessage extends BridgeEnvelope {
	kind: 'call_extension_async';
	id: number;
	fn: string;
	args: Record<string, unknown>;
}

export interface NavigateMessage extends BridgeEnvelope {
	kind: 'navigate';
	id: number;
	path: string;
}

export interface NotifyMessage extends BridgeEnvelope {
	kind: 'notify';
	id: number;
	level: NotifyLevel;
	message: string;
}

export interface OpenModalMessage extends BridgeEnvelope {
	kind: 'open_modal';
	id: number;
	title: string;
	body: string;
	actions: ModalAction[];
}

export interface ResizeMessage extends BridgeEnvelope {
	kind: 'resize';
	id: number;
	height: number;
}

export interface GetStateMessage extends BridgeEnvelope {
	kind: 'get_state';
	id: number;
}

export interface GoodbyeMessage extends BridgeEnvelope {
	kind: 'goodbye';
	id?: number;
}

/** Capability ``vault``: the host encrypts ``data`` with a fresh key and wraps that key for each of ``recipients`` (their
 * principals) with the realm's vetKeys. The screen stores what it gets back through its backend. */
export interface VaultSealMessage extends BridgeEnvelope {
	kind: 'vault_seal';
	id: number;
	recipients: string[];
	data: unknown;
}

/** Capability ``vault``: the host opens a record sealed for the member, with the key ``wrappedKey`` wrapped for them, and
 * returns its plaintext. The member's vetKey stays in the host: the frame only ever sees what it asked to open. */
export interface VaultOpenMessage extends BridgeEnvelope {
	kind: 'vault_open';
	id: number;
	wrappedKey: string;
	ciphertext: string;
}

/** What ``vault_seal`` returns: the ciphertext, and the record's key wrapped for each recipient. */
export interface VaultSealed {
	ciphertext: string;
	keys: Record<string, string>;
}

/** Capability ``download``: the member saves a file the screen made. The host first asks the extension's backend
 * whether this file may leave: it calls ``download_allowed(name, sha256, size)`` as a query, and saves the file only
 * when that returns ``{ ok: true, allowed: true }``. So an extension that must record a file before it leaves (a payment
 * file whose hash the realm commits) has the realm, not the frame, decide. ``content`` is text, or base64 when
 * ``base64`` is set. */
export interface SaveFileMessage extends BridgeEnvelope {
	kind: 'save_file';
	id: number;
	name: string;
	mimeType: string;
	content: string;
	base64?: boolean;
}

/** What ``save_file`` returns once the host handed the file to the browser. */
export interface FileSaved {
	sha256: string;
	size: number;
}

export type TaskResultStatus = 'completed' | 'failed';

export interface TaskResultPayload {
	status: TaskResultStatus;
	result?: unknown;
	error?: string;
}

export type ExtToHostMessage =
	| HelloMessage
	| CallExtensionMessage
	| CallExtensionAsyncMessage
	| NavigateMessage
	| NotifyMessage
	| OpenModalMessage
	| ResizeMessage
	| GetStateMessage
	| GoodbyeMessage
	| VaultSealMessage
	| VaultOpenMessage
	| SaveFileMessage;

// ── Host → extension ────────────────────────────────────────────────────

export interface HelloAckMessage extends BridgeEnvelope {
	kind: 'hello_ack';
	sdkVersion: string;
	extensionId: string;
	capabilities: string[];
	state: HostState;
}

export interface HelloNackMessage extends BridgeEnvelope {
	kind: 'hello_nack';
	reason: string;
}

export interface CallResultMessage extends BridgeEnvelope {
	kind: 'call_result';
	id: number;
	result: unknown;
}

export interface ErrorMessage extends BridgeEnvelope {
	kind: 'error';
	id: number;
	error: BridgeErrorPayload;
}

export interface StatePushMessage extends BridgeEnvelope {
	kind: 'state';
	state: HostState;
}

export interface ModalResultMessage extends BridgeEnvelope {
	kind: 'modal_result';
	id: number;
	actionId: string;
}

/** One-shot host push when an async extension call completes. */
export interface TaskResultMessage extends BridgeEnvelope {
	kind: 'task_result';
	taskId: string;
	status: TaskResultStatus;
	result?: unknown;
	error?: string;
}

export type HostToExtMessage =
	| HelloAckMessage
	| HelloNackMessage
	| CallResultMessage
	| ErrorMessage
	| StatePushMessage
	| ModalResultMessage
	| TaskResultMessage;

export type BridgeMessage = ExtToHostMessage | HostToExtMessage;

export function isBridgeMessage(data: unknown): data is BridgeMessage {
	if (typeof data !== 'object' || data === null) return false;
	const msg = data as Record<string, unknown>;
	return msg.source === BRIDGE_SOURCE && typeof msg.kind === 'string';
}

export function bridgeProtocolMajor(version: string): string {
	return version.split('.')[0] ?? version;
}

export function bridgeVersionsCompatible(a: string, b: string): boolean {
	return bridgeProtocolMajor(a) === bridgeProtocolMajor(b);
}
