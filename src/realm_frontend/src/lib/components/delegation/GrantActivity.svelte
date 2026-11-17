<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { Button, Card, Heading, P, Spinner } from 'flowbite-svelte';
	import type { Reply } from '$lib/realm';
	import { loadDelegationActivity } from '$lib/stores/delegation.js';
	import { describeEvent, type ActivityEvent } from '$lib/utils/activity';
	import { fmtTime } from '$lib/utils/dates';

	// What the delegate of one grant did, newest first. The realm's audit log keeps its last events only.
	let { grantId, name }: { grantId: string; name: string } = $props();

	let loading = $state(true);
	let error = $state('');
	let events = $state<ActivityEvent[]>([]);
	let since = $state(0);
	let next = $state<number | null>(null);

	async function load(from?: number) {
		loading = true;
		error = '';
		const reply = await loadDelegationActivity(grantId, from).catch((e: Error): Reply => ({ ok: false, error: e.message }));
		if (reply.ok) {
			events = from ? [...events, ...reply.events] : reply.events;
			since = reply.since;
			next = reply.next;
		} else {
			error = reply.error;
		}
		loading = false;
	}

	// Another grant chosen reads its events from the start.
	$effect(() => {
		load();
	});
</script>

<Card size="xl" class="shadow-sm">
	<Heading tag="h2" class="text-lg mb-3">{$_('delegation.activity.title', { values: { name } })}</Heading>
	{#if error}<p class="text-red-600 text-sm" role="alert">{error}</p>{/if}
	{#if !events.length && !loading}
		<P class="text-gray-500 text-sm">{$_('delegation.activity.none')}</P>
	{:else}
		<ul class="divide-y divide-gray-100">
			{#each events as event (event.id)}
				{@const row = describeEvent(event)}
				<li class="py-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
					<span class="font-mono">{row.act}</span>
					<span class="text-gray-600">
						{$_(`delegation.activity.outcome.${row.outcome}`)}
						{#if row.href}<a class="underline" href={row.href}>{row.reference}</a>{:else if row.reference}{row.reference}{/if}
					</span>
					<span class="text-xs text-gray-500">{fmtTime(event.at)}</span>
				</li>
			{/each}
		</ul>
	{/if}
	{#if loading}<Spinner class="mt-2" />{/if}
	{#if next && !loading}
		<Button size="sm" color="light" class="mt-3" onclick={() => load(next ?? undefined)}>
			{$_('delegation.activity.older')}
		</Button>
	{/if}
	{#if since}
		<p class="mt-3 text-xs text-gray-500">{$_('delegation.activity.reaches_back', { values: { when: fmtTime(since) } })}</p>
	{/if}
</Card>
