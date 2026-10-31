<script lang="ts">
	import MetaTag from '../../utils/MetaTag.svelte';
	import IdentityCard from '../../utils/settings/IdentityCard.svelte';
	import { imagesPath } from '../../utils/variables';
	import { Avatar, Button, Card, Heading, Input, Label, P, Spinner, Toggle } from 'flowbite-svelte';
	import { FingerprintOutline } from 'flowbite-svelte-icons';
	import { backend, backendReady } from '$lib/canisters';
	import { must, read, run } from '$lib/realm';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { isAuthenticated, principal } from '$lib/stores/auth';
	import { decryptPrivateData } from '$lib/crypto/vetkeys';
	import {
		buildSharePlan,
		decryptScopeData,
		deriveMySharingVetKey,
		grantScopeData,
		userScope
	} from '$lib/crypto/sharing';
	import { _ } from 'svelte-i18n';

	function t(key: string, values?: Record<string, unknown>) {
		return get(_)(key, values);
	}

	const path: string = '/identities';
	const description: string = '';
	const metaTitle: string = '';
	const subtitle: string = '';

	interface IdentityProvider {
		extensionName: string;
		name: string;
		providerUrl: string;
		logo: string;
		description: string;
		verified: boolean;
	}

	// Per-section loading states
	let publicDataLoaded = $state(false);
	let privateDataLoaded = $state(false);
	// No realm operation lists identity-provider extensions yet.
	let identitiesLoading = $state(false);
	let identityProviders: IdentityProvider[] = $state([]);

	// Public data
	let nickname = $state('');
	let avatarUrl = $state('');
	let publicSaving = $state(false);
	let publicMessage = $state('');

	// Private data
	let privateData: Record<string, string> = $state({});
	let privateSaving = $state(false);
	let privateMessage = $state('');
	/** null = not probed yet; do not treat that as "unavailable". */
	let encryptionAvailable: boolean | null = $state(null);
	let encryptionError = $state('');
	const ANON_PRINCIPAL = '2vxsx-fae';

	// Data sharing (consent-based, via vetKey crypto groups — issue #215)
	interface ShareAudience {
		id: string;
		label: string;
		type: string;
		principals: string[];
	}
	let sharingLoaded = $state(false);
	let sharingSaving = $state(false);
	let sharingMessage = $state('');
	let audiences: ShareAudience[] = $state([]);
	let selectedAudiences: Record<string, boolean> = $state({});
	let currentlySharedWith: string[] = $state([]);

	const privateDataFields = $derived([
		{ key: 'first_name', label: $_('identities.first_name'), type: 'text' },
		{ key: 'last_name', label: $_('identities.last_name'), type: 'text' },
		{ key: 'photo', label: $_('identities.photo_url'), type: 'url' },
		{ key: 'birth_date', label: $_('identities.birth_date'), type: 'date' },
		{ key: 'address', label: $_('identities.address'), type: 'text' },
		{ key: 'email', label: $_('identities.email'), type: 'email' },
		{ key: 'phone', label: $_('identities.phone'), type: 'tel' }
	]);

	const displayAvatar = $derived(
		avatarUrl?.trim() || `https://api.dicebear.com/9.x/glass/svg?seed=${$principal}`,
	);

	function signedInPrincipal(): string {
		const p = (get(principal) as string) || '';
		return p && p !== ANON_PRINCIPAL ? p : '';
	}

	async function waitForSignedInActor(timeoutMs = 15000): Promise<string> {
		await backendReady;
		const existing = signedInPrincipal();
		if (existing) return existing;

		return new Promise((resolve) => {
			const deadline = Date.now() + timeoutMs;
			let settled = false;
			let timer: ReturnType<typeof setInterval> | undefined;
			const finish = (value: string) => {
				if (settled) return;
				settled = true;
				if (timer) clearInterval(timer);
				unsubAuth();
				unsubPrincipal();
				resolve(value);
			};
			const unsubAuth = isAuthenticated.subscribe(() => {
				const p = signedInPrincipal();
				if (p) finish(p);
			});
			const unsubPrincipal = principal.subscribe(() => {
				const p = signedInPrincipal();
				if (p) finish(p);
			});
			timer = setInterval(() => {
				const p = signedInPrincipal();
				if (p) finish(p);
				else if (Date.now() >= deadline) finish('');
			}, 200);
		});
	}

	onMount(() => {
		let cancelled = false;
		let loadedFor = '';

		async function loadForCurrentUser() {
			const owner = signedInPrincipal();
			if (!owner || owner === loadedFor) return;
			loadedFor = owner;
			publicDataLoaded = false;
			privateDataLoaded = false;
			sharingLoaded = false;
			encryptionAvailable = null;
			encryptionError = '';
			await Promise.all([loadPublicData(), loadPrivateData(), loadSharingStatus()]);
		}

		void (async () => {
			await waitForSignedInActor();
			if (!cancelled) await loadForCurrentUser();
		})();

		const unsub = isAuthenticated.subscribe((auth) => {
			if (auth && signedInPrincipal()) void loadForCurrentUser();
		});

		return () => {
			cancelled = true;
			unsub();
		};
	});

	async function probeEncryption(): Promise<boolean> {
		const owner = signedInPrincipal();
		if (!owner) {
			encryptionError = t('identities.not_signed_in');
			return false;
		}
		try {
			await deriveMySharingVetKey(backend, owner);
			return true;
		} catch (e) {
			console.warn('Encryption probe failed:', e);
			encryptionError = t('identities.encryption_unavailable', { values: { detail: ' on this subnet' } });
			return false;
		}
	}

	async function loadPublicData() {
		try {
			const me = await read('me');
			if (me.ok) {
				nickname = me.user.nickname || '';
				avatarUrl = me.user.avatar || '';
			}
		} catch (err) {
			console.error('Error loading public data:', err);
		} finally {
			publicDataLoaded = true;
		}
	}

	async function loadPrivateData() {
		encryptionAvailable = null;
		encryptionError = '';
		try {
			const owner = signedInPrincipal();
			if (!owner) {
				encryptionAvailable = false;
				encryptionError = t('identities.not_signed_in');
				return;
			}
			const me = await read('me');
			if (me.ok && me.private_data) {
				// 1. Preferred: DEK + envelope model (supports sharing).
				const decrypted = await decryptScopeData<Record<string, string>>(
					backend,
					userScope(owner),
					owner,
					me.private_data
				);
				if (decrypted) {
					privateData = decrypted;
					encryptionAvailable = true;
				} else {
					// 2. Legacy: data encrypted directly with the user's vetKey.
					let legacy: Record<string, string> | null = null;
					try {
						legacy = await decryptPrivateData(backend, me.private_data);
					} catch (decErr) {
						console.warn('Legacy vetKeys decryption failed:', decErr);
					}
					if (legacy) {
						privateData = legacy;
						encryptionAvailable = true;
					} else {
						// 3. Plaintext / unknown — surface raw JSON if parseable.
						try {
							privateData = JSON.parse(me.private_data);
						} catch {
							privateData = {};
						}
					}
				}
			}
			if (encryptionAvailable !== true) {
				encryptionAvailable = await probeEncryption();
			}
		} catch (err) {
			console.error('Error loading private data:', err);
			if (encryptionAvailable !== true) {
				encryptionAvailable = await probeEncryption();
			}
		} finally {
			privateDataLoaded = true;
		}
	}

	async function loadSharingStatus() {
		try {
			const owner = signedInPrincipal();
			if (!owner) return;
			const scope = userScope(owner);

			const holders: string[] = [];
			for (let from: number | null = 1; from; ) {
				const page = await read('crypto_access', { scope, from, count: 500 });
				if (!page.ok) break;
				holders.push(...page.principals);
				from = page.next;
			}
			currentlySharedWith = holders.filter((p) => p !== owner);

			const found: ShareAudience[] = [];
			for (let from: number | null = 1; from; ) {
				const page = await must(read('department_list', { from, count: 100 }));
				const departments = await Promise.all(
					page.departments.map((d: { id: string }) => must(read('department_get', { department: d.id })))
				);
				for (const { department, positions } of departments) {
					found.push({
						id: `dept:${department.id}`,
						label: department.id,
						type: 'department',
						principals: positions.flatMap((p: any) => p.holders.map((h: any) => h.user))
					});
				}
				from = page.next;
			}
			audiences = found.filter((a) => a.principals.length > 0);

			// An audience is shown as selected if at least one of its members
			// (other than the owner) currently holds an envelope for this scope.
			const sharedSet = new Set(currentlySharedWith);
			const sel: Record<string, boolean> = {};
			for (const a of audiences) {
				sel[a.id] = a.principals.some((p) => p !== owner && sharedSet.has(p));
			}
			selectedAudiences = sel;
		} catch (e) {
			console.warn('loadSharingStatus failed:', e);
		} finally {
			sharingLoaded = true;
		}
	}

	function selectedRecipients(owner: string): string[] {
		const set = new Set<string>();
		for (const a of audiences) {
			if (selectedAudiences[a.id]) {
				for (const p of a.principals) if (p && p !== owner) set.add(p);
			}
		}
		return Array.from(set);
	}

	async function savePublicProfile() {
		publicSaving = true;
		publicMessage = '';
		try {
			const response = await run('profile_update', { nickname: nickname.trim(), avatar: avatarUrl.trim() });
			if (response.ok) {
				publicMessage = t('identities.public_updated');
				window.dispatchEvent(new CustomEvent('profilePictureUpdated', {
					detail: { profilePictureUrl: avatarUrl.trim() }
				}));
			} else {
				publicMessage = t('identities.public_update_failed');
			}
		} catch (err) {
			console.error('Error updating public profile:', err);
			publicMessage = t('identities.public_update_error');
		} finally {
			publicSaving = false;
		}
	}

	async function savePrivateData() {
		privateSaving = true;
		privateMessage = '';
		try {
			if (encryptionAvailable !== true) {
				if (encryptionAvailable === null) {
					privateMessage = t('identities.encryption_checking');
					return;
				}
				const response = await run('private_data_update', { private_data: JSON.stringify(privateData) });
				privateMessage = response.ok
					? t('identities.private_saved_unencrypted')
					: t('identities.private_update_failed');
				return;
			}

			const owner = signedInPrincipal();
			if (!owner) {
				privateMessage = t('identities.not_signed_in');
				return;
			}
			const scope = userScope(owner);
			const recipients = selectedRecipients(owner);

			// Encrypt with a fresh DEK and wrap it for the owner + each recipient.
			// The owner is always included so the new ciphertext stays self-readable.
			const plan = await buildSharePlan(backend, [owner, ...recipients], privateData);

			const response = await run('private_data_update', { private_data: plan.ciphertext });
			if (!response.ok) {
				privateMessage = t('identities.private_update_failed');
				return;
			}

			// Persist grants (and revoke anyone deselected) in batch calls; never
			// revoke the owner.
			const granted = await grantScopeData(backend, scope, plan.wrappedDeks, {
				previousRecipients: currentlySharedWith,
				keep: [owner]
			});
			currentlySharedWith = granted.filter((p) => p !== owner);

			privateMessage =
				recipients.length > 0
					? t('identities.private_saved_shared', { values: { count: recipients.length } })
					: t('identities.private_saved_encrypted');
		} catch (err) {
			console.error('Error updating private data:', err);
			privateMessage = t('identities.private_update_error');
		} finally {
			privateSaving = false;
		}
	}

	async function saveSharingSettings() {
		if (encryptionAvailable !== true) return;
		sharingSaving = true;
		sharingMessage = '';
		try {
			await savePrivateData();
			sharingMessage = t('identities.sharing_updated');
		} catch {
			sharingMessage = t('identities.sharing_update_failed');
		} finally {
			sharingSaving = false;
		}
	}
