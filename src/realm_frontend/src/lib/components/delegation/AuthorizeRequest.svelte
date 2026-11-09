<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { Button, Card, Heading, Input, Label, P, Spinner } from 'flowbite-svelte';
	import { styles, cn } from '$lib/theme/utilities';
	import { principal } from '$lib/stores/auth';
	import {
		approveDelegationRequest,
		declineDelegationRequest,
		loadDelegationRequest
	} from '$lib/stores/delegation.js';
	import { asksEverything, decide, expiryError, type Choice, type Scope } from '$lib/utils/authorizeChoices';
	import { fmtTime, toLocalInput } from '$lib/utils/dates';
	import GroupChoices from './GroupChoices.svelte';

	type Request = {
		id: string;
		delegate: string;
		delegate_is_member: boolean;
		label: string;
		description: string;
		scope: Scope;
		choices: Choice[];
		starts_at: number;
		expires_at: number;
		request_expires_at: number;
	};

	// A request to act for the signed-in member, to be answered with less than it asks or declined, never more.
	let { requestId }: { requestId: string } = $props();

	let loading = $state(true);
	let error = $state('');
	let request = $state<Request | null>(null);
	let ticked = $state<string[]>([]);
	let everything = $state(false);
	let expiresInput = $state('');
	let busy = $state(false);
	let outcome = $state<'approved' | 'declined' | ''>('');

	const name = $derived(request?.label || $_('delegation.action.unnamed'));
	const holdsAny = $derived((request?.choices ?? []).some((c) => c.held));
	const toEpoch = (input: string) => Math.floor(new Date(input).getTime() / 1000);

	async function load() {
		loading = true;
		error = '';
		try {
			const res = await loadDelegationRequest(requestId);
			if (!res?.success) {
				request = null;
				error = res?.error || $_('delegation.authorize.could_not_load');
				return;
			}
			request = res.data as Request;
			// Asked for everything, the member starts from nothing: ticking all of it is their choice to make.
			ticked = asksEverything(request.scope) ? [] : request.choices.filter((c) => c.held).map((c) => c.id);
			everything = false;
			expiresInput = toLocalInput(new Date(request.expires_at * 1000));
		} catch (e) {
			error = e instanceof Error ? e.message : $_('delegation.authorize.could_not_load');
		} finally {
			loading = false;
		}
	}

	// Signing in as another account shows what that account may see.
	$effect(() => {
		if ($principal) load();
	});

	async function authorize() {
		if (!request) return;
		error = '';
		const expiresAt = toEpoch(expiresInput);
		const late = expiryError(request, expiresAt, Math.floor(Date.now() / 1000));
		if (late) {
			error = $_(`delegation.authorize.error.${late}`, { values: { when: fmtTime(request.expires_at) } });
			return;
		}
		const decision = decide(request.scope, request.choices, ticked, everything);
		if (!decision.ok) {
			error = $_(`delegation.authorize.error.${decision.error}`);
			return;
		}
		busy = true;
		try {
			const res = await approveDelegationRequest(request.id, {
				scope: decision.scope,
				expiresAt: expiresAt === request.expires_at ? undefined : expiresAt
			});
			if (res?.success) outcome = 'approved';
			else error = res?.error || $_('delegation.authorize.failed');
		} catch (e) {
			error = e instanceof Error ? e.message : $_('delegation.authorize.failed');
		} finally {
			busy = false;
		}
	}

	async function cancel() {
		if (!request) return;
		error = '';
		busy = true;
		try {
			const res = await declineDelegationRequest(request.id);
			if (res?.success) outcome = 'declined';
			else error = res?.error || $_('delegation.authorize.could_not_decline');
		} catch (e) {
			error = e instanceof Error ? e.message : $_('delegation.authorize.could_not_decline');
		} finally {
			busy = false;
		}
	}
</script>

