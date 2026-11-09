<script lang="ts">
	import MetaTag from '../../utils/MetaTag.svelte';
	import { _ } from 'svelte-i18n';
	import { Button, Card, Heading, P, Spinner } from 'flowbite-svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { isAuthenticated } from '$lib/stores/auth';
	import {
		acceptDelegation,
		delegationsAsDelegate,
		delegationsAsGrantor,
		loadDelegationGroups,
		loadDelegations,
		pendingDelegations,
		revokeAllDelegations,
		revokeDelegation,
		setActingOnBehalfOf
	} from '$lib/stores/delegation.js';
	import { scopeLabels, type Group, type Scope } from '$lib/utils/authorizeChoices';
	import { fmtTime } from '$lib/utils/dates';
	import GrantActivity from '$lib/components/delegation/GrantActivity.svelte';
	import GrantForm from '$lib/components/delegation/GrantForm.svelte';
	import WaitingForYou from '$lib/components/delegation/WaitingForYou.svelte';

	const path = '/delegations';

	// The address can prefill the manual grant: ?delegate=…&name=…&description=…&groups=vote,propose&hours=…
	const query = $page.url.searchParams;
	const prefill = {
		delegate: query.get('delegate') || '',
		name: query.get('name') || '',
		description: query.get('description') || '',
		groups: (query.get('groups') || '').split(',').map((id) => id.trim()),
		hours: Number(query.get('hours')) || 0
	};

	let loading = $state(true);
	let message = $state('');
	let error = $state('');
	let groups = $state<Group[]>([]);
	let activityOf = $state('');

	const open = (d: { status: string }) => d.status === 'active' || d.status === 'pending';
	const activeToMe = $derived($delegationsAsDelegate.filter((d) => d.status === 'active'));
	const activityGrant = $derived($delegationsAsGrantor.find((d) => d.id === activityOf));
	const everything = $derived($_('delegation.groups.everything'));

	onMount(async () => {
		if (!$isAuthenticated) {
			loading = false;
			return;
		}
		try {
			groups = await loadDelegationGroups();
		} catch (e) {
			error = e instanceof Error ? e.message : $_('delegation.page.failed');
		}
		await refresh();
		const acceptId = query.get('accept');
		if (acceptId) await accept(acceptId);
	});

	async function refresh() {
		loading = true;
		await loadDelegations();
		loading = false;
	}

	async function change(done: string, act: () => Promise<{ success: boolean; error?: string }>) {
		error = '';
		message = '';
		try {
			const res = await act();
			if (res.success) {
				message = done;
				await refresh();
			} else {
				error = res.error || $_('delegation.page.failed');
			}
		} catch (e) {
			error = e instanceof Error ? e.message : $_('delegation.page.failed');
		}
	}

	const accept = (id: string) => change($_('delegation.page.accepted'), () => acceptDelegation(id));
	const revoke = (id: string) => change($_('delegation.page.revoked'), () => revokeDelegation(id));

	async function revokeAll() {
		error = '';
		message = '';
		const res = await revokeAllDelegations().catch((e: Error) => ({ success: false, error: e.message, data: undefined }));
		if (!res.success) {
			error = res.error || $_('delegation.page.failed');
			return;
		}
		message = $_('delegation.page.revoked_all', { values: { count: res.data?.revoked } });
		await refresh();
	}

	function granted(pending: boolean) {
		error = '';
		message = pending ? $_('delegation.page.granted_pending') : $_('delegation.page.granted');
		refresh();
	}

	function statusText(d: { status: string; starts_at?: number }) {
		if (d.status === 'active' && d.starts_at && d.starts_at > Date.now() / 1000) return $_('delegation.page.scheduled');
		return d.status;
	}

	const labels = (scope: Scope) => scopeLabels(scope, groups, everything);
	const expiry = (ts: number) => (ts ? fmtTime(ts) : $_('delegation.page.no_expiry'));
	const period = (d: { starts_at?: number; expires_at: number }) =>
		(d.starts_at ? $_('delegation.page.from', { values: { when: fmtTime(d.starts_at) } }) + ' · ' : '') +
		$_('delegation.page.expires', { values: { when: expiry(d.expires_at) } });
</script>

