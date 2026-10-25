<script lang="ts">
	import { afterNavigate } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount, tick } from 'svelte';
	import { get } from 'svelte/store';
	import type { SidebarConfig } from '$lib/config/sidebar';
	import { locale, _ } from 'svelte-i18n';
	
	import { styles, cn } from '$lib/theme/utilities';
	import { topUtilityItems } from '$lib/config/sidebar';
	import { sidebarConfig, sidebarLoading, loadSidebar } from '$lib/stores/sidebar';
	import { profilesLoading, userProfiles } from '$lib/stores/profiles';
	import { isAuthenticated } from '$lib/stores/auth';
	import { shouldShowMeSection, visibleSidebarCategories, isRealmMember } from '$lib/utils/sidebar-member-chrome';
	import { unreadCount } from '$lib/stores/notifications';
	import { getTablerIcon } from '$lib/utils/tablerIcons';
	import {
		activeSidebarFoldIds,
		navigationTargetKey,
		sidebarFoldExpandKey,
		sidebarItemIsActive,
	} from '$lib/utils/sidebar-active';
	import { IconLogin, IconLayoutDashboard } from '@tabler/icons-svelte';
	import SidebarFold from './SidebarFold.svelte';
	// @ts-ignore
	import { backend, quarterBackendStore } from '$lib/canisters';

	export let drawerHidden: boolean = false;
	export let desktopHidden: boolean = false;
	
	let showScrollIndicator = true;
	let sidebarContainer: HTMLElement;
	let lastSidebarActor: unknown = null;
	let lastLocale = '';

	const ACTIVE_ITEM_CLASSES =
		'sidebar-nav-active bg-gray-200 text-gray-900 font-medium hover:bg-gray-200 hover:text-gray-900 dark:bg-[var(--color-gray-700)] dark:hover:bg-[var(--color-gray-600)]';

	$: navPathname = $page.url.pathname;
	$: navSearch = $page.url.search;
	$: showMeSection = shouldShowMeSection($isAuthenticated, $userProfiles);
	$: sidebarCategories = visibleSidebarCategories($sidebarConfig?.categories, $userProfiles);

	function isActive(href: string, pathname = navPathname, search = navSearch): boolean {
		return sidebarItemIsActive({ href }, pathname, search);
	}

	function itemClasses(href: string, pathname: string, search: string): string {
		return cn(
			styles.sidebar.item(),
			isActive(href, pathname, search) ? cn(ACTIVE_ITEM_CLASSES, styles.sidebar.itemActive()) : '',
		);
	}

	function iconClasses(href: string, extra = '', pathname = navPathname, search = navSearch): string {
		return cn(
			extra,
			isActive(href, pathname, search) ? 'text-gray-900' : 'text-gray-500 group-hover:text-gray-900',
		);
	}

	// Bindable open flags (false = collapsed). Must be a template-bound
	// object so Svelte 5 emits mutable_source. Live #315 computed open via
	// a helper over a Set the template never read; the compiler left that
	// Set as a plain let and taps never re-rendered.
	let foldOpen: Record<string, boolean> = {
		__section_me__: false,
		__section_realm__: false,
		__section_mundus__: false,
	};
	let categoriesInitialized = false;
	let knownCategoryIds = new Set<string>();

	function defaultFoldOpen(config: SidebarConfig): Record<string, boolean> {
		const next: Record<string, boolean> = {
			__section_me__: false,
			__section_realm__: false,
		};
		if (config.mundusItems.length > 0) {
			next.__section_mundus__ = false;
		}
		for (const category of config.categories) {
			next[category.id] = false;
		}
		return next;
	}

	function initFoldOpen(config: SidebarConfig) {
		if (!categoriesInitialized) {
			categoriesInitialized = true;
			knownCategoryIds = new Set(config.categories.map((category) => category.id));
			// Keep any tap that happened before get_sidebar resolved.
			foldOpen = { ...defaultFoldOpen(config), ...foldOpen };
			return;
		}

		let changed = false;
		for (const category of config.categories) {
			if (!knownCategoryIds.has(category.id)) {
				knownCategoryIds.add(category.id);
				foldOpen[category.id] = false;
				changed = true;
			}
		}
		if (changed) {
			foldOpen = foldOpen;
		}
	}

	let userHasToggledFolds = false;

	function setFoldOpen(id: string, nextOpen: boolean) {
		userHasToggledFolds = true;
		foldOpen[id] = nextOpen;
		foldOpen = foldOpen;
	}

	// get_sidebar resolves visibility from the *caller's* user record, which in
	// a federation lives on the member's home quarter — not the capital. Route
	// through the quarter-aware actor and reload whenever it swaps (quarter
	// activation, re-authentication), otherwise the capital answers with the
	// guest menu and admin/member extensions vanish from the sidebar.
	$: {
		const actor = $quarterBackendStore || backend;
		const currentLocale = $locale || 'en';
		if (actor && (actor !== lastSidebarActor || currentLocale !== lastLocale)) {
			lastSidebarActor = actor;
			lastLocale = currentLocale;
			loadSidebar(actor, currentLocale);
		}
	}

	function categoryLabel(category: { id: string; label: string }): string {
		return $_(`categories.${category.id}`, { default: category.label });
	}
	
	function checkScrollPosition() {
		if (sidebarContainer) {
			const { scrollTop, scrollHeight, clientHeight } = sidebarContainer;
			showScrollIndicator = scrollTop + clientHeight < scrollHeight - 20;
		}
	}
	
	onMount(() => {
		if (sidebarContainer) {
			sidebarContainer.addEventListener('scroll', checkScrollPosition);
			checkScrollPosition();
		}
		return () => {
			if (sidebarContainer) {
				sidebarContainer.removeEventListener('scroll', checkScrollPosition);
			}
		};
	});
	
	function closeDrawer() {
		drawerHidden = true;
	}

	function handleNavClick() {
		if (typeof window !== 'undefined' && window.innerWidth < 1024) {
			closeDrawer();
		}
	}

	afterNavigate((nav) => {
		document.getElementById('svelte')?.scrollTo({ top: 0 });
		if (navigationTargetKey(nav.from) !== navigationTargetKey(nav.to)) {
			userHasToggledFolds = false;
			lastFoldExpandKey = '';
		}
	});

	function expandForActivePage(
		config: SidebarConfig,
		pathname = navPathname,
		search = navSearch,
	) {
		// Always start from the folded default, then open only the sections that
		// contain the current route. Avoids stale expanded categories from prior
		// navigation or old localStorage entries.
		const next = defaultFoldOpen(config);
		for (const id of activeSidebarFoldIds(config, pathname, search, get(userProfiles))) {
			next[id] = true;
		}
		foldOpen = next;
	}

	function scrollActiveIntoView() {
		if (!sidebarContainer) return;

		requestAnimationFrame(() => {
			const activeLink = sidebarContainer.querySelector('[data-sidebar-active="true"]');
			if (!activeLink) return;

			const containerRect = sidebarContainer.getBoundingClientRect();
			const linkRect = activeLink.getBoundingClientRect();
			const offset =
				linkRect.top - containerRect.top - containerRect.height / 2 + linkRect.height / 2;

			sidebarContainer.scrollTo({
				top: sidebarContainer.scrollTop + offset,
				behavior: 'smooth',
			});
			checkScrollPosition();
		});
	}

	$: if ($sidebarConfig) {
		initFoldOpen($sidebarConfig);
	}

	// Expand folds for the current page. Key includes the resolved fold ids so
	// a stale sidebar cache (no Justice yet) re-expands when get_sidebar lands.
	// userHasToggledFolds still blocks snapping a manual collapse back open.
	let lastFoldExpandKey = '';
	$: if ($sidebarConfig && $isAuthenticated && $page.url.pathname && !userHasToggledFolds) {
		const path = $page.url.pathname;
		const search = $page.url.search;
		const membership = isRealmMember($userProfiles) ? 'member' : 'guest';
		const foldIds = activeSidebarFoldIds($sidebarConfig, path, search, $userProfiles);
		const key = sidebarFoldExpandKey(path, search, membership, foldIds);
		if (lastFoldExpandKey !== key) {
			expandForActivePage($sidebarConfig, path, search);
			lastFoldExpandKey = key;
			void tick().then(() => scrollActiveIntoView());
		}
	}

	function sidebarTooltip(node: HTMLElement, text: string | undefined) {
		if (!text) return {};
		let tip: HTMLDivElement | null = null;

		function show() {
			const rect = node.getBoundingClientRect();
			tip = document.createElement('div');
			tip.textContent = text;
			tip.className = [
				'fixed z-[9999] px-2.5 py-1.5',
				'bg-gray-900 text-white text-xs rounded-md',
				'pointer-events-none whitespace-nowrap shadow-lg',
				'transition-opacity duration-150'
			].join(' ');
			tip.style.top = `${rect.top + rect.height / 2}px`;
			tip.style.left = `${rect.right + 10}px`;
			tip.style.transform = 'translateY(-50%)';
			document.body.appendChild(tip);
		}

		function hide() {
			tip?.remove();
			tip = null;
		}

		node.addEventListener('mouseenter', show);
		node.addEventListener('mouseleave', hide);

		return {
			update(newText: string | undefined) {
				text = newText ?? '';
			},
			destroy() {
				node.removeEventListener('mouseenter', show);
				node.removeEventListener('mouseleave', hide);
				hide();
			}
		};
	}

