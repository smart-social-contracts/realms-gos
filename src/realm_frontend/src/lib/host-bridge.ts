/**
 * Generic host bridge for runtime extensions.
 *
 * Extensions publish document focus; the host routes actions (open assistant,
 * clipboard, etc.) without codex- or extension-specific types.
 */
import { get, writable, type Readable } from 'svelte/store';

export interface DocumentFocusSnapshot {
	languageId: string;
	range: { startLine: number; endLine: number };
	text: string;
}

export interface DocumentFocus {
	/** Extension id that published this focus. */
	source: string;
	/** Opaque, extension-owned deep link for sharing / server resolution. */
	uri: string;
	/** Human-readable label, e.g. "tax_collection, lines 9–31". */
	label?: string;
	/** Optional snapshot so consumers need not call back to the publisher. */
	snapshot?: DocumentFocusSnapshot;
}

/**
 * The text an extension wants the assistant to explain: a chapter, or the
 * selection inside it. It is data for the model to read, never instructions —
 * whoever renders it says so (see the portal's `assistant:prompt` handling).
 */
export interface AssistantPromptContext {
	/**
	 * The text itself, cut to the cap. It is a head start, not the whole truth: a long chapter does not fit, so the
	 * reference below says where the whole of it can be read.
	 */
	text?: string;
	languageId?: string;
	/** Human-readable name of what the text is, e.g. "eu/data_protection.py". */
	label?: string;
	/** Opaque, extension-owned deep link to the same place. */
	uri?: string;
	range?: { startLine: number; endLine: number };
	/** Lines the whole document has, so a cut text can say how much is missing. */
	totalLines?: number;
	/** Set by the host when the text did not fit the cap. */
	truncated?: boolean;
	/**
	 * `sha256:<hex>` of the whole document. It is how the realm pins a chapter, so it both names the text to read
	 * and proves the text read is the right one.
	 */
	checksum?: string;
	/** Whether the realm holds this text itself, or it is published by a layer the realm does not run. */
	held?: 'realm' | 'published';
	/** An immutable public URL of the same text, such as a layer's release commit. */
	url?: string;
}

/**
 * How much text one prompt may carry, in characters. The same order as the
 * assistant backend's own cut for proposal code (12,000) — about 4k tokens,
 * which leaves the realm status, the history and the answer their room. A
 * chapter longer than this is cut at a line boundary and said to be cut.
 */
export const ASSISTANT_CONTEXT_MAX_CHARS = 16_000;

export type HostAction =
	| { type: 'assistant.open' }
	| {
			type: 'assistant.prompt';
			message?: string;
			autoSend?: boolean;
			/** Omitted: the host falls back to the current focus snapshot. */
			context?: AssistantPromptContext;
	  }
	| { type: 'clipboard.write'; text: string }
	/** Go to the realm member home (sidebar default path). Extensions must not name the target. */
	| { type: 'navigate.home' };

export interface PendingPrompt {
	message: string;
	autoSend: boolean;
	id: number;
	context?: AssistantPromptContext | null;
}

export interface HostActionEvent {
	action: HostAction;
	id: number;
}

const focusStore = writable<DocumentFocus | null>(null);
const pendingPromptStore = writable<PendingPrompt | null>(null);
const actionEventStore = writable<HostActionEvent | null>(null);

let promptSeq = 0;
let actionSeq = 0;

export const documentFocus: Readable<DocumentFocus | null> = {
	subscribe: focusStore.subscribe,
};

export const pendingAssistantPrompt: Readable<PendingPrompt | null> = {
	subscribe: pendingPromptStore.subscribe,
};

export const hostActionEvents: Readable<HostActionEvent | null> = {
	subscribe: actionEventStore.subscribe,
};

/**
 * Whether an extension's assistant affordances can reach anything here. The
 * chat UI belongs to the portal — the realm frontend has none — so a realm
 * served on its own origin (`?standalone=1`, a direct canister visit, a local
 * run) has no assistant, and a button for one would be dead.
 */
export function assistantAvailable(opts: {
	realmEnabled: boolean;
	embeddedInPortal: boolean;
}): boolean {
	return opts.realmEnabled && opts.embeddedInPortal;
}