{#snippet chips(scope: Scope)}
	<p class="mt-1 flex flex-wrap gap-1">
		{#each labels(scope) as label}
			<span class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">{label}</span>
		{/each}
	</p>
{/snippet}

<MetaTag {path} title={$_('delegation.page.title')} description={$_('delegation.page.description')} />

<div class="p-4 max-w-4xl mx-auto space-y-6">
	<Heading tag="h1" class="text-2xl font-bold">{$_('delegation.page.title')}</Heading>
	<P class="text-gray-600">{$_('delegation.page.intro')}</P>

	{#if message}<p class="text-green-700 text-sm">{message}</p>{/if}
	{#if error}<p class="text-red-600 text-sm" role="alert">{error}</p>{/if}

	{#if loading}
		<Spinner />
	{:else if !$isAuthenticated}
		<P>{$_('delegation.page.sign_in')}</P>
	{:else}
		<WaitingForYou />

		<Card size="xl" class="shadow-sm">
			<div class="mb-3 flex flex-wrap items-center justify-between gap-2">
				<Heading tag="h2" class="text-lg">{$_('delegation.grants.title')}</Heading>
				{#if $delegationsAsGrantor.some(open)}
					<Button size="sm" color="light" onclick={revokeAll}>{$_('delegation.grants.revoke_all')}</Button>
				{/if}
			</div>
			{#if $delegationsAsGrantor.length === 0}
				<P class="text-gray-500 text-sm">{$_('delegation.grants.none')}</P>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each $delegationsAsGrantor as d (d.id)}
						<li class="py-3 flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-medium">{d.label || d.delegate}</p>
								{#if d.description}<p class="text-sm text-gray-600">{d.description}</p>{/if}
								<p class="text-xs text-gray-500 font-mono break-all">→ {d.delegate}</p>
								{@render chips(d.scope)}
								<p class="text-xs text-gray-500">{$_('delegation.page.status')}: {statusText(d)} · {period(d)}</p>
							</div>
							<div class="flex gap-2">
								<a
									class="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-900 hover:bg-gray-100"
									href="#poa-activity"
									onclick={() => (activityOf = d.id)}
								>
									{$_('delegation.grants.activity')}
								</a>
								{#if open(d)}
									<Button size="sm" color="light" onclick={() => revoke(d.id)}>{$_('delegation.grants.revoke')}</Button>
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</Card>

		<div id="poa-activity">
			{#if activityGrant}
				<GrantActivity grantId={activityGrant.id} name={activityGrant.label || activityGrant.delegate} />
			{:else}
				<Card size="xl" class="shadow-sm">
					<Heading tag="h2" class="text-lg mb-3">{$_('delegation.activity.heading')}</Heading>
					<P class="text-gray-500 text-sm">{$_('delegation.activity.choose')}</P>
				</Card>
			{/if}
		</div>

		<Card size="xl" class="shadow-sm">
			<Heading tag="h2" class="text-lg mb-3">{$_('delegation.page.pending_title')}</Heading>
			{#if $pendingDelegations.length === 0}
				<P class="text-gray-500 text-sm">{$_('delegation.page.pending_none')}</P>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each $pendingDelegations as d (d.id)}
						<li class="py-3 flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-medium">{d.label || d.grantor}</p>
								{#if d.description}<p class="text-sm text-gray-600">{d.description}</p>{/if}
								{@render chips(d.scope)}
								<p class="text-xs text-gray-500">{period(d)}</p>
							</div>
							<div class="flex gap-2">
								<Button size="sm" onclick={() => accept(d.id)}>{$_('delegation.page.accept')}</Button>
								<Button size="sm" color="light" onclick={() => revoke(d.id)}>{$_('delegation.page.decline')}</Button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</Card>

		<Card size="xl" class="shadow-sm">
			<Heading tag="h2" class="text-lg mb-3">{$_('delegation.page.to_me_title')}</Heading>
			{#if activeToMe.length === 0}
				<P class="text-gray-500 text-sm">{$_('delegation.page.to_me_none')}</P>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each activeToMe as d (d.id)}
						<li class="py-3 flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-medium">{d.label || d.grantor}</p>
								{#if d.description}<p class="text-sm text-gray-600">{d.description}</p>{/if}
								{@render chips(d.scope)}
								<p class="text-xs text-gray-500">{period(d)}</p>
							</div>
							<div class="flex gap-2">
								<Button size="sm" color="alternative" onclick={() => setActingOnBehalfOf(d.grantor)}>
									{$_('delegation.page.act_as')}
								</Button>
								<Button size="sm" color="light" onclick={() => revoke(d.id)}>{$_('delegation.grants.revoke')}</Button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</Card>

		<GrantForm {groups} {prefill} ongranted={granted} />
	{/if}
</div>
