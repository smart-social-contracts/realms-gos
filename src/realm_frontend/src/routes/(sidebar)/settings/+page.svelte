<script lang="ts">
	console.log("Settings Svelte script loaded (top of file)");

	import { Heading } from 'flowbite-svelte';
	import { SITE_NAME } from '$lib/globals';
	import MetaTag from '../../utils/MetaTag.svelte';
	import { _ } from 'svelte-i18n';
	import { get } from 'svelte/store';
	import { localizeBackendError } from '$lib/utils/backendError';
	import { onMount, untrack } from 'svelte';
	// @ts-ignore
	import { createQuarterActor, quarterBackendStore } from '$lib/canisters.js';
	import { must, on, quarter, type Reply } from '$lib/realm';
	import { profilesLoading } from '$lib/stores/profiles';
	import { realmInfo } from '$lib/stores/realmInfo';
	import { activeQuarterId } from '$lib/stores/quarters';
	import {
		probeFederatedMembership,
		activateMembership,
		type MembershipHit
	} from '$lib/utils/federatedMembership';
	import { formatQuarterLabel } from '$lib/utils/quarterLabels';
	import { showBridgeAlert, showBridgeNotice } from '$lib/stores/bridge-modal';
	import LanguageSwitcher from '$lib/components/LanguageSwitcher.svelte';
	import { applyResolvedLocale } from '$lib/i18n';
	import { localeLabel } from '$lib/i18n/realmLocales';
	import { localeFromPrivateData, userLocale } from '$lib/stores/userLocale';

	const path: string = '/settings';
	const description: string = 'Settings example - Smart Social Contracts';
	const title: string = SITE_NAME + ' - Settings';
	const subtitle: string = 'Settings';

	let principal = $state('');
	let nickname = $state('');
	let avatarUrl = $state('');
	let profiles = $state<string[]>([]);
	let email = $state('');
	let emailNotificationsEnabled = $state(true);
	let emailVerified = $state(false);
	let verificationPending = $state(false);
	let verifyCode = $state('');
	let sendingCode = $state(false);
	let verifyingCode = $state(false);
	let privateData: Record<string, any> = $state({});
	let loadingUserStatus = $state(true);
	let userStatusError = $state('');
	let savingEmail = $state(false);
	let selectedLocale = $state('');
	let savingLocale = $state(false);

	const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

	const normalizedEmail = $derived(email.trim().toLowerCase());
	const emailIsValid = $derived(Boolean(normalizedEmail) && EMAIL_REGEX.test(normalizedEmail));

	/** Federated membership probe (issue #156). */
	let membershipHits = $state<MembershipHit[]>([]);
	let capitalId = $state('');
	let loadingMembership = $state(true);
	let membershipError = $state('');
	let activatingCanisterId = $state('');
	let registeringCanisterId = $state('');

	const displayAvatar = $derived(
		avatarUrl?.trim() || `https://api.dicebear.com/9.x/glass/svg?seed=${principal}`
	);

	const sessionQuarterId = $derived(($activeQuarterId as string | null) || capitalId || '');

	const showFederationUi = $derived(
		$realmInfo.quarters.length > 1 || membershipHits.length > 1
	);

	const membershipIds = $derived(new Set(membershipHits.map((h) => h.canisterId)));

	const registerTargets = $derived(
		($realmInfo.quarters || []).filter(
			(q) =>
				q.status === 'active' &&
				q.canister_id &&
				!membershipIds.has(q.canister_id)
		)
	);

	function quarterMeta(canisterId: string) {
		return ($realmInfo.quarters || []).find((q) => q.canister_id === canisterId) || null;
	}

	function labelForCanister(canisterId: string): string {
		const q = quarterMeta(canisterId);
		if (q) return formatQuarterLabel(q as any);
		if (capitalId && canisterId === capitalId) return $_('settings.quarter_capital');
		return canisterId || $_('settings.quarter_unknown');
	}

	function isSessionActive(hit: MembershipHit): boolean {
		const cid = hit.canisterId || '';
		if (hit.isCapital || (capitalId && cid === capitalId)) {
			return !$activeQuarterId || $activeQuarterId === capitalId;
		}
		return $activeQuarterId === cid;
	}

	/** Primary profile for deliberate multi-registration: prefer admin, else member. */
	function primaryJoinProfile(): string {
		if (profiles.includes('admin')) return 'admin';
		if (profiles.includes('member')) return 'member';
		if (profiles.includes('developer')) return 'developer';
		return profiles[0] || 'member';
	}

	function isInviteRequiredError(message: string): boolean {
		const m = (message || '').toLowerCase();
		return (
			m.includes('invite') ||
			m.includes('invitation') ||
			m.includes('registration code') ||
			m.includes('registration is closed') ||
			m.includes('open_registration') ||
			m.includes('not open')
		);
	}

	let userStatusSeq = 0;

	/** Returns false when a newer load superseded this one (its result is dropped). */
	async function loadUserStatus(): Promise<boolean> {
		const seq = ++userStatusSeq;
		const me = await must(quarter.read('me'));
		if (seq !== userStatusSeq) return false;
		principal = me.user.id;
		nickname = me.user.nickname || '';
		avatarUrl = me.user.avatar || '';
		profiles = me.user.profiles || [];
		try {
			privateData = JSON.parse(me.private_data || '{}');
		} catch {
			privateData = {};
		}
		email = privateData.email || '';
		emailNotificationsEnabled = privateData.email_notifications_enabled !== false;
		emailVerified = privateData.email_verified === true;
		verificationPending = Boolean(email) && !emailVerified;
		selectedLocale = localeFromPrivateData(privateData);
		userLocale.set(selectedLocale);
		applyResolvedLocale({
			userLocale: selectedLocale,
			languages: $realmInfo.languages,
			primaryLanguage: $realmInfo.primaryLanguage
		});
		return true;
	}

	async function refreshUserStatus() {
		const seq = userStatusSeq + 1;
		try {
			if (await loadUserStatus()) userStatusError = '';
		} catch (e: any) {
			if (seq === userStatusSeq) userStatusError = e.message || 'Failed to fetch user status.';
		} finally {
			if (seq === userStatusSeq) loadingUserStatus = false;
		}
	}

	// On reload the capital answers first; members live on their home quarter,
	// which activates once the membership probe finishes. Re-read the user
	// record whenever the quarter-aware actor swaps.
	$effect(() => {
		$quarterBackendStore;
		untrack(() => void refreshUserStatus());
	});

	function parseExtensionResponse(reply: Reply) {
		const tr = (key: string) => get(_)(key);
		if (!reply.ok) {
			throw new Error(localizeBackendError(reply.error, tr, 'errors.request_failed'));
		}
		const data = typeof reply.result === 'string' ? JSON.parse(reply.result) : reply.result;
		if (data?.success === false) {
			throw new Error(localizeBackendError(data, tr, 'errors.request_failed'));
		}
		return data?.data ?? data;
	}

	async function sendVerificationCode() {
		sendingCode = true;
		try {
			if (!emailIsValid) {
				throw new Error(get(_)('errors.email_invalid'));
			}
			const result = await quarter.run('extension_call', {
				extension: 'notifications',
				function: 'request_email_verification',
				args: { email: normalizedEmail }
			});
			parseExtensionResponse(result);
			verificationPending = true;
			emailVerified = false;
			void showBridgeNotice({
				title: $_('settings.email_code_sent_title'),
				body: $_('settings.email_code_sent_body'),
			});
		} catch (e: any) {
			void showBridgeAlert({ body: e.message || get(_)('errors.email_send_failed') });
		} finally {
			sendingCode = false;
		}
	}

	async function verifyEmailCode() {
		verifyingCode = true;
		try {
			const code = verifyCode.trim();
			if (!code) {
				throw new Error(get(_)('errors.email_code_required'));
			}
			const result = await quarter.run('extension_call', {
				extension: 'notifications',
				function: 'verify_email_code',
				args: { code }
			});
			parseExtensionResponse(result);
			emailVerified = true;
			verificationPending = false;
			verifyCode = '';
			void showBridgeNotice({
				title: $_('settings.email_verified_title'),
				body: $_('settings.email_verified_body'),
			});
			try {
				await loadUserStatus();
			} catch (e: any) {
				console.warn('User status refresh after verify failed:', e);
			}
		} catch (e: any) {
			void showBridgeAlert({ body: e.message || get(_)('errors.email_verify_failed') });
		} finally {
			verifyingCode = false;
		}
	}

	async function saveLocalePreference() {
		savingLocale = true;
		try {
			const allowed = $realmInfo.languages || [];
			if (selectedLocale && !allowed.includes(selectedLocale)) {
				throw new Error('Locale must be one of the realm languages.');
			}
			const updated = {
				...privateData,
				locale: selectedLocale
			};
			const response = await quarter.run('private_data_update', { private_data: JSON.stringify(updated) });
			if (response.ok) {
				privateData = updated;
				userLocale.set(selectedLocale);
				applyResolvedLocale({
					userLocale: selectedLocale,
					languages: $realmInfo.languages,
					primaryLanguage: $realmInfo.primaryLanguage
				});
				void showBridgeNotice({
					title: $_('buttons.save', { default: 'Saved' }),
					body: $_('settings.locale_saved', { default: 'Language preference saved.' })
				});
			} else {
				throw new Error(response.error || 'Could not save language preference.');
			}
		} catch (e: any) {
			void showBridgeAlert({ body: e.message || 'Failed to save language preference.' });
		} finally {
			savingLocale = false;
		}
	}

	async function saveNotificationPreference() {
		savingEmail = true;
		try {
			const updated = {
				...privateData,
				email_notifications_enabled: emailNotificationsEnabled
			};
			const response = await quarter.run('private_data_update', { private_data: JSON.stringify(updated) });
			if (response.ok) {
				privateData = updated;
				void showBridgeNotice({
					title: $_('settings.notification_saved_title'),
					body: $_('settings.notification_saved_body'),
				});
			} else {
				throw new Error(response.error || 'Could not save notification preference.');
			}
		} catch (e: any) {
			void showBridgeAlert({ body: e.message || 'Failed to save notification preference.' });
		} finally {
			savingEmail = false;
		}
	}

	async function refreshMembershipProbe() {
		const result = await probeFederatedMembership({ activate: false, cache: false });
		membershipHits = result.hits;
		capitalId = result.capitalId || '';
		return result;
	}

	async function handleActivate(hit: MembershipHit) {
		if (isSessionActive(hit) || activatingCanisterId) return;
		activatingCanisterId = hit.canisterId;
		try {
			await activateMembership(hit, capitalId, { cache: true });
			void showBridgeNotice({
				title: $_('settings.quarter_switched_title'),
				body: $_('settings.quarter_switched_body', { values: { label: labelForCanister(hit.canisterId) } }),
			});
			try {
				await loadUserStatus();
			} catch (e: any) {
				// Session switched; status refresh is best-effort.
				console.warn('User status refresh after activate failed:', e);
			}
		} catch (e: any) {
			void showBridgeAlert({ body: e.message || 'Failed to switch active quarter.' });
		} finally {
			activatingCanisterId = '';
		}
	}

	async function handleRegister(canisterId: string) {
		if (!canisterId || registeringCanisterId) return;
		registeringCanisterId = canisterId;
		try {
			const profile = primaryJoinProfile();
			const actor = await createQuarterActor(canisterId);
			const response: Reply = await on(actor).run('realm_join');
			if (!response.ok) {
				const err = response.error || 'Failed to register in this quarter.';
				if (isInviteRequiredError(err)) {
					void showBridgeAlert({
						title: $_('settings.quarter_invite_required_title'),
						body: $_('settings.quarter_invite_required_body'),
					});
				} else {
					void showBridgeAlert({ body: err });
				}
				return;
			}

			const result = await refreshMembershipProbe();
			const hit =
				result.hits.find((h) => h.canisterId === canisterId) ||
				({
					canisterId,
					isCapital: !!(capitalId && canisterId === capitalId),
					response,
					profiles: [profile]
				} as MembershipHit);

			await activateMembership(hit, result.capitalId || capitalId, { cache: true });
			void showBridgeNotice({
				title: $_('settings.quarter_registered_title'),
				body: $_('settings.quarter_registered_body', {
					values: { label: labelForCanister(canisterId) }
				}),
			});
			try {
				await loadUserStatus();
			} catch (e: any) {
				console.warn('User status refresh after register failed:', e);
			}
		} catch (e: any) {
			const msg = e.message || 'Failed to register in this quarter.';
			if (isInviteRequiredError(msg)) {
				void showBridgeAlert({
					title: $_('settings.quarter_invite_required_title'),
					body: $_('settings.quarter_invite_required_body'),
				});
			} else {
				void showBridgeAlert({ body: msg });
			}
		} finally {
			registeringCanisterId = '';
		}
	}

	onMount(async () => {
		await Promise.all([
			(async () => {
				try {
					await refreshMembershipProbe();
				} catch (e: any) {
					membershipError = e.message || 'Failed to probe quarter memberships.';
				} finally {
					loadingMembership = false;
				}
			})()
		]);
	});
