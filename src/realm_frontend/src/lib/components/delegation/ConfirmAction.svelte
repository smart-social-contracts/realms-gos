<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { get } from 'svelte/store';
	import { Card, Heading, P, Spinner } from 'flowbite-svelte';
	import { principal } from '$lib/stores/auth';
	import {
		delegationsAsDelegate,
		delegationsAsGrantor,
		isRealmMember,
		loadDelegations
	} from '$lib/stores/delegation.js';
	import { grantLabel, loadAction, type ActionCardData } from '$lib/stores/actions';
	import ActionCard from './ActionCard.svelte';

	// The page where a member decides one pending action. It shows the realm's record of the action and nothing
	// the link carries beyond its id.
	let { id }: { id: string } = $props();

	let loading = $state(true);
	let card = $state<ActionCardData | null>(null);
	let name = $state('');
	let delegateIsMember = $state<boolean | undefined>(undefined);
	let error = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			const [found] = await Promise.all([loadAction(id), loadDelegations()]);
			card = found;
			if (found) {
				name = grantLabel(found, [...get(delegationsAsGrantor), ...get(delegationsAsDelegate)]);
				delegateIsMember = await isRealmMember(found.delegate);
			}
		} catch (e) {
			card = null;
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	// Signing in as another account shows what that account may see.
	$effect(() => {
		if ($principal) load();
	});
</script>

{#if loading}
	<div class="flex justify-center py-10"><Spinner /></div>
{:else if !card}
	<Card size="xl" class="shadow-sm space-y-2">
		<Heading tag="h1" class="text-xl font-bold">{$_('delegation.confirm.not_available')}</Heading>
		<P class="text-sm text-gray-600">{error || $_('delegation.confirm.not_available_text')}</P>
		<P class="text-sm text-gray-600">
			{$_('delegation.action.signed_in_as')} <span class="font-mono break-all">{$principal}</span>
		</P>
	</Card>
{:else}
	<ActionCard {card} {name} viewer={$principal} {delegateIsMember} headingTag="h1" />
	<p class="mt-3 text-center text-xs text-gray-500">
		<a class="underline" href="/delegations">{$_('delegation.confirm.back')}</a>
	</p>
{/if}
