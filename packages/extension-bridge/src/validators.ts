import {
	BRIDGE_SOURCE,
	type CallExtensionAsyncMessage,
	type CallExtensionMessage,
	type ExtToHostMessage,
	type GetStateMessage,
	type GoodbyeMessage,
	type NavigateMessage,
	type NotifyLevel,
	type NotifyMessage,
	type OpenModalMessage,
	type ResizeMessage,
} from './protocol.js';

const NOTIFY_LEVELS: ReadonlySet<string> = new Set(['info', 'success', 'error']);
const MODAL_TONES: ReadonlySet<string> = new Set(['primary', 'secondary', 'danger']);

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
	return typeof value === 'string';
}

function isFiniteNumber(value: unknown): value is number {
	return typeof value === 'number' && Number.isFinite(value);
}

function isRequestId(value: unknown): value is number {
	return typeof value === 'number' && Number.isInteger(value) && value >= 1;
}

function validateModalActions(value: unknown): ModalActionValidation {
	if (value === undefined) {
		return { ok: true, actions: [] };
	}
	if (!Array.isArray(value)) {
		return { ok: false, reason: 'open_modal.actions must be an array when present' };
	}
	const actions: OpenModalMessage['actions'] = [];
	for (const item of value) {
		if (!isPlainObject(item)) {
			return { ok: false, reason: 'open_modal.actions entries must be objects' };
		}
		if (!isString(item.id)) {
			return { ok: false, reason: 'open_modal.actions[].id must be a string' };
		}
		if (!isString(item.label)) {
			return { ok: false, reason: 'open_modal.actions[].label must be a string' };
		}
		if (item.tone !== undefined && !MODAL_TONES.has(item.tone as string)) {
			return { ok: false, reason: 'open_modal.actions[].tone must be primary, secondary, or danger' };
		}
		actions.push({
			id: item.id,
			label: item.label,
			tone: item.tone as OpenModalMessage['actions'][number]['tone'],
		});
	}
	return { ok: true, actions };
}

type ModalActionValidation =
	| { ok: true; actions: OpenModalMessage['actions'] }
	| { ok: false; reason: string };

export interface ExtMessageValidationFailure {
	ok: false;
	reason: string;
	/** When set, the host should reply with `{ code: 'bad_request' }`. */
	responseId?: number;
}

export type ExtMessageValidationResult =
	| { ok: true; message: ExtToHostMessage | GoodbyeMessage }
	| ExtMessageValidationFailure;

function envelope(data: Record<string, unknown>): boolean {
	return data.source === BRIDGE_SOURCE && isString(data.kind);
}

export function validateHello(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'hello') {
		return { ok: false, reason: 'invalid hello envelope' };
	}
	if (!isString(data.sdkVersion)) {
		return { ok: false, reason: 'hello.sdkVersion must be a string' };
	}
	return {
		ok: true,
		message: { source: BRIDGE_SOURCE, kind: 'hello', sdkVersion: data.sdkVersion },
	};
}

export function validateCallExtension(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'call_extension') {
		return { ok: false, reason: 'invalid call_extension envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'call_extension.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	if (!isString(data.fn)) {
		return { ok: false, reason: 'call_extension.fn must be a string', responseId: data.id };
	}
	if (data.args !== undefined && !isPlainObject(data.args)) {
		return { ok: false, reason: 'call_extension.args must be an object when present', responseId: data.id };
	}
	const message: CallExtensionMessage = {
		source: BRIDGE_SOURCE,
		kind: 'call_extension',
		id: data.id,
		fn: data.fn,
		args: (data.args as Record<string, unknown> | undefined) ?? {},
		read: data.read === true,
	};
	return { ok: true, message };
}

export function validateCallExtensionAsync(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'call_extension_async') {
		return { ok: false, reason: 'invalid call_extension_async envelope' };
	}
	if (!isRequestId(data.id)) {
		return {
			ok: false,
			reason: 'call_extension_async.id must be a positive integer',
			responseId: typeof data.id === 'number' ? data.id : undefined,
		};
	}
	if (!isString(data.fn)) {
		return { ok: false, reason: 'call_extension_async.fn must be a string', responseId: data.id };
	}
	if (data.args !== undefined && !isPlainObject(data.args)) {
		return {
			ok: false,
			reason: 'call_extension_async.args must be an object when present',
			responseId: data.id,
		};
	}
	const message: CallExtensionAsyncMessage = {
		source: BRIDGE_SOURCE,
		kind: 'call_extension_async',
		id: data.id,
		fn: data.fn,
		args: (data.args as Record<string, unknown> | undefined) ?? {},
	};
	return { ok: true, message };
}

