import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

type Op = (args: Record<string, unknown>) => unknown;

const { ops, read, run, registry } = vi.hoisted(() => {
	const ops: Record<string, Op> = {};
	const call = async (name: string, args: Record<string, unknown> = {}) => ops[name](args);
	return {
		ops,
		read: vi.fn(call),
		run: vi.fn(call),
		registry: { listRegistryCodices: vi.fn(), getExtensionManifest: vi.fn() }
	};
});

let actorReadyResolve: () => void;
const backendActorReady = new Promise<void>((resolve) => {
	actorReadyResolve = resolve;
});

vi.mock('$lib/canisters', () => ({
	backendActorReady,
	// Simulates the production hang: backendReady never settles during boot.
	backendReady: new Promise(() => {})
}));

vi.mock('$lib/realm', () => ({
	read,
	run,
	must: async (reply: Promise<{ ok: boolean; error?: string }>) => {
		const out = await reply;
		if (!out.ok) throw new Error(out.error);
		return out;
	}
}));

vi.mock('$lib/file-registry-client', () => ({
	...registry,
	getFileRegistryCanisterIds: () => []
}));

vi.mock('./ledgerSymbol', () => ({ lookupLedgerSymbol: vi.fn().mockResolvedValue('RLM') }));

function setupState(overrides: Record<string, unknown> = {}) {
	return {
		ok: true,
		status: 'setup',
		founder: 'founder-principal',
		done: ['install'],
		todo: ['realm'],
		realm: {
			name: 'Agora',
			manifesto: '',
			welcome_message: '',
			token_canister_id: '',
			file_registry_canister_id: 'registry-id',
			shared_tokens_json: { RLM: { ledger: 'rlm-ledger', decimals: 8 } },
			...overrides
		}
	};
}

function codexStatus(codex: { id: string; version: string } | null = null) {
	return { ok: true, codex, history: [], next: null };
}

function useOps(overrides: Record<string, Op> = {}) {
	for (const key of Object.keys(ops)) delete ops[key];
	Object.assign(
		ops,
		{
			setup_state: () => setupState(),
			codex_status: () => codexStatus(),
			realm_info: () => ({ ok: true, realm: { status: 'alpha' } })
		},
		overrides
	);
}

beforeEach(() => {
	vi.resetModules();
	read.mockClear();
	run.mockClear();
	useOps();
	actorReadyResolve();
});

describe('fetchSetupState', () => {
	it('resolves once backendActorReady settles even if backendReady is still pending', async () => {
		useOps({ codex_status: () => codexStatus({ id: 'syntropia@1.0.0', version: '1.0.0' }) });
		const { fetchSetupState } = await import('./api');
		const state = await fetchSetupState();

		expect(state).toMatchObject({
			status: 'setup',
			creator: 'founder-principal',
			is_caller_authorized: true,
			codex: { package: 'syntropia', version: '1.0.0' },
			token: null,
			realm_name: 'Agora'
		});
		expect(run).toHaveBeenCalledWith('setup_state', {});
	});

	it('falls back to the public realm status for a caller who may not configure the realm', async () => {
		useOps({ setup_state: () => ({ ok: false, error: 'setup_state needs realm.configure' }) });
		const { fetchSetupState } = await import('./api');
		const state = await fetchSetupState();

		expect(state.status).toBe('alpha');
		expect(state.is_caller_authorized).toBe(false);
	});
});

describe('listAvailableCodices', () => {
	it('lists the registry codices the wizard offers, with their manifest names', async () => {
		registry.listRegistryCodices.mockResolvedValue([
			{ codex_id: 'syntropia', versions: ['1.0.0'], latest: '1.0.0' },
			{ codex_id: 'common', versions: ['1.0.0'], latest: '1.0.0' }
		]);
		registry.getExtensionManifest.mockResolvedValue({ name: 'Syntropia', description: 'A codex' });
		const { listAvailableCodices } = await import('./api');

		expect(await listAvailableCodices()).toEqual([
			{ id: 'syntropia', versions: ['1.0.0'], name: 'Syntropia', description: 'A codex', repository: '' }
		]);
		expect(registry.listRegistryCodices).toHaveBeenCalledWith('registry-id');
	});
});

describe('installSetupCodex', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('installs through codex_install', async () => {
		useOps({ codex_install: () => ({ ok: true, codex: 'syntropia@1.0.0' }) });
		const { installSetupCodex } = await import('./api');
		const result = await installSetupCodex({ package: 'syntropia', version: '1.0.0' });

		expect(result).toMatchObject({ success: true, resolved_version: '1.0.0' });
		expect(run).toHaveBeenCalledWith('codex_install', { codex: 'syntropia', version: '1.0.0' });
	});

	it('polls setup state when the install call throws an ambiguous error', async () => {
		let reads = 0;
		useOps({
			codex_install: () => {
				throw new Error('Call was returned undefined');
			},
			codex_status: () => {
				reads += 1;
				return codexStatus(reads >= 2 ? { id: 'syntropia@1.0.0', version: '1.0.0' } : null);
			}
		});
		const { installSetupCodex } = await import('./api');
		const promise = installSetupCodex(
			{ package: 'syntropia', version: '1.0.0' },
			{ rawCallGraceMs: 100, pollIntervalMs: 50 }
		);
		await vi.advanceTimersByTimeAsync(100);
		await vi.advanceTimersByTimeAsync(50);

		expect((await promise).success).toBe(true);
		expect(reads).toBeGreaterThanOrEqual(2);
	});

	it('surfaces refusals without polling', async () => {
		useOps({ codex_install: () => ({ ok: false, error: 'approval denied' }) });
		const { installSetupCodex } = await import('./api');
		const result = await installSetupCodex(
			{ package: 'syntropia', version: '1.0.0' },
			{ rawCallGraceMs: 100 }
		);

		expect(result).toEqual({ success: false, error: 'approval denied' });
		expect(read).not.toHaveBeenCalledWith('codex_status', expect.anything());
	});

	it('throws on non-ambiguous install errors without polling', async () => {
		useOps({
			codex_install: () => {
				throw new Error('Network error');
			}
		});
		const { installSetupCodex } = await import('./api');

		await expect(
			installSetupCodex({ package: 'syntropia', version: '1.0.0' }, { rawCallGraceMs: 100 })
		).rejects.toThrow('Network error');
		expect(read).not.toHaveBeenCalledWith('codex_status', expect.anything());
	});
});

