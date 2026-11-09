<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { onMount } from 'svelte';
	import { Heading, P, Spinner } from 'flowbite-svelte';
	import { styles, cn } from '$lib/theme/utilities';
	import { principal } from '$lib/stores/auth';
	import { delegationsAsGrantor, requestsToMe } from '$lib/stores/delegation.js';
	import { grantLabel, loadWaiting, type ActionCardData, type NamedGrants } from '$lib/stores/actions';
	import { fmtTime } from '$lib/utils/dates';
	import ActionCard from './ActionCard.svelte';

	// Everything that waits for the member's answer: the actions a delegate prepared, and the requests to act for them.
	let loading = $state(true);
	let error = $state('');
	let waiting = $state<ActionCardData[]>([]);

	onMount(async () => {
		try {
			waiting = await loadWaiting();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	});
</script>

<section class="space-y-3">
	<Heading tag="h2" class="text-lg">{$_('delegation.waiting.title')}</Heading>
	{#if loading}
		<Spinner />
	{:else}
		{#if error}<p class="text-red-600 text-sm" role="alert">{error}</p>{/if}
		{#if !waiting.length && !$requestsToMe.length}
			<P class="text-gray-500 text-sm">{$_('delegation.waiting.nothing')}</P>
		{/if}
		{#each waiting as card (card.id)}
			<ActionCard {card} name={grantLabel(card, $delegationsAsGrantor as NamedGrants)} viewer={$principal} />
		{/each}
		{#each $requestsToMe as request (request.id)}
			<div class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-200 p-3">
				<div>
					<p class="font-medium">
						{$_('delegation.waiting.request', { values: { name: request.label || $_('delegation.action.unnamed') } })}
					</p>
					<p class="font-mono text-xs text-gray-500 break-all">{request.delegate}</p>
					<p class="text-xs text-gray-500">
						{$_('delegation.authorize.request_expires', { values: { when: fmtTime(request.request_expires_at) } })}
					</p>
				</div>
				<a class={cn(styles.button.primary(), 'text-sm')} href={`/delegations/authorize/${request.id}`}>
					{$_('delegation.waiting.review')}
				</a>
			</div>
		{/each}
	{/if}
</section>
