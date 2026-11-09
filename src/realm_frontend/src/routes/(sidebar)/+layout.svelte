<script>
	import '../../app.pcss';
	import Navbar from './Navbar.svelte';
	import Sidebar from './Sidebar.svelte';
	import Footer from './Footer.svelte';
	import DemoBanner from '$lib/components/DemoBanner.svelte';
	import DelegationBanner from '$lib/components/DelegationBanner.svelte';
	import ApprovalSentNotice from '$lib/components/ApprovalSentNotice.svelte';
	import PageBreadcrumb from '$lib/components/PageBreadcrumb.svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { browser } from '$app/environment';
	import { page } from '$app/stores';
	import { isAuthenticated } from '$lib/stores/auth';
	import { loadNotifications } from '$lib/stores/notifications';
	import { hostActionEvents, documentFocus } from '$lib/host-bridge';
	import { sidebarConfig } from '$lib/stores/sidebar';
	import { resolveHomePath } from '$lib/utils/home-path';
	import { portalFocusPush, portalAssistantOpen, isEmbeddedInPortal } from '$lib/portal-bridge.ts';
	import { realmInfo } from '$lib/stores/realmInfo';
	import {
		initialHideOnScrollState,
		nextHideOnScrollState,
	} from '$lib/utils/hideOnScroll';
	
	const SIDEBAR_STATE_KEY = 'realm_sidebar_state';
	
	let drawerHidden = true;
	let initialized = false;
	let embeddedInPortal = false;
	let bannerEl;
	let headerEl;
	let mainContentEl;
	let bannerHeight = 0;
	let headerHeight = 56;
	let headerHidden = false;
	let hideScrollState = initialHideOnScrollState();

	function isDesktopViewport() {
		return typeof window !== 'undefined' && window.innerWidth >= 1024;
	}

	function isPublicDashboardPath(pathname = get(page).url.pathname) {
		return pathname.includes('/extensions/public_dashboard');
	}

	function applySidebarVisibility({ forceMobileClosed = false } = {}) {
		if (!browser) return;

		if (!isDesktopViewport()) {
			// Keep a user-opened mobile drawer open across auth/store refreshes.
			// Only force-close on first paint, breakpoint change, navigation, or logout.
			if (!initialized || forceMobileClosed) {
				drawerHidden = true;
			}
			return;
		}

		if (!get(isAuthenticated) && isPublicDashboardPath()) {
			drawerHidden = true;
			return;
		}

		try {
			const savedSidebar = localStorage.getItem(SIDEBAR_STATE_KEY);
			if (savedSidebar !== null) {
				drawerHidden = JSON.parse(savedSidebar);
			} else {
				drawerHidden = false;
			}
		} catch {
			drawerHidden = false;
		}
	}
	
	function saveSidebarState(hidden) {
		if (!browser || !initialized || !isDesktopViewport()) return;
		if (!get(isAuthenticated) && isPublicDashboardPath()) return;
		try {
			localStorage.setItem(SIDEBAR_STATE_KEY, JSON.stringify(hidden));
		} catch {
			// sandbox / private mode
		}
	}
	
	$: if (browser && initialized) {
		saveSidebarState(drawerHidden);
	}

	$: isAnonymousLanding =
		!$isAuthenticated && $page.url.pathname.includes('/extensions/public_dashboard');

	$: hideDesktopSidebar = initialized ? drawerHidden : isAnonymousLanding;

	onMount(() => {
		if (browser) {
			embeddedInPortal = isEmbeddedInPortal();
			void realmInfo.fetch();
			document.documentElement.classList.remove('dark');
			document.documentElement.classList.add('light');
			document.body.classList.remove('overflow-hidden');

			applySidebarVisibility();

			initialized = true;

			// Only react when crossing the lg breakpoint — not on keyboard-open
			// viewport resizes (those fire `resize` but keep innerWidth < 1024).
			let wasDesktop = window.innerWidth >= 1024;

			const handleResize = () => {
				const isDesktop = window.innerWidth >= 1024;
				if (wasDesktop !== isDesktop) {
					applySidebarVisibility({ forceMobileClosed: !isDesktop });
					resetHideOnScroll();
				}
				wasDesktop = isDesktop;
			};
			
			window.addEventListener('resize', handleResize);

			measureBanner();
			measureHeader();
			const bannerObserver =
				typeof ResizeObserver !== 'undefined' && bannerEl
					? new ResizeObserver(measureBanner)
					: null;
			if (bannerEl) bannerObserver?.observe(bannerEl);
			const headerObserver =
				typeof ResizeObserver !== 'undefined' && headerEl
					? new ResizeObserver(measureHeader)
					: null;
			if (headerEl) headerObserver?.observe(headerEl);

			const handleMainScroll = () => applyHideOnScroll();
			mainContentEl?.addEventListener('scroll', handleMainScroll, { passive: true });

			// Chat UI lives on the mundus RegistryAssistant. When embedded in the
			// portal, forward assistant.open to the parent; otherwise ignore.
			// navigate.home reuses get_sidebar_manifests() → MY REALM → My Dashboard
			// and goes to /extensions/[that id]. Extensions never name the target.
			const unsubHostActions = hostActionEvents.subscribe((event) => {
				if (!event) return;
				if (event.action.type === 'assistant.open') {
					if (embeddedInPortal) {
						portalAssistantOpen();
					}
					return;
				}
				if (event.action.type === 'navigate.home') {
					void goto(resolveHomePath(get(sidebarConfig)));
				}
			});

			const unsubFocus = documentFocus.subscribe((focus) => {
				portalFocusPush(focus);
			});
			
			const unsubAuth = isAuthenticated.subscribe((auth) => {
				if (auth) void loadNotifications();
				applySidebarVisibility({ forceMobileClosed: !auth });
			});

			return () => {
				window.removeEventListener('resize', handleResize);
				mainContentEl?.removeEventListener('scroll', handleMainScroll);
				bannerObserver?.disconnect();
				headerObserver?.disconnect();
				unsubHostActions();
				unsubFocus();
				unsubAuth();
				document.body.classList.remove('overflow-hidden');
			};
		}
	});

	afterNavigate(() => {
		if (get(isAuthenticated)) void loadNotifications();
		applySidebarVisibility({ forceMobileClosed: true });
		resetHideOnScroll();
	});

	$: isFullBleedExtension =
		$page.url.pathname.includes('/extensions/codex_viewer') ||
		$page.url.pathname.includes('/extensions/zone_selector') ||
		$page.url.pathname.includes('/extensions/land_registry');

	$: isPaneBleedExtension = $page.url.pathname.includes('/extensions/public_dashboard');

	$: mobileDrawerOpen =
		browser && !drawerHidden && typeof window !== 'undefined' && window.innerWidth < 1024;

	function measureBanner() {
		if (bannerEl) {
			bannerHeight = bannerEl.offsetHeight || 0;
		}
	}

	function measureHeader() {
		if (headerEl) {
			headerHeight = headerEl.offsetHeight || headerHeight;
		}
	}

	function applyHideOnScroll() {
		if (!browser || !mainContentEl) return;

		const next = nextHideOnScrollState(hideScrollState, mainContentEl.scrollTop, {
			forceVisible: isDesktopViewport() || mobileDrawerOpen,
		});
		hideScrollState = next;
		headerHidden = next.hidden;
	}

	function resetHideOnScroll() {
		hideScrollState = initialHideOnScrollState();
		headerHidden = false;
	}

	$: if (browser && mobileDrawerOpen && headerHidden) {
		resetHideOnScroll();
	}
