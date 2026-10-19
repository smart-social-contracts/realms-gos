<script lang="ts">
	import { onMount } from 'svelte';
	import { createExtensionClient, type HostState } from '@realmsgos/extension-bridge';
	import { PageHeader, Card, Button } from '@realmsgos/extension-ui';

	let bridgeReady = $state(false);
	let bridgeError = $state('');
	let hostState = $state<HostState | null>(null);
	let greetResult = $state<string | null>(null);
	let actionBusy = $state(false);

	let ctx = $state.raw<Awaited<ReturnType<typeof createExtensionClient>> | null>(null);

	function applyTheme(theme: 'light' | 'dark') {
		document.documentElement.classList.toggle('dark', theme === 'dark');
		document.documentElement.dataset.theme = theme;
	}

	function reportHeight() {
		ctx?.reportHeight(document.body.scrollHeight);
	}

	async function initClient() {
		try {
			const client = await createExtensionClient();
			ctx = client;
			bridgeReady = true;

			client.onStateChange((state) => {
				hostState = state;
				applyTheme(state.theme);
				queueMicrotask(reportHeight);
			});
		} catch (e) {
			bridgeError = e instanceof Error ? e.message : String(e);
		}
	}

	async function runGreet() {
		if (!ctx) return;
		actionBusy = true;
		greetResult = null;
		try {
			const data = await ctx.callExtension<string>('greet', {});
			greetResult = typeof data === 'string' ? data : JSON.stringify(data);
		} catch (e) {
			const message = e instanceof Error ? e.message : String(e);
			greetResult = `Error: ${message}`;
			ctx.notify('error', message);
		} finally {
			actionBusy = false;
			queueMicrotask(reportHeight);
		}
	}

	onMount(() => {
		void initClient();

		const observer = new ResizeObserver(() => reportHeight());
		observer.observe(document.body);
		queueMicrotask(reportHeight);

		return () => {
			observer.disconnect();
			ctx?.destroy();
		};
	});
</script>

<div class="mx-auto max-w-3xl space-y-6 px-4 pb-8">
	<PageHeader
		title="__EXTENSION_NAME__"
		subtitle="__EXTENSION_DESCRIPTION__"
	/>

	<Card title="Bridge">
		{#snippet children()}
			{#if bridgeError}
				<p class="text-sm text-red-600 dark:text-red-400">Handshake failed: {bridgeError}</p>
			{:else if bridgeReady}
				<p class="text-sm text-gray-600 dark:text-gray-400">
					Connected as <span class="font-mono">{ctx?.extensionId}</span>
					{#if hostState}
						· theme <span class="font-mono">{hostState.theme}</span>
					{/if}
				</p>
			{:else}
				<p class="text-sm text-gray-500 dark:text-gray-400">Waiting for host handshake…</p>
			{/if}
		{/snippet}
	</Card>

	<Card title="Hello world">
		{#snippet children()}
			<p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
				Calls <code class="font-mono text-xs">greet</code> in <code class="font-mono text-xs">backend/entry.py</code>
				via the bridge (<code class="font-mono text-xs">call_extension</code> capability).
			</p>
			<span data-testid="hello-button">
				<Button tone="primary" disabled={!bridgeReady || actionBusy} onclick={runGreet}>
					Say hello
				</Button>
			</span>
			{#if greetResult}
				<p
					data-testid="hello-result"
					class="mt-4 rounded-md bg-gray-50 p-3 font-mono text-sm text-gray-700 dark:bg-gray-900 dark:text-gray-300"
				>
					{greetResult}
				</p>
			{/if}
		{/snippet}
	</Card>
</div>