</script>

<MetaTag
	path={path}
	description={description || $_('identities.meta_description')}
	title={metaTitle || $_('identities.page_title')}
	subtitle={subtitle || $_('identities.page_subtitle')}
/>

<div class="mt-4 space-y-6 px-4 md:px-6">
	<!-- Public Data -->
	<Card size="xl">
		<Heading tag="h3" class="mb-2 text-xl font-bold dark:text-white">{$_('identities.public_data')}</Heading>
		<p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
			{$_('identities.public_data_help')}
		</p>
		{#if !publicDataLoaded}
			<div class="flex justify-center items-center py-8">
				<Spinner size="6" />
			</div>
		{:else}
			<div class="flex flex-col sm:flex-row gap-6">
				<div class="flex-shrink-0">
					<Avatar src={displayAvatar} class="h-24 w-24 rounded-lg" size="none" rounded />
				</div>
				<div class="flex-1 space-y-4">
					<div>
						<Label for="nickname" class="mb-2">{$_('identities.nickname')}</Label>
						<Input id="nickname" bind:value={nickname} placeholder={$_('identities.nickname_placeholder')} />
					</div>
					<div>
						<Label for="avatar-url" class="mb-2">{$_('identities.avatar_url')}</Label>
						<Input id="avatar-url" bind:value={avatarUrl} placeholder={$_('identities.avatar_placeholder')} />
					</div>
				</div>
			</div>
			{#if publicMessage}
				<p class="mt-3 text-sm {publicMessage.includes('success') ? 'text-green-600' : 'text-red-600'}">{publicMessage}</p>
			{/if}
			<div class="mt-4">
				<Button size="sm" color="alternative" on:click={savePublicProfile} disabled={publicSaving}>
					{publicSaving ? $_('identities.saving') : $_('buttons.save')}
				</Button>
			</div>
		{/if}
	</Card>

	<!-- Private Data -->
	<Card size="xl">
		<Heading tag="h3" class="mb-2 text-xl font-bold dark:text-white">{$_('identities.private_data')}</Heading>
		{#if !privateDataLoaded}
			<div class="flex justify-center items-center py-8">
				<Spinner size="6" />
				<span class="ml-3 text-sm text-gray-500">{$_('identities.decrypting')}</span>
			</div>
		{:else}
			{#if encryptionAvailable === true}
				<div class="mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg">
					<p class="text-sm text-green-800 dark:text-green-200">
						&#x1f512; {$_('identities.encrypted_banner')}
					</p>
				</div>
			{:else if encryptionAvailable === false}
				<div class="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
					<p class="text-sm text-yellow-800 dark:text-yellow-200">
						{$_('identities.encryption_unavailable', {
							values: { detail: encryptionError ? `: ${encryptionError}` : '' }
						})}
					</p>
				</div>
			{/if}
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				{#each privateDataFields as field}
					<div>
						<Label for="private-{field.key}" class="mb-2">
							{field.label}
						</Label>
						{#if field.type === 'date'}
							<Input id="private-{field.key}" type="date" value={privateData[field.key] || ''} on:input={(e) => { privateData[field.key] = e.currentTarget.value; }} />
						{:else if field.type === 'email'}
							<Input id="private-{field.key}" type="email" value={privateData[field.key] || ''} on:input={(e) => { privateData[field.key] = e.currentTarget.value; }} placeholder="email@example.com" />
						{:else if field.type === 'tel'}
							<Input id="private-{field.key}" type="tel" value={privateData[field.key] || ''} on:input={(e) => { privateData[field.key] = e.currentTarget.value; }} placeholder="+1 234 567 890" />
						{:else if field.type === 'url'}
							<Input id="private-{field.key}" type="url" value={privateData[field.key] || ''} on:input={(e) => { privateData[field.key] = e.currentTarget.value; }} placeholder="https://..." />
						{:else}
							<Input id="private-{field.key}" type="text" value={privateData[field.key] || ''} on:input={(e) => { privateData[field.key] = e.currentTarget.value; }} />
						{/if}
					</div>
				{/each}
			</div>
			{#if privateMessage}
				<p class="mt-3 text-sm {privateMessage.includes('success') ? 'text-green-600' : 'text-red-600'}">{privateMessage}</p>
			{/if}
			<div class="mt-4">
				<Button size="sm" color="alternative" on:click={savePrivateData} disabled={privateSaving || encryptionAvailable === null}>
					{privateSaving ? $_('identities.saving') : $_('buttons.save')}
				</Button>
			</div>
		{/if}
	</Card>

	<!-- Connected Identities -->
	<Card size="xl">
		<Heading tag="h3" class="mb-2 text-xl font-bold dark:text-white">{$_('identities.connected')}</Heading>
		<p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
			{$_('identities.connected_help')}
		</p>
		{#if identitiesLoading}
			<div class="flex justify-center items-center py-8">
				<Spinner size="8" />
			</div>
		{:else if identityProviders.length > 0}
			<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
				{#each identityProviders as provider}
					{#if provider.verified}
						<IdentityCard
							src={imagesPath(provider.logo)}
							title={provider.name}
							description={provider.description}
							status="verified"
						/>
					{:else}
						<a href="/extensions/{provider.extensionName}" class="block">
							<div class="flex flex-col items-center justify-center p-6 border-2 border-dashed border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 hover:border-blue-400 dark:hover:border-blue-500 cursor-pointer transition-colors rounded-lg">
								{#if provider.logo}
									<img src={imagesPath(provider.logo)} alt={provider.name} class="w-24 h-24 object-contain mb-3" />
								{:else}
									<div class="p-3 mb-3 rounded-full bg-blue-100 dark:bg-blue-900">
										<FingerprintOutline class="w-8 h-8 text-blue-600 dark:text-blue-400" />
									</div>
								{/if}
								<Heading tag="h4" class="mb-1 text-lg font-semibold text-gray-900 dark:text-white">{provider.name}</Heading>
								<P class="mb-3 text-sm text-gray-500 dark:text-gray-400 text-center">
									{provider.description}
								</P>
								<Button size="sm" color="blue" class="px-4 py-2">{$_('identities.start_verification')}</Button>
							</div>
						</a>
					{/if}
				{/each}
			</div>
		{:else}
			<div class="text-center py-6">
				<div class="w-12 h-12 mx-auto mb-3 bg-gray-100 dark:bg-gray-700 rounded-full flex items-center justify-center">
					<FingerprintOutline class="w-6 h-6 text-gray-400 dark:text-gray-500" />
				</div>
				<p class="text-gray-500 dark:text-gray-400">
					{$_('identities.no_providers')}
				</p>
			</div>
		{/if}
	</Card>

	<!-- Data Sharing -->
	<Card size="xl">
		<Heading tag="h3" class="mb-2 text-xl font-bold dark:text-white">{$_('identities.data_sharing')}</Heading>
		<p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
			{$_('identities.data_sharing_help')}
		</p>
		{#if !sharingLoaded}
			<div class="flex justify-center items-center py-6">
				<Spinner size="6" />
			</div>
		{:else if encryptionAvailable === null}
			<div class="flex justify-center items-center py-6">
				<Spinner size="6" />
			</div>
		{:else if encryptionAvailable === false}
			<div class="p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
				<p class="text-sm text-yellow-800 dark:text-yellow-200">
					{$_('identities.sharing_requires_encryption')}
				</p>
			</div>
		{:else if audiences.length === 0}
			<div class="p-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg">
				<p class="text-sm text-gray-600 dark:text-gray-300">
					{$_('identities.no_share_audiences')}
				</p>
			</div>
		{:else}
			<ul class="space-y-2">
				{#each audiences as audience (audience.id)}
					<li class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg">
						<div>
							<p class="text-sm font-medium text-gray-900 dark:text-white">
								{audience.label}
								{#if audience.type === 'department'}
									<span class="ml-1 text-xs font-normal text-gray-400">{$_('identities.department')}</span>
								{/if}
							</p>
							<p class="text-xs text-gray-500 dark:text-gray-400">
								{$_('identities.members_gain_access', { values: { count: audience.principals.length } })}
							</p>
						</div>
						<Toggle bind:checked={selectedAudiences[audience.id]} disabled={sharingSaving || privateSaving} />
					</li>
				{/each}
			</ul>

			<div class="mt-4 flex items-center gap-3">
				<Button size="sm" color="blue" on:click={saveSharingSettings} disabled={sharingSaving || privateSaving}>
					{sharingSaving ? $_('identities.updating') : $_('identities.save_sharing')}
				</Button>
				{#if sharingMessage}
					<span class="text-sm {sharingMessage.includes('updated') ? 'text-green-600' : 'text-red-600'}">{sharingMessage}</span>
				{/if}
			</div>

			{#if currentlySharedWith.length > 0}
				<div class="mt-3">
					<p class="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
						{$_('identities.currently_shared', { values: { count: currentlySharedWith.length } })}
					</p>
					<ul class="space-y-1 max-h-32 overflow-y-auto">
						{#each currentlySharedWith as p}
							<li class="text-xs font-mono text-gray-600 dark:text-gray-300 break-all">{p}</li>
						{/each}
					</ul>
				</div>
			{/if}
		{/if}
	</Card>

</div>
