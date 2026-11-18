<script lang="ts">
	import { resolveDemoNoticeView, type DemoNoticeBodies } from '$lib/config/hostTestFlags';
	import { localeLabel } from '$lib/i18n/realmLocales';

	interface Props {
		bodies?: DemoNoticeBodies | null;
		primaryLanguage?: string;
		accepted?: boolean;
		checkboxId?: string;
	}

	let {
		bodies = {},
		primaryLanguage = 'en',
		accepted = $bindable(false),
		checkboxId = 'demo-notice-understand'
	}: Props = $props();

	const view = $derived(resolveDemoNoticeView(bodies, primaryLanguage));
</script>

<div class="demo-notice">
	{#if view.showPrimary}
		<h2 class="text-2xl font-bold text-gray-900 mb-2">Aviso</h2>
		<p class="text-xs uppercase tracking-wide text-gray-500 mb-3">
			{localeLabel(view.primaryLanguage)}
		</p>
		<div class="demo-notice__body" lang={view.primaryLanguage}>{view.primary}</div>
		<h3 class="text-lg font-semibold text-gray-900 mt-6 mb-2">Notice</h3>
		<p class="text-xs uppercase tracking-wide text-gray-500 mb-3">English</p>
	{:else}
		<h2 class="text-2xl font-bold text-gray-900 mb-2">Notice</h2>
	{/if}
	<div class="demo-notice__body" lang="en">{view.english}</div>

	<label
		class="flex items-center gap-3 p-4 border border-gray-200 rounded-xl cursor-pointer hover:bg-gray-50 transition-colors mt-6"
		for={checkboxId}
	>
		<input
			id={checkboxId}
			type="checkbox"
			bind:checked={accepted}
			class="w-5 h-5 rounded border-gray-300 text-gray-900 focus:ring-gray-900"
		/>
		<span class="text-sm font-medium text-gray-700">I understand this notice</span>
	</label>
</div>

<style>
	.demo-notice__body {
		white-space: pre-wrap;
		font-size: 0.875rem;
		line-height: 1.55;
		color: #374151;
		background: #f9fafb;
		border: 1px solid #e5e7eb;
		border-radius: 0.75rem;
		padding: 1rem;
	}
</style>
