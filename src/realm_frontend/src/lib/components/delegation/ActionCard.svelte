<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { Button, Card, Spinner } from 'flowbite-svelte';
	import { styles, cn } from '$lib/theme/utilities';
	import {
		approveAction,
		declineAction,
		loadAction,
		argumentLines,
		realmLines,
		nextStep,
		madeBy,
		failure,
		showUnseen,
		type ActionCardData
	} from '$lib/stores/actions';
	import { memberNickname } from '$lib/stores/delegation.js';
	import { fmtTime } from '$lib/utils/dates';
	import { framedByStranger } from '$lib/utils/framedByStranger';
	import { shortPrincipal } from '$lib/utils/principals';

	// One pending action as its member reads it: the realm's own words about the call, apart from the delegate's.
	// Nothing here can be edited. Approve sends the id and the hash of the record shown, and nothing else. The delegate
	// who opens it reads the same record in words written for them, with no way to decide.
	let {
		card,
		name,
		viewer,
		headingTag = 'h2'
	}: {
		card: ActionCardData;
		/** What the member called the grant it was prepared under; empty when they gave it no name. */
		name: string;
		/** The signed-in principal. Only the action's own member decides it, and they read it as the member. */
		viewer: string;
		headingTag?: 'h1' | 'h2';
	} = $props();

	// What the realm answered to a decision replaces the card; until then the card is the one given.
	let decided = $state<ActionCardData | null>(null);
	let busy = $state(false);
	let error = $state('');

	// Inside another site's frame that site could cover or click the buttons: the card then offers no decision. Pages
	// that refuse the frame as a whole (ConsentShell) never get this far; the Power of attorney page shows cards beside
	// its grants, so each card checks.
	const framed = framedByStranger();

	const current = $derived(decided ?? card);
	const pending = $derived(current.status === 'pending');
	const side = $derived(viewer === current.grantor ? 'member' : 'delegate');
	const who = $derived(name || $_('delegation.action.unnamed'));
	const canDecide = $derived(pending && side === 'member' && !framed);

	// The delegate reads who decides: the member's nickname, else their principal shortened.
	let nickname = $state('');
	$effect(() => {
		nickname = '';
		if (side === 'delegate') memberNickname(current.grantor).then((found: string) => (nickname = found));
	});
	const member = $derived(nickname || shortPrincipal(current.grantor));

	/** A sentence of the card written for whoever reads it: the member, or the delegate who prepared it. */
	const say = (key: string, values: { when?: string } = {}) =>
		$_(`delegation.action.${side}.${key}`, { values: { name: who, member, ...values } });
	const proposalLink = (field: string, value: string) => (field === 'proposal' ? `/proposal/${value}` : '');
	const outcomeColour = (status: string) =>
		status === 'done'
			? 'border-green-300 bg-green-50 text-green-900'
			: status === 'failed'
				? 'border-red-300 bg-red-50 text-red-900'
				: 'border-blue-200 bg-blue-50 text-blue-900';

	async function decide(approve: boolean) {
		busy = true;
		error = '';
		try {
			decided = await (approve ? approveAction(current) : declineAction(current));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			// The realm may have moved the action on (its day passed, its grant ended): show it as it stands.
			decided = (await loadAction(current.id).catch(() => null)) ?? decided;
		} finally {
			busy = false;
		}
	}
</script>

