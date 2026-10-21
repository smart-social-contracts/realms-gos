<script lang="ts">
	import MetaTag from '../../utils/MetaTag.svelte';
	import { Button, Card, Checkbox, Heading, Input, Label, P, Spinner, Textarea } from 'flowbite-svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { styles, cn } from '$lib/theme/utilities';
	import { resolveDelegationBackend } from '$lib/stores/delegation.js';
	import { isAuthenticated, principal } from '$lib/stores/auth';
	import {
		delegationsAsDelegate,
		delegationsAsGrantor,
		pendingDelegations,
		loadDelegations,
		loadDelegableOperations,
		grantDelegation,
		setActingOnBehalfOf
	} from '$lib/stores/delegation.js';

	const path = '/delegations';
	const title = 'Power of attorney';
	const description = 'Let someone, or an AI, act for you within permissions you choose';

	let loading = true;
	let message = '';
	let error = '';

	let delegableOps: { operation: string; label: string; description: string }[] = [];
	let grantDelegate = '';
	let grantName = '';
	let grantDescription = '';
	let grantOps: string[] = [];
	let grantStarts = '';
	let grantExpires = '';
	let granting = false;

	function toLocalInput(date: Date) {
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
	}

	function prefillGrantForm() {
		const q = $page.url.searchParams;
		const hours = Number(q.get('hours')) || 24 * 7;
		const now = new Date();
		grantDelegate = q.get('delegate') || '';
		grantName = q.get('name') || '';
		grantDescription = q.get('description') || '';
		const wanted = (q.get('ops') || '').split(',').map((s) => s.trim());
		grantOps = delegableOps.map((o) => o.operation).filter((op) => wanted.includes(op));
		grantStarts = toLocalInput(now);
		grantExpires = toLocalInput(new Date(now.getTime() + hours * 3600 * 1000));
	}

	onMount(async () => {
		if (!$isAuthenticated) {
			loading = false;
			return;
		}
		await refresh();
		try {
			delegableOps = await loadDelegableOperations();
		} catch (e) {
			console.warn('Failed to load delegable operations:', e);
		}
		prefillGrantForm();
		const acceptId = $page.url.searchParams.get('accept');
		if (acceptId) await acceptOne(acceptId);
	});

	async function submitGrant() {
		error = '';
		message = '';
		const startsAt = Math.floor(new Date(grantStarts).getTime() / 1000);
		const expiresAt = Math.floor(new Date(grantExpires).getTime() / 1000);
		if (!grantDelegate.trim()) {
			error = 'Enter the principal of the person or AI connection you are granting to.';
			return;
		}
		if (!grantOps.length) {
			error = 'Choose at least one permission.';
			return;
		}
		if (!expiresAt || expiresAt <= startsAt) {
			error = 'The expiry must be after the start.';
			return;
		}
		granting = true;
		try {
			const res = await grantDelegation({
				grantor: $principal,
				delegate: grantDelegate.trim(),
				operations: grantOps,
				label: grantName.trim(),
				description: grantDescription.trim(),
				startsAt: startsAt <= Math.floor(Date.now() / 1000) ? 0 : startsAt,
				expiresAt
			});
			if (res?.success) {
				message =
					res.data?.status === 'pending'
						? 'Granted. It takes effect once the member accepts it.'
						: 'Granted.';
				grantDelegate = '';
				grantName = '';
				grantDescription = '';
				grantOps = [];
				await refresh();
			} else {
				error = res?.error || 'Grant failed';
			}
		} catch (e: any) {
			error = e.message || 'Grant failed';
		} finally {
			granting = false;
		}
	}

	function statusText(d: { status: string; starts_at?: number }) {
		if (d.status === 'active' && d.starts_at && d.starts_at > Date.now() / 1000) return 'scheduled';
		return d.status;
	}

	async function refresh() {
		loading = true;
		error = '';
		await loadDelegations();
		loading = false;
	}

	async function acceptOne(delegationId: string) {
		error = '';
		message = '';
		try {
			const actor = await resolveDelegationBackend();
			const raw = await actor.accept_delegation_json(
				JSON.stringify({ delegation_id: delegationId })
			);
			const res = typeof raw === 'string' ? JSON.parse(raw) : raw;
			if (res.success) {
				message = 'Delegation accepted.';
				await refresh();
			} else {
				error = res.error || 'Accept failed';
			}
		} catch (e: any) {
			error = e.message || 'Accept failed';
		}
	}

	async function revokeOne(delegationId: string) {
		error = '';
		message = '';
		try {
			const actor = await resolveDelegationBackend();
			const raw = await actor.revoke_delegation_json(
				JSON.stringify({ delegation_id: delegationId })
			);
			const res = typeof raw === 'string' ? JSON.parse(raw) : raw;
			if (res.success) {
				message = 'Delegation revoked.';
				await refresh();
			} else {
				error = res.error || 'Revoke failed';
			}
		} catch (e: any) {
			error = e.message || 'Revoke failed';
		}
	}

	function scopeSummary(scope: { all?: boolean; operations?: string[] }) {
		if (scope?.all) return 'All operations (admin grantor only)';
		const ops = scope?.operations || [];
		return ops.length ? ops.join(', ') : '—';
	}

	function fmtExpiry(ts: number) {
		if (!ts) return 'No expiry';
		return new Date(ts * 1000).toLocaleString();
	}

	function fmtWindow(d: { starts_at?: number; expires_at: number }) {
		const from = d.starts_at ? `From ${new Date(d.starts_at * 1000).toLocaleString()} · ` : '';
		return `${from}Expires: ${fmtExpiry(d.expires_at)}`;
	}