export function validateNavigate(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'navigate') {
		return { ok: false, reason: 'invalid navigate envelope' };
	}
	if (!isString(data.path)) {
		return { ok: false, reason: 'navigate.path must be a string' };
	}
	const message: NavigateMessage = {
		source: BRIDGE_SOURCE,
		kind: 'navigate',
		id: typeof data.id === 'number' ? data.id : 0,
		path: data.path,
	};
	return { ok: true, message };
}

export function validateNotify(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'notify') {
		return { ok: false, reason: 'invalid notify envelope' };
	}
	if (!isString(data.message)) {
		return { ok: false, reason: 'notify.message must be a string' };
	}
	if (!NOTIFY_LEVELS.has(data.level as string)) {
		return { ok: false, reason: 'notify.level must be info, success, or error' };
	}
	const message: NotifyMessage = {
		source: BRIDGE_SOURCE,
		kind: 'notify',
		id: typeof data.id === 'number' ? data.id : 0,
		level: data.level as NotifyLevel,
		message: data.message,
	};
	return { ok: true, message };
}

export function validateOpenModal(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'open_modal') {
		return { ok: false, reason: 'invalid open_modal envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'open_modal.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	if (!isString(data.title)) {
		return { ok: false, reason: 'open_modal.title must be a string', responseId: data.id };
	}
	if (!isString(data.body)) {
		return { ok: false, reason: 'open_modal.body must be a string', responseId: data.id };
	}
	const actionsResult = validateModalActions(data.actions);
	if (!actionsResult.ok) {
		return { ok: false, reason: actionsResult.reason, responseId: data.id };
	}
	const message: OpenModalMessage = {
		source: BRIDGE_SOURCE,
		kind: 'open_modal',
		id: data.id,
		title: data.title,
		body: data.body,
		actions: actionsResult.actions,
	};
	return { ok: true, message };
}

export function validateResize(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'resize') {
		return { ok: false, reason: 'invalid resize envelope' };
	}
	if (!isFiniteNumber(data.height)) {
		return { ok: false, reason: 'resize.height must be a finite number' };
	}
	const message: ResizeMessage = {
		source: BRIDGE_SOURCE,
		kind: 'resize',
		id: typeof data.id === 'number' ? data.id : 0,
		height: data.height,
	};
	return { ok: true, message };
}

export function validateGetState(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'get_state') {
		return { ok: false, reason: 'invalid get_state envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'get_state.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	const message: GetStateMessage = {
		source: BRIDGE_SOURCE,
		kind: 'get_state',
		id: data.id,
	};
	return { ok: true, message };
}

export function validateGoodbye(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'goodbye') {
		return { ok: false, reason: 'invalid goodbye envelope' };
	}
	if (data.id !== undefined && !isRequestId(data.id)) {
		return { ok: false, reason: 'goodbye.id must be a positive integer when present' };
	}
	const message: GoodbyeMessage = {
		source: BRIDGE_SOURCE,
		kind: 'goodbye',
		id: data.id as number | undefined,
	};
	return { ok: true, message };
}

/** At most this many recipients for one sealed record. */
export const VAULT_RECIPIENTS_MAX = 64;
/** A principal in its text form: base32 groups of five, joined by dashes. */
const PRINCIPAL_RE = /^[a-z2-7]{1,5}(-[a-z2-7]{1,5})*$/;
/** A wrapped key in hex, and a ciphertext as the host writes it. */
const WRAPPED_KEY_RE = /^[0-9a-f]{2,4096}$/i;
/** A file name the browser can save as it is: no path, no control characters. */
const FILE_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._ ()-]{0,119}$/;
/** The kinds of file a screen may hand the member. */
export const SAVE_FILE_TYPES: ReadonlySet<string> = new Set([
	'application/xml',
	'text/xml',
	'text/plain',
	'text/csv',
	'application/json',
	'application/pdf',
	'image/svg+xml',
]);