</script>

<div
	class="relative flex h-viewport flex-col overflow-hidden"
	style="--realm-banner-h: {bannerHeight}px"
>
	<div bind:this={bannerEl} class="shrink-0">
		<DemoBanner />
	</div>
	<header
		bind:this={headerEl}
		class="relative z-[70] mx-auto w-full border-b border-gray-200 bg-white transition-transform duration-200 ease-out motion-reduce:transition-none lg:flex-none max-lg:absolute max-lg:inset-x-0 max-lg:top-[var(--realm-banner-h)] {headerHidden
			? 'max-lg:-translate-y-full'
			: 'translate-y-0'}"
		data-mobile-header-hidden={headerHidden ? 'true' : 'false'}
	>
		<Navbar bind:drawerHidden />
	</header>
	<div
		class="relative flex min-h-0 flex-1 overflow-hidden bg-white {isFullBleedExtension
			? 'max-lg:pt-[var(--realm-mobile-header-h)]'
			: ''}"
		style="--realm-mobile-header-h: {headerHeight}px"
	>
		<!-- Sidebar (left, in-flow on lg; mobile drawer is a separate overlay) -->
		<Sidebar bind:drawerHidden desktopHidden={hideDesktopSidebar} />

		<!-- Main Content. On mobile the header overlays this pane so hiding it
		     reveals content instead of collapsing the flex row (which jumps). -->
		<div
			bind:this={mainContentEl}
			class="main-content-area relative min-w-0 flex-1 overflow-x-hidden bg-white max-lg:pb-[env(safe-area-inset-bottom,0px)] {isFullBleedExtension ? 'flex min-h-0 flex-col overflow-hidden' : 'overflow-y-auto'}"
			inert={mobileDrawerOpen ? true : undefined}
		>
			{#if !isFullBleedExtension}
				<div
					class="shrink-0 lg:hidden"
					style="height: {headerHeight}px"
					aria-hidden="true"
				></div>
			{/if}
			<DelegationBanner />
			<ApprovalSentNotice />

			<div
				class="{isFullBleedExtension ? 'flex min-h-0 flex-1 flex-col overflow-hidden' : ''}"
			>
				<div class="shrink-0 px-4 lg:px-6">
					<PageBreadcrumb />
				</div>
				<div
					class="{isFullBleedExtension
						? 'flex min-h-0 flex-1 flex-col overflow-hidden'
						: isPaneBleedExtension
							? ''
							: 'px-4 lg:px-6'}"
				>
					<slot />
					{#if !isFullBleedExtension}
						<Footer />
					{/if}
				</div>
			</div>
		</div>
	</div>
</div>
