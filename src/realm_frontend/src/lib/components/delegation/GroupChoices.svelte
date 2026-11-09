<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { Badge, Checkbox } from 'flowbite-svelte';
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

	// Flowbite's Checkbox only follows `bind:group` when given `choices`, so each box is told its state and reports back.
	function tick(id: string, on: boolean) {
		ticked = on ? [...ticked, id] : ticked.filter((other) => other !== id);
	}
</script>

<fieldset class="space-y-3">
	<legend class="mb-2 text-sm font-medium text-gray-900">{legend}</legend>

	{#if offerEverything}
		<div class="rounded-lg border border-red-300 bg-red-50 p-3">
			<Checkbox bind:checked={everything}>
				<span>
					<span class="font-medium">{$_('delegation.groups.everything')}</span>
					<span class="block text-xs text-red-800">{$_('delegation.groups.everything_warning')}</span>
				</span>
			</Checkbox>
		</div>
	{/if}

	{#each choices as choice (choice.id)}
		<Checkbox
			checked={ticked.includes(choice.id)}
			onchange={(e: Event) => tick(choice.id, (e.currentTarget as HTMLInputElement).checked)}
			disabled={!choice.held || everything}
		>
			<span class={choice.held ? '' : 'text-gray-400'}>
				<span class="font-medium">{choice.label}</span>
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
		</Checkbox>
	{/each}
</fieldset>
