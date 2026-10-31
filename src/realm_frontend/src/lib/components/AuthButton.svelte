<!-- src/lib/components/AuthButton.svelte -->
<script>
	import { login, logout, restoreAuthSession, resetAuthSessionRestore } from '$lib/auth';
	import { isEmbeddedInPortal, redirectToPortalLogin } from '$lib/portal-bridge.ts';
	import { markPortalSigningOut } from '$lib/portal-redirect-path.ts';
	import { isAuthenticated, userIdentity, principal } from '$lib/stores/auth';
	import { loadUserProfiles, resetProfileState, applyUserGetRecord, userProfiles, userDepartments, hasJoined } from '$lib/stores/profiles';
	import { formatProfileValues, formatDepartmentValues } from '$lib/utils/membershipLabels';
	import { activeQuarterId } from '$lib/stores/quarters';
	import { goto } from '$app/navigation';
	import { Avatar, Button } from 'flowbite-svelte';
	import { onMount } from 'svelte';
	import { slide, fade } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import { _ } from 'svelte-i18n';
	import T from '$lib/components/T.svelte';
	import { IconBuilding, IconCheck, IconCopy, IconLogin, IconUser } from '@tabler/icons-svelte';
	import { initBackendWithIdentity, setActiveQuarter } from '$lib/canisters';
	import { read } from '$lib/realm';
	import { userStatusFromMe } from '$lib/utils/federatedMembership';
	import { copyText } from '$lib/clipboard.js';
	import QuarterSwitcher from '$lib/components/QuarterSwitcher.svelte';
	import QuarterIndicator from '$lib/components/QuarterIndicator.svelte';

	export let showRealmControls = true;

	let principalText = '';
	let showDropdown = false;
	let copiedPrincipal = false;

	// Using the centralized profile loading function from the store

	onMount(async () => {
		// In test mode, do not auto-login — the join page shows an identity picker.
		const restored = await restoreAuthSession();
		if (restored.authenticated) {
			principalText = restored.principal;
			console.log('Principal restored from existing session:', principalText);
			await loadUserProfilePicture();
		}
		
		// Add a click handler to close dropdown when clicking outside
		const handleClickOutside = (event) => {
			if (showDropdown && !event.target.closest('.avatar-dropdown')) {
				showDropdown = false;
			}
		};
		
		document.addEventListener('click', handleClickOutside);
		
		return () => {
			document.removeEventListener('click', handleClickOutside);
		};
	});

	async function handleLogin(options = {}) {
		const { identity, principal: userPrincipal } = await login(options);
		if (!userPrincipal || !identity) return;
		isAuthenticated.set(true);
		principalText = userPrincipal.toText();

		userIdentity.set(principalText);
		principal.set(principalText);

		console.log('Principal at login:', principalText);
		await initBackendWithIdentity(identity);
		await loadUserProfiles();
		await loadUserProfilePicture();

		// Only redirect to dashboard if user is on a neutral page (root, join, welcome).
		// Don't redirect if they're already viewing a specific extension or page.
		const currentPath = window.location.pathname;
		const neutralPages = ['/', '/join', '/welcome', '/dashboard'];
		if (hasJoined() && neutralPages.includes(currentPath)) {
			goto('/extensions/member_dashboard');
		}
	}

	async function handleLogout() {
		await logout();
		resetAuthSessionRestore();
		isAuthenticated.set(false);
		principalText = '';
		userIdentity.set(null);
		principal.set('');
		
		// Reset the entire profile state instead of just clearing profiles array
		resetProfileState();

		// Clear persisted auth flags on logout
		if (typeof localStorage !== 'undefined') {
			localStorage.removeItem('auth_isAuthenticated');
			localStorage.removeItem('auth_userIdentity');
			localStorage.removeItem('auth_principal');
		}

		// Reset quarter routing
		if (typeof localStorage !== 'undefined') {
			localStorage.removeItem('home_quarter');
		}
		setActiveQuarter(null);

		console.log('Principal after logout:', principalText);
		showDropdown = false;
		markPortalSigningOut();
		goto('/join');
	}

	function generateAvatarUrl(seed, profilePictureUrl) {
		// Use profile picture URL if available, otherwise fall back to DiceBear
		if (profilePictureUrl && profilePictureUrl.trim()) {
			return profilePictureUrl;
		}
		// Use glass style for elegant colorful gradients
		return `https://api.dicebear.com/9.x/glass/svg?seed=${seed}`;
	}
	
	// Cedar profiles and department membership are different kinds of thing.
	$: profileValues = formatProfileValues($userProfiles);
	$: departmentValues = formatDepartmentValues($userDepartments);
	
	function goToSettings() {
		showDropdown = false;
		goto('/settings');
	}

	let userProfilePictureUrl = '';

	async function loadUserProfilePicture() {
		try {
			const response = userStatusFromMe(await read('me'));
			if (response.success) {
				const userGet = response.data.userGet;
				userProfilePictureUrl = userGet.avatar || '';
				applyUserGetRecord(userGet);
			}
		} catch (error) {
			console.error('Error loading user profile picture:', error);
			userProfilePictureUrl = '';
		}
	}

	// Reload avatar + department membership when this tab's quarter changes.
	let lastQuarterKey;
	$: if ($isAuthenticated) {
		const key = $activeQuarterId ?? '';
		if (lastQuarterKey !== undefined && lastQuarterKey !== key) {
			loadUserProfilePicture();
		}
		lastQuarterKey = key;
	}

	// Listen for profile picture updates from settings page
	function handleProfilePictureUpdate(event) {
		if (event.detail && event.detail.profilePictureUrl !== undefined) {
			userProfilePictureUrl = event.detail.profilePictureUrl;
		}
	}

	onMount(() => {
		// Listen for custom events when profile picture is updated
		window.addEventListener('profilePictureUpdated', handleProfilePictureUpdate);
		
		return () => {
			window.removeEventListener('profilePictureUpdated', handleProfilePictureUpdate);
		};
	});

	// Get a shortened display version of the principal
	$: shortPrincipal = $principal ? `${$principal.substring(0, 8)}...${$principal.slice(-8)}` : '';
	
	// Toggle dropdown without event propagation
	function toggleDropdown(event) {
		event.stopPropagation();
		showDropdown = !showDropdown;
	}

	async function copyPrincipal(event) {
		event.stopPropagation();
		if (!$principal) return;
		if (await copyText($principal)) {
			copiedPrincipal = true;
			setTimeout(() => {
				copiedPrincipal = false;
			}, 2000);
		} else {
			console.error('Failed to copy principal');
		}
	}
