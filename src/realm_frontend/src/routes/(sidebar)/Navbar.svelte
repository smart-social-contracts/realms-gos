<script>
	import AuthButton from '$lib/components/AuthButton.svelte';
	import DelegationSwitcher from '$lib/components/DelegationSwitcher.svelte';
	import { REALM_DEFAULT_MARK, resolveRealmMarkSrc } from '$lib/branding/realmMark';
	import { realmInfo, realmName } from '$lib/stores/realmInfo';
	import { unreadCount } from '$lib/stores/notifications';
	import { isEmbeddedInPortal } from '$lib/portal-bridge.ts';
	import { onMount } from 'svelte';
	import { Navbar } from 'flowbite-svelte';
	import { IconMenu2 } from '@tabler/icons-svelte';
	import '../../app.pcss';

	export let fluid = true;
	export let drawerHidden = false;
	export let showMenu = true;
	export let showRealmControls = true;

	const hideRealmName = isEmbeddedInPortal();

	// The realm's own mark first. Only when it fails to load does the default one take its place (never before:
	// a realm with a logo must not flash the default). If that fails too, no mark is drawn.
	let markFailed = false;
	let defaultFailed = false;
	let lastMarkSrc = '';

	$: markSrc = resolveRealmMarkSrc($realmInfo.logoUrl);
	$: if (markSrc !== lastMarkSrc) {
		lastMarkSrc = markSrc;
		markFailed = false;
		defaultFailed = false;
	}
	$: shownMark = !markSrc || defaultFailed ? '' : markFailed ? REALM_DEFAULT_MARK : markSrc;

	onMount(() => {
		realmInfo.fetch();
	});
</script>

<Navbar {fluid} class="text-black relative z-50" color="default" style="pointer-events: auto;">
	<div class="grid w-full min-w-0 flex-1 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2">
		<div class="flex shrink-0 items-center">
			{#if showMenu}
				<button
					type="button"
					on:click={() => (drawerHidden = !drawerHidden)}
					class="relative m-0 inline-flex p-2 rounded-lg hover:bg-gray-100 transition-colors duration-200 text-gray-600 hover:text-gray-900"
					aria-label={$unreadCount > 0 ? 'Toggle navigation menu (unread messages)' : 'Toggle navigation menu'}
					aria-expanded={!drawerHidden}
				>
					<IconMenu2 size={22} />
					{#if $unreadCount > 0}
						<span
							class="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-red-500 ring-2 ring-white"
							aria-hidden="true"
						></span>
					{/if}
				</button>
			{/if}
		</div>

		<div class="flex min-w-0 items-center justify-center">
			<a href="/" class="flex min-w-0 max-w-full items-center">
				{#if shownMark}
					<img
						src={shownMark}
						class="h-8 shrink-0 sm:h-10 pointer-events-none"
						alt={hideRealmName ? $realmName || 'Realm' : ''}
						on:error={() => (markFailed ? (defaultFailed = true) : (markFailed = true))}
					/>
				{/if}
				{#if !hideRealmName}
					<span
						class="ml-2 min-w-0 truncate self-center text-lg font-medium text-gray-700 sm:ml-3 sm:text-2xl pointer-events-none"
					>
						{$realmName || ''}
					</span>
				{/if}
			</a>
		</div>

		<div class="flex min-w-0 items-center justify-end gap-1 text-gray-500 sm:gap-2">
			{#if showRealmControls}
				<DelegationSwitcher />
			{/if}
			<AuthButton {showRealmControls} />
		</div>
	</div>
</Navbar>