export function setDocumentFocus(focus: DocumentFocus | null): void {
	focusStore.set(focus);
}

function emitAction(action: HostAction): void {
	actionEventStore.set({ action, id: ++actionSeq });
}

export function buildPromptFromFocus(focus: DocumentFocus | null): string | null {
	if (!focus) return null;

	const snap = focus.snapshot;
	if (snap?.text?.trim()) {
		const lines =
			snap.range.startLine === snap.range.endLine
				? `line ${snap.range.startLine}`
				: `lines ${snap.range.startLine}-${snap.range.endLine}`;
		const label = focus.label ?? focus.uri;
		return (
			`Explain the following ${snap.languageId} code (${label}, ${lines}):\n\n` +
			'```' +
			snap.languageId +
			'\n' +
			snap.text +
			'\n```'
		);
	}

	const label = focus.label ?? focus.uri;
	return label ? `Please explain: ${label}` : null;
}

import { copyText } from './clipboard.js';

function copyTextWithFallback(text: string): void {
	void copyText(text);
}

/**
 * The text of a prompt, cut to `ASSISTANT_CONTEXT_MAX_CHARS` on a line
 * boundary so the model never reads half a statement.
 */
export function capAssistantText(text: string): { text: string; truncated: boolean } {
	if (!text) return { text: '', truncated: false };
	if (text.length <= ASSISTANT_CONTEXT_MAX_CHARS) return { text, truncated: false };
	const cut = text.slice(0, ASSISTANT_CONTEXT_MAX_CHARS);
	const lastLineEnd = cut.lastIndexOf('\n');
	// Only honour the line boundary when it keeps most of the cap; one very
	// long line would otherwise shrink the text to almost nothing.
	const kept = lastLineEnd > ASSISTANT_CONTEXT_MAX_CHARS / 2 ? cut.slice(0, lastLineEnd) : cut;
	return { text: kept, truncated: true };
}

/**
 * What the assistant is given to read: what the extension passed with the
 * action, or else the current focus snapshot (what older extensions publish).
 * Returns null when there is no text to send.
 */
export function buildAssistantContext(
	context: AssistantPromptContext | undefined,
	focus: DocumentFocus | null,
): AssistantPromptContext | null {
	const fromFocus: AssistantPromptContext | null = focus?.snapshot?.text?.trim()
		? {
				text: focus.snapshot.text,
				languageId: focus.snapshot.languageId,
				label: focus.label,
				uri: focus.uri,
				range: focus.snapshot.range,
			}
		: null;
	// A context the extension passed is used even with no text in it: its reference says where the text is read.
	const source = context?.text?.trim() || context?.checksum || context?.url ? context : fromFocus;
	if (!source) return null;

	const { text, truncated } = capAssistantText(source.text ?? '');
	return text ? { ...source, text, truncated } : { ...source, text: undefined, truncated: false };
}

export function dispatchHostAction(action: HostAction): void {
	if (action.type === 'clipboard.write') {
		copyTextWithFallback(action.text);
		return;
	}

	if (action.type === 'assistant.prompt') {
		const message = action.message ?? buildPromptFromFocus(get(focusStore));
		const context = buildAssistantContext(action.context, get(focusStore));
		const autoSend = action.autoSend ?? false;
		if (message) {
			pendingPromptStore.set({ message, autoSend, id: ++promptSeq, context });
		}
		// The resolved prompt goes out as one event. Whoever has the chat UI
		// (the portal) opens it; a host without one falls back to opening only.
		emitAction({
			type: 'assistant.prompt',
			message: message ?? undefined,
			autoSend,
			context: context ?? undefined,
		});
		return;
	}

	emitAction(action);
}

export function createHostContext(): {
	host: {
		focus: Readable<DocumentFocus | null>;
		setFocus: typeof setDocumentFocus;
		dispatch: typeof dispatchHostAction;
		pendingPrompt: Readable<PendingPrompt | null>;
	};
} {
	return {
		host: {
			focus: documentFocus,
			setFocus: setDocumentFocus,
			dispatch: dispatchHostAction,
			pendingPrompt: pendingAssistantPrompt,
		},
	};
}
