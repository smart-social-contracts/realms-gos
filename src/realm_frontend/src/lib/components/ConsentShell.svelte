<script lang="ts">
	import type { Snippet } from 'svelte';
	import { _ } from 'svelte-i18n';
	import { Card, Heading, P } from 'flowbite-svelte';
	import { isAuthenticated } from '$lib/stores/auth';
	import { framedByStranger } from '$lib/utils/framedByStranger';

	// The frame of every page where a member decides for themselves (an Authorize page, a confirm page): it shows
	// the page's own content only to a signed-in member, and never inside a stranger's frame. Until then nothing of
	// the content exists, so nothing is loaded or run.
	let {
		id,
		signInTitle,
		signInText,
		missingText,
		children
	}: {
		/** The request or action the link names. */
		id: string;
		signInTitle: string;
		signInText: string;
		missingText: string;
		children: Snippet;
	} = $props();

	const refused = framedByStranger();
</script>

<div class="p-4 max-w-xl mx-auto">
	{#if refused}
		<Card size="xl" class="shadow-sm">
			<Heading tag="h1" class="text-xl font-bold mb-2">{$_('delegation.consent.framed_title')}</Heading>
			<P class="text-sm text-gray-600">{$_('delegation.consent.framed_text')}</P>
		</Card>
	{:else if !id}
		<Card size="xl" class="shadow-sm">
			<P>{missingText}</P>
		</Card>
	{:else if !$isAuthenticated}
		<Card size="xl" class="shadow-sm">
			<Heading tag="h1" class="text-xl font-bold mb-2">{signInTitle}</Heading>
			<P class="text-sm text-gray-600">{signInText}</P>
		</Card>
	{:else}
		{@render children()}
	{/if}
</div>
