import {
	BRIDGE_SOURCE,
	type CallExtensionAsyncMessage,
	type CallExtensionMessage,
	type CallOperationMessage,
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
	};
	return { ok: true, message };
}

export function validateCallOperation(data: Record<string, unknown>): ExtMessageValidationResult {
	if (!envelope(data) || data.kind !== 'call_operation') {
		return { ok: false, reason: 'invalid call_operation envelope' };
	}
	if (!isRequestId(data.id)) {
		return { ok: false, reason: 'call_operation.id must be a positive integer', responseId: typeof data.id === 'number' ? data.id : undefined };
	}
	if (!isString(data.name)) {
		return { ok: false, reason: 'call_operation.name must be a string', responseId: data.id };
	}
	if (data.args !== undefined && !isPlainObject(data.args)) {
		return { ok: false, reason: 'call_operation.args must be an object when present', responseId: data.id };
	}
	const message: CallOperationMessage = {
		source: BRIDGE_SOURCE,
		kind: 'call_operation',
		id: data.id,
		name: data.name,
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
			return validateCallOperation(data);
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
		default:
			return { ok: false, reason: `unknown message kind: ${String(data.kind)}` };
	}
}