<Card size="xl" class="shadow-sm space-y-4">
	<div class="space-y-1">
		<svelte:element this={headingTag} class="text-xl font-bold text-gray-900">
			{say(pending ? 'asks' : 'prepared')}
		</svelte:element>
		{#if side === 'member'}
			<p class="text-xs text-gray-500">{$_('delegation.action.name_unverified')}</p>
		{/if}
	</div>

	{#if side === 'member'}
		<div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
			<p class="text-xs font-medium text-gray-500 mb-1">{$_('delegation.action.principal')}</p>
			<p class="font-mono text-sm break-all">{current.delegate}</p>
			<p class="text-xs text-gray-500 mt-1">
				{current.delegate_is_member ? $_('delegation.action.is_member') : $_('delegation.action.is_ai')}
			</p>
		</div>
	{/if}

	{#if !pending}
		<div class="rounded-lg border p-3 text-sm space-y-1 {outcomeColour(current.status)}" role="status">
			<p class="font-medium">{$_(`delegation.action.state.${current.status}`)}</p>
			{#if current.status === 'failed'}
				<p>{failure(current) || $_('delegation.action.failed_no_reason')}</p>
			{:else if current.status === 'done'}
				{#each madeBy(current) as [field, value]}
					<p class="text-xs">
						{field}:
						{#if proposalLink(field, value)}
							<a class="underline" href={proposalLink(field, value)}>{value}</a>
						{:else}
							<span class="font-mono break-all">{value}</span>
						{/if}
					</p>
				{/each}
			{/if}
		</div>
	{:else if side === 'delegate'}
		<div class="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900" role="status">
			{$_('delegation.action.only_the_member')}
		</div>
	{:else if framed}
		<div class="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900" role="status">
			{$_('delegation.consent.framed_text')}
		</div>
	{/if}

	<div class="space-y-1">
		<h3 class="text-lg font-semibold text-gray-900">{current.title || current.method}</h3>
		<p class="font-mono text-xs text-gray-500">{current.method}</p>
	</div>

	{#if argumentLines(current).length}
		<div class="rounded-lg border border-dashed border-gray-300 p-3">
			<p class="text-xs font-medium text-gray-500 mb-1">{say('arguments')}</p>
			<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs">
				{#each argumentLines(current) as line}
					<dt class="text-gray-500">{line.label}</dt>
					<dd class="break-all">{line.value}</dd>
				{/each}
			</dl>
		</div>
	{/if}

	{#each realmLines(current) as line}
		<div>
			<p class="text-xs font-medium text-gray-500">{line.label}</p>
			<p class="text-sm break-words">{line.value}</p>
		</div>
	{/each}

	{#if current.risk}
		<div class="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
			<span class="font-medium">{$_('delegation.action.risk')}</span>
			{current.risk}
		</div>
	{/if}

	{#if nextStep(current)}
		<div>
			<p class="text-xs font-medium text-gray-500">{$_('delegation.action.next')}</p>
			<p class="text-sm break-words">{nextStep(current)}</p>
		</div>
	{/if}

	{#if current.note}
		<figure class="border-l-4 border-gray-300 bg-gray-50 px-3 py-2">
			<figcaption class="text-xs font-medium text-gray-500">
				{say('note')}
			</figcaption>
			<blockquote dir="auto" class="text-sm italic whitespace-pre-line break-words">{showUnseen(current.note)}</blockquote>
		</figure>
	{/if}

	<div class="rounded-lg border border-gray-200 p-3 text-xs text-gray-600 space-y-1">
		<p>{$_('delegation.action.grant', { values: { grant: current.grant } })}</p>
		{#if pending}<p>{say('lapses', { when: fmtTime(current.expires_at) })}</p>{/if}
		<p>{$_('delegation.action.signed_in_as')} <span class="font-mono break-all">{viewer}</span></p>
		<p>{say('recorded')}</p>
	</div>

	{#if error}<p class="text-red-600 text-sm" role="alert">{error}</p>{/if}

	{#if canDecide}
		<div class="flex gap-3">
			<Button color="light" class="flex-1" disabled={busy} onclick={() => decide(false)}>
				{$_('delegation.action.decline')}
			</Button>
			<Button
				color="none"
				class={cn(styles.button.primary(), 'flex-1 disabled:opacity-50')}
				disabled={busy}
				onclick={() => decide(true)}
			>
				{#if busy}<Spinner size="4" class="me-2" />{/if}
				{$_('delegation.action.approve')}
			</Button>
		</div>
	{/if}
</Card>
