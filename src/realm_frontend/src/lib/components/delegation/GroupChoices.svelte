<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { Badge } from 'flowbite-svelte';
	import { type Choice } from '$lib/utils/authorizeChoices';

	// The groups of a power of attorney to tick, each in the realm's own words: what it allows, what it risks, and
	// whether some of it asks the member each time. "Everything" is offered only when the caller says so, and is
	// never ticked until the member ticks it.
	let {
		legend,
		choices,
		ticked = $bindable(),
		everything = $bindable(false),
		offerEverything = false
	}: {
		legend: string;
		choices: Choice[];
		ticked: string[];
		everything?: boolean;
		offerEverything?: boolean;
	} = $props();

	// Flowbite's Checkbox ignores `bind:group` unless given `choices`, so these are plain checkboxes that report back.
	const box = 'mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-primary-600 focus:ring-primary-500';

	function tick(id: string, on: boolean) {
		ticked = on ? [...ticked, id] : ticked.filter((other) => other !== id);
	}
</script>

<fieldset class="space-y-3">
	<legend class="mb-2 text-sm font-medium text-gray-900">{legend}</legend>

	{#if offerEverything}
		<label class="flex items-start gap-3 rounded-lg border border-red-300 bg-red-50 p-3">
			<input type="checkbox" class={box} bind:checked={everything} />
			<span>
				<span class="text-sm font-medium text-gray-900">{$_('delegation.groups.everything')}</span>
				<span class="block text-xs text-red-800">{$_('delegation.groups.everything_warning')}</span>
			</span>
		</label>
	{/if}

	{#each choices as choice (choice.id)}
		<label class="flex items-start gap-3">
			<input
				type="checkbox"
				class={box}
				checked={ticked.includes(choice.id)}
				onchange={(e) => tick(choice.id, e.currentTarget.checked)}
				disabled={!choice.held || everything}
			/>
			<span class={choice.held ? 'text-gray-900' : 'text-gray-400'}>
				<span class="text-sm font-medium">{choice.label}</span>
				{#if choice.confirms}
					<Badge color="yellow" class="ms-2">{$_('delegation.groups.asks_each_time')}</Badge>
				{/if}
				<span class="block text-xs {choice.held ? 'text-gray-500' : 'text-gray-400'}">
					{choice.held ? choice.summary : $_('delegation.groups.not_held')}
				</span>
				{#if choice.held && choice.risk}
					<span class="block text-xs text-amber-700">{choice.risk}</span>
				{/if}
			</span>
		</label>
	{/each}
</fieldset>
