// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';

const ctor = vi.fn();

vi.mock('./extension-bridge-host', () => ({
	SandboxBridgeService: class {
		ready = Promise.resolve();
		constructor(_iframe: unknown, deps: { callAsync?: unknown; callSync?: unknown }) {
			ctor(deps);
		}
		destroy() {}
	},
}));

vi.mock('./realm', () => ({ read: vi.fn(), must: vi.fn() }));

import { mountSandboxedExtension } from './extension-loader';

describe('mountSandboxedExtension', () => {
	afterEach(() => {
		ctor.mockClear();
		document.body.replaceChildren();
	});

	it('passes callAsync into the sandbox bridge', async () => {
		const callAsync = vi.fn();
		const callSync = vi.fn();
		const container = document.createElement('div');
		document.body.appendChild(container);

		const mounted = await mountSandboxedExtension('member_dashboard', '1.1.4', container, {
			manifest: { capabilities: ['call_extension'] },
			callSync,
			callAsync,
			navigate: vi.fn(async () => {}),
			getHostState: () => ({
				principal: '',
				locale: 'en',
				theme: 'light',
				realmInfo: {
					name: 't',
					welcomeMessage: '',
					manifesto: '',
					isQuarter: false,
					parentRealmCanisterId: '',
				},
				cssVariables: {
					'--color-primary-600': '#3b82f6',
				},
			}),
			subscribeHostState: () => () => {},
		});

		expect(ctor).toHaveBeenCalled();
		expect(ctor.mock.calls[0]?.[0].callAsync).toBe(callAsync);
		expect(ctor.mock.calls[0]?.[0].callSync).toBe(callSync);
		const iframe = container.querySelector('iframe');
		expect(iframe?.getAttribute('sandbox')).toBe('allow-scripts allow-modals');
		mounted.unmount();
	});
});
