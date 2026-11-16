import { describe, expect, it } from 'vitest';
import {
	ASSISTANT_CONTEXT_MAX_CHARS,
	assistantAvailable,
	buildAssistantContext,
	buildPromptFromFocus,
	capAssistantText,
	dispatchHostAction,
	hostActionEvents,
	setDocumentFocus,
	type HostAction,
} from './host-bridge';

/** The last action the host emitted while `run` dispatched. */
function emitted(run: () => void): HostAction | null {
	let seen: HostAction | null = null;
	const unsub = hostActionEvents.subscribe((event) => {
		if (event) seen = event.action;
	});
	run();
	unsub();
	return seen;
}

describe('host-bridge', () => {
	it('builds a snippet prompt from focus snapshot', () => {
		setDocumentFocus({
			source: 'codex_viewer',
			uri: 'realms://codex_viewer/codex/tax_collection?lines=9-31',
			label: 'tax_collection, lines 9–31',
			snapshot: {
				languageId: 'python',
				range: { startLine: 9, endLine: 31 },
				text: 'def collect():\n    pass',
			},
		});

		const prompt = buildPromptFromFocus({
			source: 'codex_viewer',
			uri: 'realms://codex_viewer/codex/tax_collection?lines=9-31',
			label: 'tax_collection, lines 9–31',
			snapshot: {
				languageId: 'python',
				range: { startLine: 9, endLine: 31 },
				text: 'def collect():\n    pass',
			},
		});

		expect(prompt).toContain('tax_collection, lines 9–31');
		expect(prompt).toContain('```python');
		expect(prompt).toContain('def collect()');
	});

	it('emits navigate.home without a target extension id', () => {
		let seen: { type: string } | null = null;
		const unsub = hostActionEvents.subscribe((event) => {
			if (event?.action.type === 'navigate.home') {
				seen = event.action;
			}
		});
		dispatchHostAction({ type: 'navigate.home' });
		unsub();
		expect(seen).toEqual({ type: 'navigate.home' });
		expect(JSON.stringify(seen)).not.toMatch(/member_dashboard/);
	});

	it('emits the resolved prompt, not a bare open', () => {
		setDocumentFocus(null);
		const action = emitted(() =>
			dispatchHostAction({
				type: 'assistant.prompt',
				message: 'Explain eu/data_protection.py (layer eu, runs here)',
				autoSend: true,
				context: { text: 'def allow():\n    pass', languageId: 'python' },
			}),
		);

		expect(action).toMatchObject({
			type: 'assistant.prompt',
			message: 'Explain eu/data_protection.py (layer eu, runs here)',
			autoSend: true,
		});
		expect(action?.type === 'assistant.prompt' && action.context?.text).toContain('def allow()');
	});

	it('still carries the focus snapshot for an extension that passes no context', () => {
		setDocumentFocus({
			source: 'codex_viewer',
			uri: 'realms://codex_viewer/codex/tax_collection?lines=9-31',
			label: 'tax_collection, lines 9–31',
			snapshot: {
				languageId: 'python',
				range: { startLine: 9, endLine: 31 },
				text: 'def collect():\n    pass',
			},
		});
		const action = emitted(() => dispatchHostAction({ type: 'assistant.prompt', autoSend: true }));
		setDocumentFocus(null);

		expect(action?.type).toBe('assistant.prompt');
		expect(action?.type === 'assistant.prompt' && action.context).toMatchObject({
			text: 'def collect():\n    pass',
			languageId: 'python',
			label: 'tax_collection, lines 9–31',
			truncated: false,
		});
	});

	it('emits a prompt with no context when there is no text anywhere', () => {
		setDocumentFocus(null);
		const action = emitted(() =>
			dispatchHostAction({ type: 'assistant.prompt', message: 'Explain this realm' }),
		);

		expect(action).toEqual({
			type: 'assistant.prompt',
			message: 'Explain this realm',
			autoSend: false,
			context: undefined,
		});
	});
});

describe('capAssistantText', () => {
	it('leaves a chapter that fits alone', () => {
		expect(capAssistantText('def allow():\n    pass')).toEqual({
			text: 'def allow():\n    pass',
			truncated: false,
		});
	});

	it('cuts a long chapter on a line boundary and says it was cut', () => {
		const line = 'x'.repeat(79) + '\n';
		const long = line.repeat(Math.ceil((ASSISTANT_CONTEXT_MAX_CHARS * 2) / line.length));
		const capped = capAssistantText(long);

		expect(capped.truncated).toBe(true);
		expect(capped.text.length).toBeLessThanOrEqual(ASSISTANT_CONTEXT_MAX_CHARS);
		expect(capped.text.endsWith('x')).toBe(true);
		expect(capped.text.split('\n').every((l) => l === '' || l.length === 79)).toBe(true);
	});

	it('cuts at the cap when one line is longer than half of it', () => {
		const capped = capAssistantText('y'.repeat(ASSISTANT_CONTEXT_MAX_CHARS + 10));

		expect(capped.truncated).toBe(true);
		expect(capped.text.length).toBe(ASSISTANT_CONTEXT_MAX_CHARS);
	});
});

describe('buildAssistantContext', () => {
	it('prefers what the extension passed over the published focus', () => {
		const context = buildAssistantContext(
			{ text: 'the whole chapter', label: 'eu/data_protection.py' },
			{
				source: 'codex_viewer',
				uri: 'realms://codex_viewer/codex/x',
				snapshot: { languageId: 'python', range: { startLine: 1, endLine: 1 }, text: 'a line' },
			},
		);

		expect(context).toMatchObject({ text: 'the whole chapter', label: 'eu/data_protection.py' });
	});

	it('is null when neither the action nor the focus holds text', () => {
		expect(buildAssistantContext(undefined, null)).toBeNull();
		expect(buildAssistantContext({ text: '   ' }, null)).toBeNull();
		expect(
			buildAssistantContext(undefined, {
				source: 'codex_viewer',
				uri: 'realms://codex_viewer/codex/x',
				label: 'x',
			}),
		).toBeNull();
	});

	it('caps the text it was handed', () => {
		const context = buildAssistantContext(
			{ text: 'z'.repeat(ASSISTANT_CONTEXT_MAX_CHARS + 1) },
			null,
		);

		expect(context?.truncated).toBe(true);
		expect(context?.text.length).toBe(ASSISTANT_CONTEXT_MAX_CHARS);
	});
});

describe('assistantAvailable', () => {
	it('is true only in a portal, where the chat UI lives', () => {
		expect(assistantAvailable({ realmEnabled: true, embeddedInPortal: true })).toBe(true);
		expect(assistantAvailable({ realmEnabled: true, embeddedInPortal: false })).toBe(false);
		expect(assistantAvailable({ realmEnabled: false, embeddedInPortal: true })).toBe(false);
	});
});
