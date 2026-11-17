<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { Button, Card, Heading, Input, Label, P, Spinner, Textarea } from 'flowbite-svelte';
	import { styles, cn } from '$lib/theme/utilities';
	import { grantDelegation } from '$lib/stores/delegation.js';
	import { choiceOf, type Group } from '$lib/utils/authorizeChoices';
	import { toLocalInput } from '$lib/utils/dates';
	import GroupChoices from './GroupChoices.svelte';

	const DAY = 86400;
	const SUGGESTED_DAYS = 30;

	// The manual grant: the member picks the groups. "Everything" is never offered here; it is granted only by
	// answering a request that asks for it.
	let {
		groups,
		prefill,
		ongranted
	}: {
		groups: Group[];
		prefill: { delegate: string; name: string; description: string; groups: string[]; hours: number };
		ongranted: (pending: boolean) => void;
	} = $props();

	const choices = $derived(groups.map(choiceOf));
	const now = new Date();

	let delegate = $state(prefill.delegate);
	let name = $state(prefill.name);
	let description = $state(prefill.description);
	let ticked = $state<string[]>(groups.map((g) => g.id).filter((id) => prefill.groups.includes(id)));
	let starts = $state(toLocalInput(now));
	let expires = $state(toLocalInput(new Date(now.getTime() + (prefill.hours || SUGGESTED_DAYS * 24) * 3600 * 1000)));
	let granting = $state(false);
	let error = $state('');

	// A grant lasts as long as the shortest of its groups allows.
	const longestDays = $derived(
		ticked.length ? Math.min(...groups.filter((g) => ticked.includes(g.id)).map((g) => g.longest)) / DAY : 0
	);

	async function submit() {
		error = '';
		const startsAt = Math.floor(new Date(starts).getTime() / 1000);
		const expiresAt = Math.floor(new Date(expires).getTime() / 1000);
		if (!delegate.trim()) {
			error = $_('delegation.form.error.no_delegate');
			return;
		}
		if (!ticked.length) {
			error = $_('delegation.form.error.no_group');
			return;
		}
		if (!expiresAt || expiresAt <= startsAt) {
			error = $_('delegation.form.error.expiry');
			return;
		}
		granting = true;
		try {
			const res = await grantDelegation({
				delegate: delegate.trim(),
				groups: ticked,
				label: name.trim(),
				description: description.trim(),
				startsAt: startsAt <= Math.floor(Date.now() / 1000) ? 0 : startsAt,
				expiresAt
			});
			if (res?.success) {
				ongranted(res.data?.status === 'pending');
				delegate = name = description = '';
				ticked = [];
			} else {
				error = res?.error || $_('delegation.form.error.failed');
			}
		} catch (e) {
			error = e instanceof Error ? e.message : $_('delegation.form.error.failed');
		} finally {
			granting = false;
		}
	}
</script>

<Card size="xl" class="shadow-sm">
	<Heading tag="h2" class="text-lg mb-3">{$_('delegation.form.title')}</Heading>
	{#if choices.length === 0}
		<P class="text-gray-500 text-sm">{$_('delegation.form.nothing')}</P>
	{:else}
		<form class="space-y-4" onsubmit={(e) => { e.preventDefault(); submit(); }}>
			<div>
				<Label for="poa-delegate" class="mb-1">{$_('delegation.form.delegate')}</Label>
				<Input id="poa-delegate" bind:value={delegate} placeholder="xxxxx-xxxxx-…" class="font-mono text-sm" />
				<p class="mt-1 text-xs text-gray-500">{$_('delegation.form.delegate_hint')}</p>
			</div>
			<div>
				<Label for="poa-name" class="mb-1">{$_('delegation.form.name')}</Label>
				<Input id="poa-name" bind:value={name} placeholder={$_('delegation.form.name_placeholder')} maxlength={256} />
			</div>
			<div>
				<Label for="poa-description" class="mb-1">{$_('delegation.form.description')}</Label>
				<Textarea
					id="poa-description"
					bind:value={description}
					rows={2}
					maxlength={2048}
					placeholder={$_('delegation.form.description_placeholder')}
				/>
			</div>
			<GroupChoices legend={$_('delegation.form.groups')} {choices} bind:ticked />
			<div class="grid gap-4 sm:grid-cols-2">
				<div>
					<Label for="poa-starts" class="mb-1">{$_('delegation.form.starts')}</Label>
					<Input id="poa-starts" type="datetime-local" bind:value={starts} />
				</div>
				<div>
					<Label for="poa-expires" class="mb-1">{$_('delegation.form.expires')}</Label>
					<Input id="poa-expires" type="datetime-local" bind:value={expires} />
				</div>
			</div>
			{#if longestDays}
				<p class="text-xs text-gray-500">{$_('delegation.form.longest', { values: { days: longestDays } })}</p>
			{/if}
			{#if error}<p class="text-red-600 text-sm" role="alert">{error}</p>{/if}
			<Button type="submit" color="none" class={cn(styles.button.primary(), 'disabled:opacity-50')} disabled={granting}>
				{#if granting}<Spinner size="4" class="me-2" />{/if}
				{$_('delegation.form.grant')}
			</Button>
		</form>
	{/if}
</Card>
