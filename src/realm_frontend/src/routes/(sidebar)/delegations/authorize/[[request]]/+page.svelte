<script lang="ts">
	import MetaTag from '../../../../utils/MetaTag.svelte';
	import { Button, Card, Checkbox, Heading, Input, Label, P, Spinner } from 'flowbite-svelte';
	import { page } from '$app/stores';
	import { styles, cn } from '$lib/theme/utilities';
	import { isAuthenticated, principal } from '$lib/stores/auth';
	import {
		loadDelegationRequest,
		approveDelegationRequest,
		declineDelegationRequest
	} from '$lib/stores/delegation.js';

	const path = '/delegations/authorize';
	const title = 'Authorize power of attorney';
	const description = 'Review a request to act for you in this realm';

	type Permission = { operation: string; label: string; description: string; grantor_holds: boolean };
	type Request = {
		id: string;
		grantor: string;
		delegate: string;
		label: string;
		description: string;
		starts_at: number;
		expires_at: number;
		request_expires_at: number;
		delegate_is_member: boolean;
		permissions?: Permission[];
	};

	const requestId = $page.params.request || $page.url.searchParams.get('request') || '';

	let loading = true;
	let loadedFor = '';
	let error = '';
	let request: Request | null = null;
	let chosen: string[] = [];
	let expiresInput = '';
	let busy = false;
	let outcome: 'approved' | 'declined' | '' = '';

	// Same parents as frame-ancestors in static/.ic-assets.json5; keep in sync.
	const PORTAL_PARENT =
		/^(https:\/\/([a-z0-9-]+\.)*gos\.earth|https:\/\/realmsgos\.org|http:\/\/([a-z0-9-]+\.)*localhost(:\d+)?|http:\/\/127\.0\.0\.1(:\d+)?)$/;

	function framedByStranger(): boolean {
		if (typeof window === 'undefined' || window.self === window.top) return false;
		const allowed = new Set([window.location.origin]);
		const portal = (globalThis as any).__CANISTER_IDS?.portal_url;
		if (portal) {
			try {
				allowed.add(new URL(portal).origin);
			} catch {
				/* ignore */
			}
		}
		const ancestors = window.location.ancestorOrigins;
		let parent = ancestors?.length ? ancestors[0] : '';
		if (!parent && document.referrer) {
			try {
				parent = new URL(document.referrer).origin;
			} catch {
				parent = '';
			}
		}
		return !parent || !(allowed.has(parent) || PORTAL_PARENT.test(parent));
	}

	const refused = framedByStranger();

	function toLocalInput(ts: number) {
		const d = new Date(ts * 1000);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}

	function fmt(ts: number) {
		return ts ? new Date(ts * 1000).toLocaleString() : '';
	}

	async function load() {
		loading = true;
		error = '';
		try {
			const res = await loadDelegationRequest(requestId);
			if (!res?.success) {
				request = null;
				error = res?.error || 'Could not load this request';
				return;
			}
			request = res.data;
			chosen = (request?.permissions || []).filter((p) => p.grantor_holds).map((p) => p.operation);
			expiresInput = toLocalInput(request!.expires_at);
		} catch (e: any) {
			error = e.message || 'Could not load this request';
		} finally {
			loading = false;
		}
	}

	$: if (!refused && requestId && $isAuthenticated && $principal && loadedFor !== $principal) {
		loadedFor = $principal;
		load();
	}
	$: if (!requestId || !$isAuthenticated) loading = false;

	$: holdsAny = (request?.permissions || []).some((p) => p.grantor_holds);
	$: name = request?.label || 'An unnamed requester';

	async function authorize() {
		if (!request) return;
		error = '';
		const expiresAt = Math.floor(new Date(expiresInput).getTime() / 1000);
		if (!chosen.length) {
			error = 'Choose at least one permission, or cancel.';
			return;
		}
		if (!expiresAt || expiresAt > request.expires_at) {
			error = `The expiry can be at most ${fmt(request.expires_at)}.`;
			return;
		}
		if (expiresAt <= Math.max(request.starts_at, Math.floor(Date.now() / 1000))) {
			error = 'The expiry must be in the future and after the start.';
			return;
		}
		busy = true;
		try {
			const res = await approveDelegationRequest(request.id, {
				operations: chosen,
				expiresAt: expiresAt === request.expires_at ? undefined : expiresAt
			});
			if (res?.success) outcome = 'approved';
			else error = res?.error || 'Authorization failed';
		} catch (e: any) {
			error = e.message || 'Authorization failed';
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
			else error = res?.error || 'Could not decline';
		} catch (e: any) {
			error = e.message || 'Could not decline';
		} finally {
			busy = false;
		}
	}
</script>

<MetaTag {path} {title} {description} />