export function validateVaultSeal(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'vault_seal') {
		return { ok: false, reason: 'invalid vault_seal envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'vault_seal.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	const recipients = data.recipients;
	if (!Array.isArray(recipients) || recipients.length === 0 || recipients.length > VAULT_RECIPIENTS_MAX) {
		return { ok: false, reason: `vault_seal.recipients must list 1 to ${VAULT_RECIPIENTS_MAX} principals`, responseId: data.id };
	}
	if (!recipients.every((p) => isString(p) && PRINCIPAL_RE.test(p))) {
		return { ok: false, reason: 'vault_seal.recipients must be principals', responseId: data.id };
	}
	if (data.data === undefined) {
		return { ok: false, reason: 'vault_seal.data is missing', responseId: data.id };
	}
	return {
		ok: true,
		message: { source: BRIDGE_SOURCE, kind: 'vault_seal', id: data.id, recipients: [...new Set(recipients as string[])], data: data.data },
	};
}

export function validateVaultOpen(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'vault_open') {
		return { ok: false, reason: 'invalid vault_open envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'vault_open.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	if (!isString(data.wrappedKey) || !WRAPPED_KEY_RE.test(data.wrappedKey)) {
		return { ok: false, reason: 'vault_open.wrappedKey must be a key in hex', responseId: data.id };
	}
	if (!isString(data.ciphertext) || !data.ciphertext) {
		return { ok: false, reason: 'vault_open.ciphertext must be a string', responseId: data.id };
	}
	return {
		ok: true,
		message: { source: BRIDGE_SOURCE, kind: 'vault_open', id: data.id, wrappedKey: data.wrappedKey, ciphertext: data.ciphertext },
	};
}

export function validateSaveFile(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'save_file') {
		return { ok: false, reason: 'invalid save_file envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'save_file.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	if (!isString(data.name) || !FILE_NAME_RE.test(data.name) || data.name.includes('..')) {
		return { ok: false, reason: 'save_file.name must be a plain file name', responseId: data.id };
	}
	if (!isString(data.mimeType) || !SAVE_FILE_TYPES.has(data.mimeType)) {
		return { ok: false, reason: `save_file.mimeType must be one of ${[...SAVE_FILE_TYPES].join(', ')}`, responseId: data.id };
	}
	if (!isString(data.content)) {
		return { ok: false, reason: 'save_file.content must be a string', responseId: data.id };
	}
	if (data.base64 !== undefined && typeof data.base64 !== 'boolean') {
		return { ok: false, reason: 'save_file.base64 must be a boolean when present', responseId: data.id };
	}
	return {
		ok: true,
		message: {
			source: BRIDGE_SOURCE,
			kind: 'save_file',
			id: data.id,
			name: data.name,
			mimeType: data.mimeType,
			content: data.content,
			base64: data.base64 === true,
		},
	};
}

/** Validate an extension→host bridge message before dispatch. */
export function validateExtToHostMessage(data: unknown): ExtMessageValidationResult {
	if (!isPlainObject(data)) {
		return { ok: false, reason: 'message must be an object' };
	}
	switch (data.kind) {
		case 'hello':
			return validateHello(data);
		case 'call_extension':
			return validateCallExtension(data);
		case 'call_extension_async':
			return validateCallExtensionAsync(data);
		case 'call_operation':
			// A screen built against bridge 0.3, which called realm operations itself: it now reaches the realm only
			// through its own backend, and is told so rather than left waiting for a reply.
			return {
				ok: false,
				reason: 'a screen calls only its own backend (call_extension), which calls the realm operations it declares',
				responseId: isRequestId(data.id) ? data.id : undefined,
			};
		case 'navigate':
			return validateNavigate(data);
		case 'notify':
			return validateNotify(data);
		case 'open_modal':
			return validateOpenModal(data);
		case 'resize':
			return validateResize(data);
		case 'get_state':
			return validateGetState(data);
		case 'goodbye':
			return validateGoodbye(data);
		case 'vault_seal':
			return validateVaultSeal(data);
		case 'vault_open':
			return validateVaultOpen(data);
		case 'save_file':
			return validateSaveFile(data);
		default:
			return { ok: false, reason: `unknown message kind: ${String(data.kind)}` };
	}
}