</script>


<div class="flex items-center">
{#if $isAuthenticated}
	<div class="avatar-dropdown relative">
		<!-- Avatar Image -->
		<div 
			class="cursor-pointer" 
			role="button"
			aria-haspopup="true"
			aria-expanded={showDropdown}
			tabindex="0"
			on:click={toggleDropdown}
			on:keydown={(e) => {
				if (e.key === 'Enter' || e.key === ' ') {
					toggleDropdown(e);
				}
			}}
		>
		<Avatar 
			src={generateAvatarUrl($principal, userProfilePictureUrl)} 
			tabindex={0} 
			title={`Principal: ${$principal}`} 
			alt="User avatar"
		/>
		</div>
		
		<!-- Dropdown Menu -->
		{#if showDropdown}
			<div 
				class="absolute right-0 mt-2 w-56 z-50 bg-white rounded-lg shadow-xl border border-gray-200 dark:bg-gray-800 dark:border-gray-700 origin-top-right" 
				role="menu"
				transition:slide={{ duration: 200, easing: quintOut }}
			>
				<div class="px-4 py-3">
					<div class="flex items-center gap-1.5 min-w-0">
						<p class="text-sm text-gray-900 dark:text-white truncate font-medium flex-1 min-w-0">
							{shortPrincipal}
						</p>
						<button
							type="button"
							class="shrink-0 p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 {copiedPrincipal
								? 'text-green-600 dark:text-green-400'
								: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200'}"
							title={copiedPrincipal ? 'Copied!' : 'Copy principal'}
							aria-label={copiedPrincipal ? 'Copied' : 'Copy principal'}
							on:click={copyPrincipal}
						>
							{#if copiedPrincipal}
								<IconCheck size={14} />
							{:else}
								<IconCopy size={14} />
							{/if}
						</button>
					</div>
					<div class="mt-1 space-y-1 text-xs text-gray-500 dark:text-gray-400">
						<div
							class="flex items-center gap-1.5 min-w-0"
							title={$_('common.profile', { default: 'Profile' })}
							aria-label={$_('common.profile', { default: 'Profile' })}
						>
							<IconUser size={14} class="shrink-0" aria-hidden="true" />
							<span class="truncate">{profileValues}</span>
						</div>
						<div
							class="flex items-center gap-1.5 min-w-0"
							title={$_('common.dept', { default: 'Dept' })}
							aria-label={$_('common.dept', { default: 'Dept' })}
						>
							<IconBuilding size={14} class="shrink-0" aria-hidden="true" />
							<span class="truncate">{departmentValues}</span>
						</div>
					</div>
				</div>
				{#if showRealmControls}
					<QuarterSwitcher variant="menu" />
					<QuarterIndicator variant="menu" />
				{/if}
				<hr class="h-px bg-gray-200 border-0 dark:bg-gray-700">
				<button 
					class="block w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
					role="menuitem"
					on:click={goToSettings}
				>
					<T key="common.settings" default_text="Settings" />
				</button>
				<hr class="h-px bg-gray-200 border-0 dark:bg-gray-700">
				<button 
					class="block w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
					role="menuitem"
					on:click={handleLogout}
				>
					<T key="common.logout" default_text="Log Out" />
				</button>
			</div>
		{/if}
	</div>
{:else}
	<button
		on:click={() => {
			if (isEmbeddedInPortal()) {
				redirectToPortalLogin('/join');
				return;
			}
			goto('/join');
		}}
		class="group inline-flex items-center gap-1.5 p-2 rounded-lg hover:bg-gray-100 transition-colors duration-200 text-gray-500 hover:text-gray-700"
		title={$_('common.sign_in', { default: 'Sign in' })}
		aria-label={$_('common.sign_in', { default: 'Sign in' })}
	>
		<IconLogin size={22} />
		<span
			class="hidden md:inline-block md:max-w-0 md:overflow-hidden md:opacity-0 md:group-hover:max-w-[8rem] md:group-hover:opacity-100 md:group-focus-visible:max-w-[8rem] md:group-focus-visible:opacity-100 transition-[max-width,opacity] duration-500 ease-in-out text-sm font-medium whitespace-nowrap"
		>
			{$_('common.sign_in', { default: 'Sign in' })}
		</span>
	</button>
{/if}
</div>