describe('completeSetup', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('polls setup state when the launch call never settles', async () => {
		let reads = 0;
		useOps({
			setup_launch: () => new Promise(() => {}),
			setup_state: () => {
				reads += 1;
				return { ...setupState(), status: reads >= 2 ? 'alpha' : 'setup' };
			}
		});
		const { completeSetup } = await import('./api');
		const promise = completeSetup({ rawCallGraceMs: 100, pollIntervalMs: 50 });
		await vi.advanceTimersByTimeAsync(100);
		await vi.advanceTimersByTimeAsync(50);

		expect(await promise).toEqual({ success: true });
	});

	it('returns refusals without polling', async () => {
		useOps({ setup_launch: () => ({ ok: false, error: 'name the realm first' }) });
		const { completeSetup } = await import('./api');
		const result = await completeSetup({ rawCallGraceMs: 100 });

		expect(result).toEqual({ success: false, error: 'name the realm first' });
		expect(run).not.toHaveBeenCalledWith('setup_state', expect.anything());
	});
});

describe('pollUntilCodexInstalled', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it('succeeds on a final setup-state check after the poll deadline', async () => {
		let reads = 0;
		useOps({
			codex_status: () => {
				reads += 1;
				return codexStatus(reads >= 3 ? { id: 'syntropia@1.0.0', version: '1.0.0' } : null);
			}
		});
		const { pollUntilCodexInstalled } = await import('./api');
		const promise = pollUntilCodexInstalled('syntropia', '1.0.0', 100, 50);
		await vi.advanceTimersByTimeAsync(100);
		await promise;

		expect(reads).toBe(3);
	});

	it('throws a 40-minute timeout error when the final check still has no codex', async () => {
		const { pollUntilCodexInstalled } = await import('./api');
		const timeoutMs = 40 * 60 * 1_000;
		const promise = pollUntilCodexInstalled('syntropia', '1.0.0', timeoutMs, 5_000);
		const expectation = expect(promise).rejects.toThrow(
			/Codex installation timed out after 40 minutes/
		);
		await vi.advanceTimersByTimeAsync(timeoutMs);
		await expectation;
	});
});

describe('setup draft and launch', () => {
	beforeEach(() => {
		const store = new Map<string, string>();
		vi.stubGlobal('localStorage', {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => store.set(key, value)
		});
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('keeps the draft and its images in this browser', async () => {
		const { saveSetupDraft, fetchSetupDraftAsset } = await import('./api');
		const saved = await saveSetupDraft({
			codex: { package: 'syntropia', version: '1.0.0' },
			branding: { logo_data_url: 'data:image/png;base64,AA', colors: { primary: '#1E6B52' } }
		});

		expect(saved.draft).toMatchObject({
			codex: { package: 'syntropia' },
			branding: { logo: true, colors: { primary: '#1E6B52' } }
		});
		expect(await fetchSetupDraftAsset('logo')).toBe('data:image/png;base64,AA');
		expect(await fetchSetupDraftAsset('background')).toBeNull();
	});

	it('applies the draft step by step, then opens the realm', async () => {
		useOps({
			codex_install: () => ({ ok: true }),
			setup_step: () => ({ ok: true }),
			setup_launch: () => ({ ok: true, status: 'alpha' })
		});
		const { saveSetupDraft, startSetupLaunch, fetchSetupLaunchStatus } = await import('./api');
		await saveSetupDraft({
			codex: { package: 'syntropia', version: '1.0.0' },
			token: 'RLM',
			identity: { manifesto: 'We share' }
		});

		expect((await startSetupLaunch()).launch?.status).toBe('running');
		await vi.waitFor(async () => expect((await fetchSetupLaunchStatus()).status).toBe('completed'));

		const calls = run.mock.calls.filter(([name]) => name !== 'setup_state');
		expect(calls).toEqual([
			['codex_install', { codex: 'syntropia', version: '1.0.0' }],
			[
				'setup_step',
				{
					step: 'tokens',
					token_canister_id: 'rlm-ledger',
					accounting_currency: 'RLM',
					accounting_currency_decimals: 8
				}
			],
			['setup_step', expect.objectContaining({ step: 'branding' })],
			['setup_step', expect.objectContaining({ step: 'realm', manifesto: 'We share' })],
			['setup_launch', {}]
		]);
	});

	it('refuses to launch without a codex', async () => {
		const { startSetupLaunch } = await import('./api');
		expect(await startSetupLaunch()).toEqual({
			success: false,
			error: 'Choose a codex before launching'
		});
	});
});

describe('isAmbiguousInstallError', () => {
	it('detects agent-js ambiguous sync-call failures', async () => {
		const { isAmbiguousInstallError } = await import('./api');
		expect(isAmbiguousInstallError(new Error('Call was returned undefined'))).toBe(true);
		expect(
			isAmbiguousInstallError(new Error('Cannot determine if the call was successful'))
		).toBe(true);
		expect(isAmbiguousInstallError(new Error('Network error'))).toBe(false);
	});
});
