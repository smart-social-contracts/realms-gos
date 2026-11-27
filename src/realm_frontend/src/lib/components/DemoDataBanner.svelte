<script lang="ts">
	import { browser } from '$app/environment';
	import { _ } from 'svelte-i18n';
	import { isEmbeddedInPortal } from '$lib/portal-bridge.ts';
	import { realmName, testModeDemoNotice } from '$lib/stores/realmInfo';
	import DemoLegalLinks from './DemoLegalLinks.svelte';

	// Shown on every page of a demo realm (one whose demo notice is on: a staging or demo network by default). It can
	// be folded to one line, and that choice is remembered, but it has no dismiss: the notice must never disappear.
	// Inside the portal the portal's own demo banner stands above the realm on every page, so the realm leaves it there
	// rather than show a second one.
	const COLLAPSED_KEY = 'demo_data_banner_collapsed';
	const embedded = browser && isEmbeddedInPortal();

	let collapsed = $state(browser && localStorage.getItem(COLLAPSED_KEY) === 'true');

	function toggle() {
		collapsed = !collapsed;
		if (browser) localStorage.setItem(COLLAPSED_KEY, String(collapsed));
	}
</script>

{#if $testModeDemoNotice && !embedded}
	<div
		class="flex items-start gap-3 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-950"
		role="region"
		aria-label={$_('demo_data_banner.title')}
		data-testid="demo-data-banner"
	>
		<p class="min-w-0 flex-1">
			<span class="font-semibold">{$_('demo_data_banner.title')}</span>
			{#if !collapsed}
				{' '}{$_('demo_data_banner.body', { values: { name: $realmName || $_('demo_data_banner.this_realm') } })}
			{/if}
			{' '}<DemoLegalLinks />
		</p>
		<button
			type="button"
			class="flex-shrink-0 rounded-md border border-amber-300 bg-white px-2 py-0.5 text-xs font-medium hover:bg-amber-100"
			aria-expanded={!collapsed}
			onclick={toggle}
		>
			{collapsed ? $_('demo_data_banner.expand') : $_('demo_data_banner.collapse')}
		</button>
	</div>
{/if}