{#if loading}
	<div class="flex justify-center py-10"><Spinner /></div>
{:else if outcome === 'approved' && request}
	<Card size="xl" class="shadow-sm space-y-2">
		<Heading tag="h1" class="text-xl font-bold">{$_('delegation.authorize.authorized')}</Heading>
		<P class="text-sm">
			{$_('delegation.authorize.authorized_text', {
				values: { name, until: fmtTime(toEpoch(expiresInput)) }
			})}
		</P>
		<P class="text-sm text-gray-600">
			{$_('delegation.authorize.revoke_hint')}
			<a class="underline" href="/delegations">{$_('delegation.page.title')}</a>.
		</P>
	</Card>
{:else if outcome === 'declined'}
	<Card size="xl" class="shadow-sm">
		<Heading tag="h1" class="text-xl font-bold mb-2">{$_('delegation.authorize.declined')}</Heading>
		<P class="text-sm text-gray-600">{$_('delegation.authorize.declined_text')}</P>
	</Card>
{:else if !request}
	<Card size="xl" class="shadow-sm space-y-2">
		<Heading tag="h1" class="text-xl font-bold">{$_('delegation.authorize.not_available')}</Heading>
		<P class="text-sm text-gray-600">{error}</P>
		<P class="text-sm text-gray-600">
			{$_('delegation.authorize.not_available_text')}
			{$_('delegation.action.signed_in_as')} <span class="font-mono break-all">{$principal}</span>.
		</P>
	</Card>
{:else}
	<Card size="xl" class="shadow-sm space-y-5">
		<div class="text-center space-y-1">
			<Heading tag="h1" class="text-xl font-bold">{$_('delegation.authorize.heading', { values: { name } })}</Heading>
			<P class="text-xs text-gray-500">{$_('delegation.action.name_unverified')}</P>
		</div>

		<div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
			<p class="text-xs font-medium text-gray-500 mb-1">{$_('delegation.authorize.requested_by')}</p>
			<p class="font-mono text-sm break-all">{request.delegate}</p>
			<p class="text-xs text-gray-500 mt-1">
				{request.delegate_is_member ? $_('delegation.action.is_member') : $_('delegation.action.is_ai')}
				{$_('delegation.authorize.recognise')}
			</p>
		</div>

		{#if request.description}
			<div>
				<p class="text-xs font-medium text-gray-500 mb-1">{$_('delegation.authorize.says_it_is_for')}</p>
				<p class="text-sm whitespace-pre-line">{request.description}</p>
			</div>
		{/if}

		<GroupChoices
			legend={$_('delegation.authorize.wants_to')}
			choices={request.choices}
			bind:ticked
			bind:everything
			offerEverything={asksEverything(request.scope)}
		/>
		<p class="text-xs text-gray-500">{$_('delegation.authorize.untick')}</p>

		<div>
			<Label for="poa-authorize-expires" class="mb-1">{$_('delegation.authorize.until')}</Label>
			<Input
				id="poa-authorize-expires"
				type="datetime-local"
				bind:value={expiresInput}
				max={toLocalInput(new Date(request.expires_at * 1000))}
			/>
			<p class="mt-1 text-xs text-gray-500">
				{request.starts_at
					? $_('delegation.authorize.starts', { values: { when: fmtTime(request.starts_at) } })
					: $_('delegation.authorize.starts_now')}
				{$_('delegation.authorize.sooner_not_later')}
			</p>
		</div>

		<div class="rounded-lg border border-gray-200 p-3 text-xs text-gray-600 space-y-1">
			<p>{$_('delegation.action.signed_in_as')} <span class="font-mono break-all">{$principal}</span></p>
			<p>{$_('delegation.authorize.recorded')}</p>
			<p>{$_('delegation.authorize.request_expires', { values: { when: fmtTime(request.request_expires_at) } })}</p>
			<p class="font-medium">{$_('delegation.authorize.never')}</p>
		</div>

		{#if error}<p class="text-red-600 text-sm" role="alert">{error}</p>{/if}

		<div class="flex gap-3">
			<Button color="light" class="flex-1" disabled={busy} onclick={cancel}>
				{$_('delegation.authorize.cancel')}
			</Button>
			<Button
				color="none"
				class={cn(styles.button.primary(), 'flex-1 disabled:opacity-50')}
				disabled={busy || !holdsAny || (!ticked.length && !everything)}
				onclick={authorize}
			>
				{#if busy}<Spinner size="4" class="me-2" />{/if}
				{$_('delegation.authorize.authorize')}
			</Button>
		</div>
		{#if !holdsAny}
			<p class="text-xs text-gray-500 text-center">{$_('delegation.authorize.holds_none')}</p>
		{/if}
	</Card>
{/if}
