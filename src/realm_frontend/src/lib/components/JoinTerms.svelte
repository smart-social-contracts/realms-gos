<script lang="ts">
	import { _ } from 'svelte-i18n';

	interface Props {
		accepted?: boolean;
		checkboxId?: string;
	}

	let { accepted = $bindable(false), checkboxId = 'join-terms-agree' }: Props = $props();

	// Heroicons outline paths, one per statement below.
	const statements = [
		{
			key: 'join.terms_smart_contracts',
			icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z'
		},
		{
			key: 'join.terms_ownership',
			icon: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z'
		},
		{
			key: 'join.terms_governance',
			icon: 'M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3'
		},
		{
			key: 'join.terms_on_chain',
			icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1'
		}
	];
</script>

<h2 class="text-2xl font-bold text-gray-900 mb-2">{$_('join.terms_title')}</h2>
<p class="text-gray-500 mb-6">{$_('join.terms_subtitle')}</p>

<div class="space-y-4 mb-6">
	{#each statements as statement (statement.key)}
		<div class="flex gap-3 p-3 bg-gray-50 rounded-lg items-start">
			<svg class="w-5 h-5 text-gray-500 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={statement.icon} />
			</svg>
			<p class="text-sm text-gray-700">{$_(statement.key)}</p>
		</div>
	{/each}
</div>

<label
	class="flex items-center gap-3 p-4 border border-gray-200 rounded-xl cursor-pointer hover:bg-gray-50 transition-colors mb-6"
	for={checkboxId}
>
	<input
		id={checkboxId}
		type="checkbox"
		bind:checked={accepted}
		class="w-5 h-5 rounded border-gray-300 text-gray-900 focus:ring-gray-900"
	/>
	<span class="text-sm font-medium text-gray-700">{$_('join.terms_agree')}</span>
</label>