</script>

<MetaTag {path} {title} {description} />

<div class="p-4 max-w-4xl mx-auto space-y-6">
	<Heading tag="h1" class="text-2xl font-bold">Power of attorney</Heading>
	<P class="text-gray-600">
		Let another member, or an AI assistant you connected, act for you in this realm: only
		with the permissions you tick, only between the start and expiry you set. Every action
		is recorded as done on your behalf, and you can revoke it here at any time. No private
		keys are shared.
	</P>

	{#if message}
		<p class="text-green-700 text-sm">{message}</p>
	{/if}
	{#if error}
		<p class="text-red-600 text-sm">{error}</p>
	{/if}

	{#if loading}
		<Spinner />
	{:else if !$isAuthenticated}
		<P>Sign in to manage delegations.</P>
	{:else}
		<Card size="xl" class="shadow-sm">
			<Heading tag="h2" class="text-lg mb-3">Grant power of attorney</Heading>
			{#if delegableOps.length === 0}
				<P class="text-gray-500 text-sm">
					You hold no permissions in this realm that can be delegated.
				</P>
			{:else}
				<form class="space-y-4" on:submit|preventDefault={submitGrant}>
					<div>
						<Label for="poa-delegate" class="mb-1">Grant to (principal)</Label>
						<Input
							id="poa-delegate"
							bind:value={grantDelegate}
							placeholder="xxxxx-xxxxx-…"
							class="font-mono text-sm"
						/>
						<p class="mt-1 text-xs text-gray-500">
							A member's principal, or the principal your AI assistant shows for its connection.
						</p>
					</div>
					<div>
						<Label for="poa-name" class="mb-1">Name</Label>
						<Input id="poa-name" bind:value={grantName} placeholder="e.g. My Claude" maxlength={256} />
					</div>
					<div>
						<Label for="poa-description" class="mb-1">Description</Label>
						<Textarea
							id="poa-description"
							bind:value={grantDescription}
							rows={2}
							maxlength={2048}
							placeholder="What it is for, e.g. vote on budget proposals while I travel"
						/>
					</div>
					<fieldset>
						<legend class="mb-2 text-sm font-medium text-gray-900">Permissions</legend>
						<div class="space-y-2">
							{#each delegableOps as op (op.operation)}
								<Checkbox bind:group={grantOps} value={op.operation}>
									<span>
										<span class="font-medium">{op.label}</span>
										<span class="block text-xs text-gray-500">{op.description}</span>
									</span>
								</Checkbox>
							{/each}
						</div>
					</fieldset>
					<div class="grid gap-4 sm:grid-cols-2">
						<div>
							<Label for="poa-starts" class="mb-1">Starts</Label>
							<Input id="poa-starts" type="datetime-local" bind:value={grantStarts} />
						</div>
						<div>
							<Label for="poa-expires" class="mb-1">Expires</Label>
							<Input id="poa-expires" type="datetime-local" bind:value={grantExpires} />
						</div>
					</div>
					<Button
						type="submit"
						color="none"
						class={cn(styles.button.primary(), 'disabled:opacity-50')}
						disabled={granting}
					>
						{#if granting}<Spinner size="4" class="me-2" />{/if}
						Grant
					</Button>
				</form>
			{/if}
		</Card>

		<Card size="xl" class="shadow-sm">
			<Heading tag="h2" class="text-lg mb-3">Pending — accept to act on behalf</Heading>
			{#if $pendingDelegations.length === 0}
				<P class="text-gray-500 text-sm">No pending invitations.</P>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each $pendingDelegations as d (d.id)}
						<li class="py-3 flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-medium">{d.label || d.grantor}</p>
								{#if d.description}<p class="text-sm text-gray-600">{d.description}</p>{/if}
								<p class="text-xs text-gray-500">Scope: {scopeSummary(d.scope)}</p>
								<p class="text-xs text-gray-500">{fmtWindow(d)}</p>
							</div>
							<div class="flex gap-2">
								<Button size="sm" onclick={() => acceptOne(d.id)}>Accept</Button>
								<Button size="sm" color="light" onclick={() => revokeOne(d.id)}>Decline</Button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</Card>

		<Card size="xl" class="shadow-sm">
			<Heading tag="h2" class="text-lg mb-3">Granted to me (active)</Heading>
			{#if $delegationsAsDelegate.filter((d) => d.status === 'active').length === 0}
				<P class="text-gray-500 text-sm">None active.</P>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each $delegationsAsDelegate.filter((d) => d.status === 'active') as d (d.id)}
						<li class="py-3 flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-medium">{d.label || d.grantor}</p>
								{#if d.description}<p class="text-sm text-gray-600">{d.description}</p>{/if}
								<p class="text-xs text-gray-500">{scopeSummary(d.scope)}</p>
								<p class="text-xs text-gray-500">{fmtWindow(d)}</p>
							</div>
							<div class="flex gap-2">
								<Button size="sm" color="alternative" onclick={() => setActingOnBehalfOf(d.grantor)}>
									Act as them
								</Button>
								<Button size="sm" color="light" onclick={() => revokeOne(d.id)}>Revoke</Button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</Card>

		<Card size="xl" class="shadow-sm">
			<Heading tag="h2" class="text-lg mb-3">Granted by me</Heading>
			{#if $delegationsAsGrantor.length === 0}
				<P class="text-gray-500 text-sm">You have not granted any delegations.</P>
			{:else}
				<ul class="divide-y divide-gray-100">
					{#each $delegationsAsGrantor as d (d.id)}
						<li class="py-3 flex flex-wrap items-center justify-between gap-2">
							<div>
								<p class="font-medium">{d.label || d.delegate}</p>
								{#if d.description}<p class="text-sm text-gray-600">{d.description}</p>{/if}
								<p class="text-xs text-gray-500 font-mono">→ {d.delegate}</p>
								<p class="text-xs text-gray-500">Status: {statusText(d)} · {scopeSummary(d.scope)}</p>
								<p class="text-xs text-gray-500">{fmtWindow(d)}</p>
							</div>
							{#if d.status === 'active' || d.status === 'pending'}
								<Button size="sm" color="light" onclick={() => revokeOne(d.id)}>Revoke</Button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</Card>
	{/if}
</div>