</script>

<MetaTag {path} {description} {title} {subtitle} />

<main class="p-4">
	<div class="grid grid-cols-1 space-y-2 dark:bg-gray-900">
		<div class="col-span-full mb-6">
			<Heading tag="h1" class="text-xl font-semibold text-gray-900 sm:text-2xl dark:text-white">
				{$_('settings.title', { default: 'User Settings' })}
			</Heading>

			<!-- User Info -->
			{#if loadingUserStatus || (userStatusError && $profilesLoading)}
				<div class="mt-4 text-gray-500">{$_('settings.loading_status')}</div>
			{:else if userStatusError}
				<div class="mt-4 text-red-500">{userStatusError}</div>
			{:else}
				<div class="mt-4 p-5 bg-gray-50 rounded-lg border border-gray-200 dark:bg-gray-800 dark:border-gray-700">
					<div class="flex items-center gap-4 mb-4">
						<img
							src={displayAvatar}
							alt="Avatar"
							class="w-16 h-16 rounded-full object-cover border-2 border-gray-200 dark:border-gray-600"
						/>
						<div>
							<div class="text-lg font-semibold text-gray-900 dark:text-white">
								{nickname || $_('settings.no_nickname')}
							</div>
							<div class="flex flex-wrap gap-1.5 mt-1">
								{#each profiles as profile (profile)}
									<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300 capitalize">
										{profile}
									</span>
								{/each}
								{#if profiles.length === 0}
									<span class="text-sm text-gray-500 dark:text-gray-400">{$_('settings.no_profiles')}</span>
								{/if}
							</div>
						</div>
					</div>
					<div class="pt-3 border-t border-gray-200 dark:border-gray-700">
						<span class="text-xs font-medium text-gray-500 dark:text-gray-400">{$_('settings.principal')}</span>
						<div class="font-mono text-sm text-gray-700 dark:text-gray-300 break-all mt-0.5">{principal}</div>
					</div>
					{#if sessionQuarterId}
						<div class="pt-3 mt-3 border-t border-gray-200 dark:border-gray-700">
							<span class="text-xs font-medium text-gray-500 dark:text-gray-400">{$_('settings.active_quarter')}</span>
							<div class="text-sm text-gray-700 dark:text-gray-300 mt-0.5">
								{labelForCanister(sessionQuarterId)}
							</div>
						</div>
					{/if}
				</div>
			{/if}
		</div>

		<!-- Personal locale (issue #361) -->
		<div class="col-span-full mt-6">
			<Heading tag="h2" class="text-lg font-semibold text-gray-900 dark:text-white mb-3">
				{$_('settings.select_language', { default: 'Language' })}
			</Heading>
			<div class="p-4 bg-gray-50 rounded border border-gray-200 dark:bg-gray-800 dark:border-gray-700">
				<p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
					{$_('settings.language_description')}
				</p>
				<div class="flex flex-wrap items-center gap-3">
					<LanguageSwitcher
						locales={$realmInfo.languages}
						value={selectedLocale}
						allowEmpty={true}
						emptyLabel={$_('settings.locale_use_primary', {
							default: 'Use realm primary language'
						}) + ` (${localeLabel($realmInfo.primaryLanguage)})`}
						onchange={(next) => (selectedLocale = next)}
					/>
					<button
						type="button"
						onclick={saveLocalePreference}
						disabled={savingLocale || loadingUserStatus}
						class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
					>
						{savingLocale
							? $_('common.loading', { default: 'Loading...' })
							: $_('settings.locale_save', { default: 'Save language' })}
					</button>
				</div>
			</div>
		</div>

		<!-- Email notifications (issue #266) -->
		<div class="col-span-full mt-6">
			<Heading tag="h2" class="text-lg font-semibold text-gray-900 dark:text-white mb-3">
				{$_('settings.email_title')}
			</Heading>
			<div class="p-4 bg-gray-50 rounded border border-gray-200 dark:bg-gray-800 dark:border-gray-700">
				<p class="text-sm text-gray-600 dark:text-gray-400 mb-4">
					{$_('settings.email_description')}
				</p>

				<div class="space-y-4">
					<div>
						<label for="user-email" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
							{$_('settings.email_address')}
						</label>
						<div class="flex flex-wrap items-center gap-2">
							<input
								id="user-email"
								type="email"
								bind:value={email}
								placeholder="you@example.com"
								class="flex-1 min-w-[12rem] px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
							/>
							{#if emailVerified}
								<span class="text-sm font-medium text-green-600 dark:text-green-400">{$_('settings.email_verified')}</span>
							{/if}
						</div>
					</div>

					{#if !emailVerified}
						<button
							type="button"
							onclick={sendVerificationCode}
							disabled={sendingCode || !emailIsValid}
							class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
						>
							{sendingCode ? $_('settings.email_sending') : $_('settings.email_send_code')}
						</button>
					{/if}

					{#if verificationPending && !emailVerified}
						<div>
							<label for="user-email-verify-code" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
								{$_('settings.email_code')}
							</label>
							<div class="flex flex-wrap items-center gap-2">
								<input
									id="user-email-verify-code"
									type="text"
									inputmode="numeric"
									autocomplete="one-time-code"
									maxlength="6"
									bind:value={verifyCode}
									placeholder="123456"
									class="w-36 px-3 py-2 border border-gray-300 rounded-lg text-sm tracking-widest focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white"
								/>
								<button
									type="button"
									onclick={verifyEmailCode}
									disabled={verifyingCode || !verifyCode.trim()}
									class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
								>
									{verifyingCode ? $_('settings.email_verifying') : $_('settings.email_verify')}
								</button>
							</div>
						</div>
					{/if}

					<div class="flex items-center gap-3">
						<label for="user-email-enabled" class="relative inline-flex items-center cursor-pointer">
							<input
								id="user-email-enabled"
								type="checkbox"
								bind:checked={emailNotificationsEnabled}
								class="sr-only peer"
							/>
							<div class="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-blue-600 peer-focus:ring-2 peer-focus:ring-blue-300 dark:bg-gray-600 dark:peer-focus:ring-blue-800 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-full"></div>
						</label>
						<div>
							<span class="text-sm font-medium text-gray-700 dark:text-gray-300">{$_('settings.email_enable')}</span>
							<p class="text-xs text-gray-500 dark:text-gray-400">{$_('settings.email_enable_help')}</p>
						</div>
					</div>

					<button
						type="button"
						onclick={saveNotificationPreference}
						disabled={savingEmail}
						class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
					>
						{savingEmail ? $_('settings.email_saving') : $_('settings.email_save_preference')}
					</button>
				</div>
			</div>
		</div>

		<!-- Multi-quarter membership (issue #156) -->
		{#if showFederationUi}
		<div class="col-span-full mt-6">
			<Heading tag="h2" class="text-lg font-semibold text-gray-900 dark:text-white mb-3">
				{$_('settings.quarter_title', { default: 'Quarters' })}
			</Heading>

			<!-- 1. Activate / switch session among existing memberships -->
			<div class="p-4 bg-gray-50 rounded border border-gray-200 dark:bg-gray-800 dark:border-gray-700 mb-4">
				<h3 class="text-sm font-semibold text-gray-900 dark:text-white">
					{$_('settings.active_quarter')}
				</h3>
				<p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
					{$_('settings.quarter_switch_help')}
				</p>

				{#if loadingMembership}
					<div class="mt-3 text-sm text-gray-500">{$_('settings.quarter_checking')}</div>
				{:else if membershipError}
					<div class="mt-3 text-sm text-red-600 dark:text-red-400">{membershipError}</div>
				{:else if membershipHits.length === 0}
					<div class="mt-3 text-sm text-gray-500">{$_('settings.quarter_no_memberships')}</div>
				{:else}
					<ul class="mt-3 space-y-2">
						{#each membershipHits as hit (hit.canisterId)}
							<li class="flex flex-wrap items-center justify-between gap-2 rounded border border-gray-200 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-700">
								<div class="min-w-0">
									<div class="text-sm font-medium text-gray-900 dark:text-white">
										{labelForCanister(hit.canisterId)}
									</div>
									<div class="text-xs text-gray-500 dark:text-gray-400 font-mono break-all">
										{hit.canisterId}
									</div>
									{#if hit.profiles?.length}
										<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400 capitalize">
											{hit.profiles.join(', ')}
										</div>
									{/if}
								</div>
								{#if isSessionActive(hit)}
									<span class="text-xs font-medium text-green-700 dark:text-green-400">{$_('settings.quarter_in_use')}</span>
								{:else}
									<button
										type="button"
										onclick={() => handleActivate(hit)}
										disabled={!!activatingCanisterId}
										class="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
									>
										{activatingCanisterId === hit.canisterId ? $_('settings.quarter_switching') : $_('settings.quarter_use_this')}
									</button>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}

			</div>

			<!-- 2. Deliberate register in another quarter -->
			<div class="p-4 bg-gray-50 rounded border border-gray-200 dark:bg-gray-800 dark:border-gray-700">
				<h3 class="text-sm font-semibold text-gray-900 dark:text-white">
					{$_('settings.quarter_register_title')}
				</h3>
				<p class="mt-1 text-sm text-gray-600 dark:text-gray-400">
					{$_('settings.quarter_register_help')}
				</p>

				{#if loadingMembership}
					<div class="mt-3 text-sm text-gray-500">{$_('settings.quarter_loading_available')}</div>
				{:else if registerTargets.length === 0}
					<div class="mt-3 text-sm text-gray-500">
						{$_('settings.quarter_all_joined')}
					</div>
				{:else}
					<ul class="mt-3 space-y-2">
						{#each registerTargets as quarter (quarter.canister_id)}
							<li class="flex flex-wrap items-center justify-between gap-2 rounded border border-gray-200 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-700">
								<div class="min-w-0">
									<div class="text-sm font-medium text-gray-900 dark:text-white">
										{formatQuarterLabel(quarter as any)}
									</div>
									<div class="text-xs text-gray-500 dark:text-gray-400 font-mono break-all">
										{quarter.canister_id}
									</div>
									<div class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
										{$_('settings.quarter_users', { values: { count: quarter.population } })}
									</div>
								</div>
								<button
									type="button"
									onclick={() => handleRegister(quarter.canister_id)}
									disabled={!!registeringCanisterId || loadingUserStatus || !profiles.length}
									class="px-3 py-1.5 text-sm font-medium text-gray-800 bg-white border border-gray-300 rounded-lg hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed dark:bg-gray-600 dark:text-white dark:border-gray-500 dark:hover:bg-gray-500"
								>
									{registeringCanisterId === quarter.canister_id ? $_('settings.quarter_registering') : $_('settings.quarter_register_here')}
								</button>
							</li>
						{/each}
					</ul>
				{/if}

			</div>
		</div>
		{:else if $realmInfo.quarters.length === 1}
		<div class="col-span-full mt-6">
			<Heading tag="h2" class="text-lg font-semibold text-gray-900 dark:text-white mb-3">
				{$_('settings.quarter_title', { default: 'Quarter' })}
			</Heading>
			<div class="p-4 bg-gray-50 rounded border border-gray-200 dark:bg-gray-800 dark:border-gray-700">
				<span class="font-semibold">{$_('settings.quarter_current')}</span>
				<span class="ml-1">{labelForCanister($realmInfo.quarters[0]?.canister_id || sessionQuarterId)}</span>
			</div>
		</div>
		{/if}

		<!-- Registries (only shown when there are registries to display) -->
		{#if $realmInfo.registries.length > 0}
		<div class="col-span-full mt-6">
			<Heading tag="h2" class="text-lg font-semibold text-gray-900 dark:text-white mb-3">
				{$_('settings.registries_title', { default: 'Registries' })}
			</Heading>
			<div class="space-y-2">
				{#each $realmInfo.registries as reg (reg.canister_id)}
					<div class="p-3 bg-gray-50 rounded border border-gray-200 dark:bg-gray-800 dark:border-gray-700">
						<span class="font-semibold">{reg.canister_type}:</span>
						<span class="font-mono text-sm break-all ml-1">{reg.canister_id}</span>
					</div>
				{/each}
			</div>
		</div>
		{/if}

	</div>
</main>