<div class="p-4 max-w-xl mx-auto">
	{#if refused}
		<Card size="xl" class="shadow-sm">
			<Heading tag="h1" class="text-xl font-bold mb-2">Open this page directly</Heading>
			<P class="text-sm text-gray-600">
				For your safety, power of attorney requests cannot be approved inside another site.
				Open the link in its own browser tab.
			</P>
		</Card>
	{:else if !requestId}
		<Card size="xl" class="shadow-sm">
			<P>This link has no request in it. Ask for a new link.</P>
		</Card>
	{:else if !$isAuthenticated}
		<Card size="xl" class="shadow-sm">
			<Heading tag="h1" class="text-xl font-bold mb-2">Sign in to review this request</Heading>
			<P class="text-sm text-gray-600">
				Someone asked for power of attorney in this realm. Sign in with the account it was
				addressed to; the request is then shown here.
			</P>
		</Card>
	{:else if loading}
		<div class="flex justify-center py-10"><Spinner /></div>
	{:else if outcome === 'approved' && request}
		<Card size="xl" class="shadow-sm space-y-2">
			<Heading tag="h1" class="text-xl font-bold">Authorized</Heading>
			<P class="text-sm">
				<span class="font-medium">{name}</span> can now act for you with the permissions you
				chose, until {fmt(Math.floor(new Date(expiresInput).getTime() / 1000))}. You can go back
				to it now.
			</P>
			<P class="text-sm text-gray-600">
				Revoke it at any time under <a class="underline" href="/delegations">Power of attorney</a>.
			</P>
		</Card>
	{:else if outcome === 'declined'}
		<Card size="xl" class="shadow-sm">
			<Heading tag="h1" class="text-xl font-bold mb-2">Request declined</Heading>
			<P class="text-sm text-gray-600">Nothing was granted.</P>
		</Card>
	{:else if !request}
		<Card size="xl" class="shadow-sm space-y-2">
			<Heading tag="h1" class="text-xl font-bold">Request not available</Heading>
			<P class="text-sm text-gray-600">{error}</P>
			<P class="text-sm text-gray-600">
				Requests expire after an hour and can only be opened by the member they were sent to.
				You are signed in as <span class="font-mono break-all">{$principal}</span>.
			</P>
		</Card>
	{:else}
		<Card size="xl" class="shadow-sm space-y-5">
			<div class="text-center space-y-1">
				<Heading tag="h1" class="text-xl font-bold">Authorize {name}</Heading>
				<P class="text-xs text-gray-500">The name is chosen by the requester and is not verified.</P>
			</div>

			<div class="rounded-lg border border-gray-200 bg-gray-50 p-3">
				<p class="text-xs font-medium text-gray-500 mb-1">Requested by principal</p>
				<p class="font-mono text-sm break-all">{request.delegate}</p>
				<p class="text-xs text-gray-500 mt-1">
					{request.delegate_is_member
						? 'A member of this realm.'
						: 'Not a member: for example an AI assistant connection.'}
					Only grant it if you recognise this principal.
				</p>
			</div>

			{#if request.description}
				<div>
					<p class="text-xs font-medium text-gray-500 mb-1">Says it is for</p>
					<p class="text-sm whitespace-pre-line">{request.description}</p>
				</div>
			{/if}

			<fieldset>
				<legend class="mb-2 text-sm font-medium text-gray-900">
					It wants to act for you to
				</legend>
				<div class="space-y-3">
					{#each request.permissions || [] as p (p.operation)}
						<Checkbox bind:group={chosen} value={p.operation} disabled={!p.grantor_holds}>
							<span class={p.grantor_holds ? '' : 'text-gray-400'}>
								<span class="font-medium">{p.label}</span>
								<span class="block text-xs {p.grantor_holds ? 'text-gray-500' : 'text-gray-400'}">
									{p.grantor_holds ? p.description : 'You do not hold this permission, so you cannot grant it.'}
								</span>
							</span>
						</Checkbox>
					{/each}
				</div>
				<p class="mt-2 text-xs text-gray-500">Untick anything you do not want to grant.</p>
			</fieldset>

			<div>
				<Label for="poa-authorize-expires" class="mb-1">Until</Label>
				<Input
					id="poa-authorize-expires"
					type="datetime-local"
					bind:value={expiresInput}
					max={toLocalInput(request.expires_at)}
				/>
				<p class="mt-1 text-xs text-gray-500">
					{request.starts_at ? `Starts ${fmt(request.starts_at)}. ` : 'Starts at once. '}
					You can make it end sooner, not later.
				</p>
			</div>

			<div class="rounded-lg border border-gray-200 p-3 text-xs text-gray-600 space-y-1">
				<p>Signed in as <span class="font-mono break-all">{$principal}</span></p>
				<p>Every action is recorded as done on your behalf. You can revoke this at any time.</p>
				<p>This request expires {fmt(request.request_expires_at)}.</p>
			</div>

			{#if error}<p class="text-red-600 text-sm">{error}</p>{/if}

			<div class="flex gap-3">
				<Button color="light" class="flex-1" disabled={busy} on:click={cancel}>Cancel</Button>
				<Button
					color="none"
					class={cn(styles.button.primary(), 'flex-1 disabled:opacity-50')}
					disabled={busy || !holdsAny || !chosen.length}
					on:click={authorize}
				>
					{#if busy}<Spinner size="4" class="me-2" />{/if}
					Authorize
				</Button>
			</div>
			{#if !holdsAny}
				<p class="text-xs text-gray-500 text-center">
					You hold none of the permissions asked for, so there is nothing to grant.
				</p>
			{/if}
		</Card>
	{/if}
</div>