</script>

<!-- Mobile drawer: fill the pane under the header (no guessed top-16 gap). -->
<div
	class="mobile-nav-drawer absolute inset-0 z-[60] lg:hidden {drawerHidden ? 'pointer-events-none' : ''}"
	role="dialog"
	aria-modal={!drawerHidden}
	aria-hidden={drawerHidden}
	inert={drawerHidden ? true : undefined}
	aria-label={$_('chrome.navigation_menu')}
>
	<button
		type="button"
		class="drawer-backdrop absolute inset-0 border-0 bg-gray-900/50 p-0 cursor-pointer touch-manipulation {drawerHidden ? 'is-closed' : ''}"
		aria-label={$_('chrome.close_menu')}
		tabindex={drawerHidden ? -1 : 0}
		onclick={closeDrawer}
	></button>
	<aside
		class="drawer-panel absolute inset-y-0 left-0 z-10 flex w-64 max-w-[85vw] flex-col border-r border-gray-200 bg-white shadow-xl touch-manipulation {drawerHidden ? 'is-closed' : ''}"
	>
			<h4 class="sr-only">{$_('chrome.main_menu')}</h4>
			<div
				class={cn(styles.sidebar.container(), 'overflow-y-auto h-full px-3 pb-12 scrollbar-hide overscroll-contain')}
			>
				<nav>
					{#if !$isAuthenticated}
						<ul class="pt-5 pb-1 space-y-1">
							<li class="px-3 pb-2">
								<p class="text-xs text-gray-500 leading-relaxed">
									{$_('chrome.sign_in_prompt')}
								</p>
							</li>
							<li>
								<a href="/join" class={cn(styles.sidebar.item(), 'font-medium')} onclick={handleNavClick}>
									<IconLogin size={22} class="flex-shrink-0 w-5 h-5 text-gray-500 group-hover:text-gray-900" />
									<span class="ml-3">{$_('chrome.sign_in')}</span>
								</a>
							</li>
							<li>
								<a
									href="/extensions/public_dashboard"
									class={itemClasses('/extensions/public_dashboard', $page.url.pathname, $page.url.search)}
									data-sidebar-active={isActive('/extensions/public_dashboard', $page.url.pathname, $page.url.search) ? 'true' : undefined}
									aria-current={isActive('/extensions/public_dashboard', $page.url.pathname, $page.url.search) ? 'page' : undefined}
									onclick={handleNavClick}
								>
									<IconLayoutDashboard size={22} class={iconClasses('/extensions/public_dashboard', 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
									<span class="ml-3">{$_('chrome.public_dashboard')}</span>
								</a>
							</li>
						</ul>
					{:else}
						{#if showMeSection}
						<div class="pt-5 pb-1">
							<SidebarFold bind:open={foldOpen['__section_me__']} setOpen={(nextOpen) => setFoldOpen('__section_me__', nextOpen)}>
								<div slot="header" class="flex w-full min-w-0 items-center justify-between">
									<h3 class={styles.sidebar.sectionHeader()}>{$_('chrome.me')}</h3>
									<svg class="fold-chevron w-3.5 h-3.5 text-gray-400 " fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>
								</div>
							<ul class="pb-1 space-y-1">
								{#each topUtilityItems as item}
									{@const IconComp = getTablerIcon(item.icon)}
									<li>
										<a href={item.href} use:sidebarTooltip={item.tooltipKey ? $_(item.tooltipKey) : undefined} class={itemClasses(item.href, $page.url.pathname, $page.url.search)} data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined} aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined} onclick={handleNavClick}>
											<span class="relative flex-shrink-0">
												<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'w-5 h-5', $page.url.pathname, $page.url.search)} />
												{#if item.href === '/messages' && $unreadCount > 0}
													<span class="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-red-500 ring-2 {isActive(item.href, $page.url.pathname, $page.url.search) ? 'ring-gray-200' : 'ring-white'}" aria-hidden="true"></span>
												{/if}
											</span>
											<span class="ml-3">{$_(item.labelKey)}</span>
										</a>
									</li>
								{/each}
							</ul>
							</SidebarFold>
						</div>
						{/if}
						{#if $profilesLoading || $sidebarLoading}
							<div class="py-4 flex items-center justify-center">
								<div class="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-900"></div>
							</div>
						{/if}
						{#if $sidebarConfig}
							<div class="pt-3 pb-1">
							<SidebarFold bind:open={foldOpen['__section_realm__']} setOpen={(nextOpen) => setFoldOpen('__section_realm__', nextOpen)}>
								<div slot="header" class="flex w-full min-w-0 items-center justify-between">
									<h3 class={styles.sidebar.sectionHeader()}>{$_('chrome.my_realm')}</h3>
									<svg class="fold-chevron w-3.5 h-3.5 text-gray-400 " fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>
								</div>
								<ul class="pb-1 space-y-1">
									{#each $sidebarConfig.welcomeItems as item (item.href)}
										{@const IconComp = getTablerIcon(item.icon)}
										<li>
											<a href={item.href} use:sidebarTooltip={item.tooltip} class={itemClasses(item.href, $page.url.pathname, $page.url.search)} data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined} aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined} onclick={handleNavClick}>
												<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
												<span class="ml-3">{item.label}</span>
											</a>
										</li>
									{/each}
								</ul>
								{#each sidebarCategories as category (category.id)}
									<div class="pt-2 pb-1 px-3">
									<SidebarFold
										bind:open={foldOpen[category.id]}
										summaryClass="flex items-center justify-between w-full cursor-pointer bg-transparent p-0 group/cat"
										setOpen={(nextOpen) => setFoldOpen(category.id, nextOpen)}
									>
										<div slot="header" class="flex w-full min-w-0 items-center justify-between">
											<h3 class={styles.sidebar.categoryHeader()}>{categoryLabel(category)}</h3>
											<svg class="fold-chevron w-3.5 h-3.5 text-gray-400 " fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>
										</div>
										<ul class="pb-1 space-y-1">
											{#each category.items as item (item.href)}
												{@const IconComp = getTablerIcon(item.icon)}
												<li>
													<a href={item.href} use:sidebarTooltip={item.tooltip} class={itemClasses(item.href, $page.url.pathname, $page.url.search)} data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined} aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined} onclick={handleNavClick}>
														<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
														<span class="ml-3">{item.label}</span>
													</a>
												</li>
											{/each}
										</ul>
									</SidebarFold>
									</div>
								{/each}
							</SidebarFold>
							</div>
							{#if $sidebarConfig.mundusItems.length > 0}
								<div class="pt-3 pb-1">
								<SidebarFold
									bind:open={foldOpen['__section_mundus__']}
									setOpen={(nextOpen) => setFoldOpen('__section_mundus__', nextOpen)}
								>
									<div slot="header" class="flex w-full min-w-0 items-center justify-between">
										<h3 class={styles.sidebar.sectionHeader()}>{$_('chrome.my_mundus')}</h3>
										<svg class="fold-chevron w-3.5 h-3.5 text-gray-400 " fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" /></svg>
									</div>
									<ul class="pb-1 space-y-1">
										{#each $sidebarConfig.mundusItems as item (item.href)}
											{@const IconComp = getTablerIcon(item.icon)}
											<li>
												<a href={item.href} use:sidebarTooltip={item.tooltip} class={itemClasses(item.href, $page.url.pathname, $page.url.search)} data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined} aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined} onclick={handleNavClick}>
													<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
													<span class="ml-3">{item.label}</span>
												</a>
											</li>
										{/each}
									</ul>
								</SidebarFold>
								</div>
							{/if}
						{/if}
					{/if}
				</nav>
			</div>
		</aside>
</div>

<!-- Desktop sidebar (in-flow beside main; do not pair fixed + ml-64) -->
<aside
	class="relative isolate hidden lg:flex lg:shrink-0 flex-col min-h-0 h-full overflow-hidden bg-white z-30 transition-[width] duration-200 ease-out motion-reduce:transition-none {desktopHidden ? 'w-0 min-w-0 border-r-0 pointer-events-none' : 'w-64 border-r border-gray-200'}"
	aria-hidden={desktopHidden ? true : undefined}
	inert={desktopHidden ? true : undefined}
>
	<h4 class="sr-only">{$_('chrome.main_menu')}</h4>
	<div
		bind:this={sidebarContainer}
		class={cn(styles.sidebar.container(), 'w-64 overflow-y-auto h-full px-3 pb-12 scrollbar-hide overscroll-contain border-r-0')}
	>
		<nav>
			{#if !$isAuthenticated}
				<ul class="pt-5 lg:pt-3 pb-1 space-y-1">
					<li class="px-3 pb-2">
						<p class="text-xs text-gray-500 leading-relaxed">
							{$_('chrome.sign_in_prompt')}
						</p>
					</li>
					<li>
						<a
							href="/join"
							class={cn(styles.sidebar.item(), 'font-medium')}
						>
							<IconLogin size={22} class="flex-shrink-0 w-5 h-5 text-gray-500 group-hover:text-gray-900" />
							<span class="ml-3">{$_('chrome.sign_in')}</span>
						</a>
					</li>
					<li>
						<a
							href="/extensions/public_dashboard"
							class={itemClasses('/extensions/public_dashboard', $page.url.pathname, $page.url.search)}
							data-sidebar-active={isActive('/extensions/public_dashboard', $page.url.pathname, $page.url.search) ? 'true' : undefined}
							aria-current={isActive('/extensions/public_dashboard', $page.url.pathname, $page.url.search) ? 'page' : undefined}
						>
							<IconLayoutDashboard size={22} class={iconClasses('/extensions/public_dashboard', 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
							<span class="ml-3">{$_('chrome.public_dashboard')}</span>
						</a>
					</li>
				</ul>
			{:else}
			{#if showMeSection}
			<!-- ME section (super-category) -->
			<div class="pt-5 lg:pt-3 pb-1">
			<SidebarFold bind:open={foldOpen['__section_me__']} setOpen={(nextOpen) => setFoldOpen('__section_me__', nextOpen)}>
				<div slot="header" class="flex w-full min-w-0 items-center justify-between">
					<h3 class={styles.sidebar.sectionHeader()}>
						{$_('chrome.me')}
					</h3>
					<svg
						class="fold-chevron w-3.5 h-3.5 text-gray-400 "
						fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
					</svg>
				</div>
				<ul class="pb-1 space-y-1">
					{#each topUtilityItems as item}
						{@const IconComp = getTablerIcon(item.icon)}
						<li>
							<a 
								href={item.href}
								use:sidebarTooltip={item.tooltipKey ? $_(item.tooltipKey) : undefined}
								class={itemClasses(item.href, $page.url.pathname, $page.url.search)}
								data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined}
								aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined}
							>
								<span class="relative flex-shrink-0">
									<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'w-5 h-5', $page.url.pathname, $page.url.search)} />
									{#if item.href === '/messages' && $unreadCount > 0}
										<span
											class="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-red-500 ring-2 {isActive(item.href, $page.url.pathname, $page.url.search) ? 'ring-gray-200' : 'ring-white'}"
											aria-hidden="true"
										></span>
									{/if}
								</span>
								<span class="ml-3">{$_(item.labelKey)}</span>
							</a>
						</li>
					{/each}
				</ul>
			</SidebarFold>
			</div>
			{/if}

			<!-- Loading State -->
			{#if $profilesLoading || $sidebarLoading}
				<div class="py-4 flex items-center justify-center">
					<div class="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-900"></div>
				</div>
			{/if}

			<!-- MY REALM section (super-category) -->
			{#if $sidebarConfig}
				<div class="pt-3 pb-1">
				<SidebarFold bind:open={foldOpen['__section_realm__']} setOpen={(nextOpen) => setFoldOpen('__section_realm__', nextOpen)}>
					<div slot="header" class="flex w-full min-w-0 items-center justify-between">
						<h3 class={styles.sidebar.sectionHeader()}>
							{$_('chrome.my_realm')}
						</h3>
						<svg
							class="fold-chevron w-3.5 h-3.5 text-gray-400 "
							fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
						>
							<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
						</svg>
					</div>
					<!-- Welcome items (My Dashboard, etc.) -->
					<ul class="pb-1 space-y-1">
						{#each $sidebarConfig.welcomeItems as item (item.href)}
							{@const IconComp = getTablerIcon(item.icon)}
							<li>
								<a 
									href={item.href}
									use:sidebarTooltip={item.tooltip}
									class={itemClasses(item.href, $page.url.pathname, $page.url.search)}
									data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined}
									aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined}
								>
									<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
									<span class="ml-3">{item.label}</span>
								</a>
							</li>
						{/each}
					</ul>

					<!-- Category sections (collapsible) -->
					{#each sidebarCategories as category (category.id)}
						<div class="pt-2 pb-1 px-3">
						<SidebarFold
							bind:open={foldOpen[category.id]}
							summaryClass="flex items-center justify-between w-full cursor-pointer bg-transparent p-0 group/cat"
							setOpen={(nextOpen) => setFoldOpen(category.id, nextOpen)}
						>
							<div slot="header" class="flex w-full min-w-0 items-center justify-between">
								<h3 class={styles.sidebar.categoryHeader()}>
									{categoryLabel(category)}
								</h3>
								<svg
									class="fold-chevron w-3.5 h-3.5 text-gray-400 "
									fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
								</svg>
							</div>
							<ul class="pb-1 space-y-1">
								{#each category.items as item (item.href)}
									{@const IconComp = getTablerIcon(item.icon)}
									<li>
										<a 
											href={item.href}
											use:sidebarTooltip={item.tooltip}
											class={itemClasses(item.href, $page.url.pathname, $page.url.search)}
											data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined}
											aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined}
										>
											<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
											<span class="ml-3">{item.label}</span>
										</a>
									</li>
								{/each}
							</ul>
						</SidebarFold>
						</div>
					{/each}
				</SidebarFold>
				</div>

				<!-- MY MUNDUS section (super-category) -->
				{#if $sidebarConfig.mundusItems.length > 0}
					<div class="pt-3 pb-1">
					<SidebarFold
						bind:open={foldOpen['__section_mundus__']}
						setOpen={(nextOpen) => setFoldOpen('__section_mundus__', nextOpen)}
					>
						<div slot="header" class="flex w-full min-w-0 items-center justify-between">
							<h3 class={styles.sidebar.sectionHeader()}>
								{$_('chrome.my_mundus')}
							</h3>
							<svg
								class="fold-chevron w-3.5 h-3.5 text-gray-400 "
								fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
							>
								<path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
							</svg>
						</div>
						<ul class="pb-1 space-y-1">
							{#each $sidebarConfig.mundusItems as item (item.href)}
								{@const IconComp = getTablerIcon(item.icon)}
								<li>
									<a 
										href={item.href}
										use:sidebarTooltip={item.tooltip}
										class={itemClasses(item.href, $page.url.pathname, $page.url.search)}
										data-sidebar-active={isActive(item.href, $page.url.pathname, $page.url.search) ? 'true' : undefined}
										aria-current={isActive(item.href, $page.url.pathname, $page.url.search) ? 'page' : undefined}
									>
										<svelte:component this={IconComp} size={22} class={iconClasses(item.href, 'flex-shrink-0 w-5 h-5', $page.url.pathname, $page.url.search)} />
										<span class="ml-3">{item.label}</span>
									</a>
								</li>
							{/each}
						</ul>
					</SidebarFold>
					</div>
				{/if}
			{/if}
			{/if}
		</nav>
	</div>

	<!-- Fixed scroll indicator at bottom of sidebar -->
	{#if showScrollIndicator}
		<div class="absolute bottom-0 left-0 right-0 h-12 pointer-events-none bg-gradient-to-t from-white to-transparent flex items-end justify-center pb-2">
			<span class="text-gray-400 text-lg animate-bounce">&#8964;</span>
		</div>
	{/if}
</aside>

<style>
	/* Use real transform/opacity values (not Tailwind translate-* CSS vars) so 200ms interpolates. */
	.drawer-backdrop {
		opacity: 1;
		transition: opacity 200ms ease-out;
	}
	.drawer-backdrop.is-closed {
		opacity: 0;
	}
	.drawer-panel {
		transform: translateX(0);
		transition: transform 200ms ease-out;
	}
	.drawer-panel.is-closed {
		transform: translateX(-100%);
	}
	@media (prefers-reduced-motion: reduce) {
		.drawer-backdrop,
		.drawer-panel {
			transition: none;
		}
	}
	/* Desktop: the drawer is a full-pane stacking layer. If `lg:hidden` is
	   missing from the built CSS, an open drawer (desktop default) steals
	   every mouse hit while Tab can still reach controls underneath. */
	@media (min-width: 1024px) {
		.mobile-nav-drawer {
			display: none !important;
			pointer-events: none !important;
		}
	}
</style>
