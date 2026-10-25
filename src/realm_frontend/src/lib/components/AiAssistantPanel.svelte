<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { browser } from '$app/environment';
	// @ts-ignore
	import { backend } from '$lib/canisters';
	import { canisterId as backendCanisterId } from '$lib/declarations/realm_backend';
	import { principal, isAuthenticated } from '$lib/stores/auth';
	import { userProfiles } from '$lib/stores/profiles';
	import { realmInfo } from '$lib/stores/realmInfo';
	import { notifications, unreadCount, loadNotifications, markAsRead } from '$lib/stores/notifications';
	// @ts-ignore
	import { _, locale } from 'svelte-i18n';
	import { get, writable } from 'svelte/store';
	import { createHostContext } from '$lib/host-bridge';
	// @ts-ignore
	import { CONFIG } from '$lib/config.js';
	import { cn } from '$lib/theme/utilities';
	import { mountExtension, type MountResult } from '$lib/extension-loader';
	import { getInstalledExtensionVersion } from '$lib/utils/extension-manifest';
	import { loadExtensionTranslation } from '$lib/i18n';
	import { getInfraConfig } from '$lib/utils/infraConfig';
	import { IconSettings, IconX } from '@tabler/icons-svelte';

	export let open = false;
	export let panelWidth = 320;
	export let onClose: () => void = () => {};

	const EXTENSION_ID = 'llm_chat';
	const AI_PANEL_WIDTH_KEY = 'realm_ai_panel_width';
	const DEFAULT_PANEL_WIDTH = 320;
	const MIN_PANEL_WIDTH = 280;
	const MAX_PANEL_WIDTH_RATIO = 0.5;
	const SETTINGS_PATH = '/extensions/llm_chat';
	const modelLabel = CONFIG?.llmModelLabel ?? 'Default';

	function runtimeCanisterIds(): { realm_backend?: string; network?: string } {
		return (globalThis as {
			__CANISTER_IDS?: { realm_backend?: string; network?: string };
		}).__CANISTER_IDS ?? {};
	}

	// The realm declares its own network (canister_ids.js / status()); there is
	// no id→network table here to go stale when an environment is rebuilt.
	function resolveGeisterNetwork(): string {
		const ids = runtimeCanisterIds();
		if (ids.network && ids.network !== 'ic') return ids.network;
		if (browser && window.location.hostname.includes('icp0.io')) return 'test';
		return 'staging';
	}

	function getRealmBackendCanisterId(): string {
		return runtimeCanisterIds().realm_backend ?? backendCanisterId?.toString?.() ?? '';
	}

	function openSettings() {
		goto(SETTINGS_PATH);
	}

	let mountPoint: HTMLDivElement | undefined;
	let status: 'idle' | 'loading' | 'ready' | 'error' = 'idle';
	let errorMsg = '';
	let mounted: MountResult | void;
	let infraConfig: { fileRegistryCanisterId?: string; marketplaceCanisterId?: string } = {};
	let isResizing = false;
	let mobilePanelHeight = '';
	let panelElement: HTMLDivElement | undefined;

	function getMaxPanelWidth(): number {
		if (!browser) return 600;
		return Math.max(MIN_PANEL_WIDTH, Math.floor(window.innerWidth * MAX_PANEL_WIDTH_RATIO));
	}

	function clampPanelWidth(width: number): number {
		return Math.min(Math.max(width, MIN_PANEL_WIDTH), getMaxPanelWidth());
	}

	function savePanelWidth() {
		if (browser) {
			localStorage.setItem(AI_PANEL_WIDTH_KEY, String(panelWidth));
		}
	}

	function startResize(event: PointerEvent) {
		if (!browser || window.innerWidth < 1024) return;
		event.preventDefault();
		event.stopPropagation();
		isResizing = true;
		document.body.style.userSelect = 'none';
		document.body.style.cursor = 'col-resize';

		const handle = event.currentTarget as HTMLElement;
		handle.setPointerCapture(event.pointerId);

		const onMove = (e: PointerEvent) => {
			panelWidth = clampPanelWidth(window.innerWidth - e.clientX);
		};

		const onUp = (e: PointerEvent) => {
			isResizing = false;
			document.body.style.userSelect = '';
			document.body.style.cursor = '';
			savePanelWidth();
			handle.releasePointerCapture(e.pointerId);
			handle.removeEventListener('pointermove', onMove);
			handle.removeEventListener('pointerup', onUp);
			handle.removeEventListener('pointercancel', onUp);
		};

		handle.addEventListener('pointermove', onMove);
		handle.addEventListener('pointerup', onUp);
		handle.addEventListener('pointercancel', onUp);
	}

	function resetPanelWidth() {
		panelWidth = DEFAULT_PANEL_WIDTH;
		savePanelWidth();
	}

	type PageContext = { pathname: string; extensionId: string; title: string };

	// Reactive store so the mounted assistant stays aware of what the user is viewing
	// as they navigate, without needing to be remounted.
	const pageContext = writable<PageContext>(computePageContext($page.url.pathname));
	$: pageContext.set(computePageContext($page.url.pathname));
	$: activeExtensionId = extractActiveExtension($page.url.pathname);

	function extractActiveExtension(pathname: string): string {
		const match = pathname.match(/\/extensions\/([^/]+)/);
		return match?.[1] ?? '';
	}

	function prettifySegment(segment: string): string {
		if (!segment) return '';
		return segment
			.replace(/[-_]/g, ' ')
			.replace(/\b\w/g, (c) => c.toUpperCase());
	}

	function computePageContext(pathname: string): PageContext {
		const extensionId = extractActiveExtension(pathname);
		let title = '';
		if (extensionId) {
			title = prettifySegment(extensionId);
		} else {
			const segments = pathname.split('/').filter(Boolean);
			title = segments.length ? prettifySegment(segments[segments.length - 1]) : 'Home';
		}
		return { pathname, extensionId, title };
	}

	async function resolveInfraConfig() {
		infraConfig = await getInfraConfig();
	}

	function buildContext(id: string, version: string): any {
		async function callSync(fn: string, args: Record<string, unknown> = {}): Promise<unknown> {
			const raw = await backend.extension_sync_call(id, fn, JSON.stringify(args));
			const res = typeof raw === 'string' ? JSON.parse(raw) : raw;
			if (res?.success === false) throw new Error(res.response ?? 'extension_sync_call failed');
			if (!res?.response) return res;
			try { return JSON.parse(res.response); } catch { return res.response; }
		}
		async function callAsync(fn: string, args: Record<string, unknown> = {}): Promise<unknown> {
			const raw = await backend.extension_async_call(id, fn, JSON.stringify(args));
			const res = typeof raw === 'string' ? JSON.parse(raw) : raw;
			if (res?.success === false) throw new Error(res.response ?? 'extension_async_call failed');
			if (!res?.response) return res;
			try { return JSON.parse(res.response); } catch { return res.response; }
		}

		return {
			...createHostContext(),
			extensionId: id,
			version,
			backend,
			callSync,
			callAsync,
			principal,
			isAuthenticated,
			userProfiles,
			realmInfo,
			config: {
				...CONFIG,
				canisterId: getRealmBackendCanisterId(),
				network: resolveGeisterNetwork(),
				aiAssistantEnabled: get(realmInfo).aiAssistantEnabled !== false,
				...infraConfig,
			},
			navigate: goto,
			t: _,
			locale,
			notifications: {
				items: notifications,
				unreadCount,
				load: loadNotifications,
				markAsRead,
			},
			theme: { cn },
			pageContext,
			context: {
				activeExtensionId,
			},
			sidebarPanel: true,
			settingsPath: SETTINGS_PATH,
			hideModelBar: true,
		};
	}

	function cleanupMounted() {
		if (mounted && typeof (mounted as MountResult)?.unmount === 'function') {
			try { (mounted as MountResult).unmount!(); } catch (e) { /* ignore */ }
		}
		mounted = undefined;
		if (mountPoint) mountPoint.innerHTML = '';
	}

	async function loadAssistant() {
		if (!browser || !mountPoint) return;
		cleanupMounted();
		status = 'loading';
		errorMsg = '';

		try {
			const [version] = await Promise.all([
				getInstalledExtensionVersion(EXTENSION_ID),
				resolveInfraConfig(),
			]);
			if (!version) {
				status = 'error';
				errorMsg = get(_)('errors.ai_not_installed');
				return;
			}
			await loadExtensionTranslation(EXTENSION_ID, version, get(locale as any) || 'en');
			const ctx = buildContext(EXTENSION_ID, version);
			mounted = await mountExtension(EXTENSION_ID, version, mountPoint, ctx);
			status = 'ready';
		} catch (e: any) {
			console.error('[AI Panel] Load failed:', e);
			status = 'error';
			errorMsg = String(e?.message ?? e);
		}
	}

	$: if (open && browser && mountPoint && status === 'idle') {
		loadAssistant();
	}

	onMount(() => {
		if (!browser) return;
		const saved = localStorage.getItem(AI_PANEL_WIDTH_KEY);
		if (saved !== null) {
			const parsed = parseInt(saved, 10);
			if (!Number.isNaN(parsed)) {
				panelWidth = clampPanelWidth(parsed);
			}
		}

		const updateMobilePanelHeight = () => {
			if (window.innerWidth >= 1024) {
				mobilePanelHeight = '';
				return;
			}
			const vv = window.visualViewport;
			if (!vv || !panelElement) return;
			const top = panelElement.getBoundingClientRect().top;
			mobilePanelHeight = `${Math.max(Math.round(vv.height + vv.offsetTop - top), 200)}px`;
		};

		const handleWindowResize = () => {
			panelWidth = clampPanelWidth(panelWidth);
			updateMobilePanelHeight();
		};

		window.addEventListener('resize', handleWindowResize);

		const vv = window.visualViewport;
		updateMobilePanelHeight();
		if (vv) {
			vv.addEventListener('resize', updateMobilePanelHeight);
			vv.addEventListener('scroll', updateMobilePanelHeight);
		}

		return () => {
			window.removeEventListener('resize', handleWindowResize);
			if (vv) {
				vv.removeEventListener('resize', updateMobilePanelHeight);
				vv.removeEventListener('scroll', updateMobilePanelHeight);
			}
		};
	});

	onDestroy(() => {
		cleanupMounted();
	});
</script>

<div
	bind:this={panelElement}
	class="ai-assistant-panel fixed top-16 right-0 z-30 border-l border-gray-200 bg-white transition-transform duration-300 ease-in-out flex flex-col {open ? 'translate-x-0' : 'translate-x-full'} {isResizing ? 'is-resizing' : ''} {mobilePanelHeight ? '' : 'h-[calc(100dvh-4rem)]'}"
	style="--panel-width: {panelWidth}px;{mobilePanelHeight ? ` height: ${mobilePanelHeight};` : ''}"
>
	<!-- Drag handle (left edge) — must sit above panel content to receive pointer events -->
	<div
		class="resize-handle"
		on:pointerdown|stopPropagation={startResize}
		on:dblclick|stopPropagation={resetPanelWidth}
		role="separator"
		aria-orientation="vertical"
		aria-label={$_('chrome.ai_resize')}
		title={$_('chrome.ai_resize_hint')}
	></div>
	<!-- Panel header -->
	<div class="panel-header flex items-center gap-2 px-4 py-3 border-b border-gray-200">
		<h3 class="text-sm font-semibold text-gray-700 flex-1 min-w-0 truncate">{$_('common.assistant')}</h3>
		<span class="text-[11px] text-gray-400 font-medium shrink-0">{modelLabel}</span>
		<button
			on:click={openSettings}
			class="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors shrink-0"
			title={$_('chrome.ai_open_settings')}
			aria-label={$_('chrome.ai_open_settings')}
		>
			<IconSettings size={16} />
		</button>
		<button
			on:click={onClose}
			class="p-1 rounded hover:bg-gray-100 text-gray-500 hover:text-gray-700 transition-colors shrink-0"
			aria-label={$_('chrome.ai_close')}
		>
			<IconX size={18} />
		</button>
	</div>

	<!-- Extension mount point -->
	<div class="panel-body flex-1 min-h-0 overflow-hidden flex flex-col">
		{#if status === 'loading'}
			<div class="flex items-center justify-center h-32 text-gray-500">
				<div class="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-700"></div>
				<span class="ml-2 text-sm">Loading...</span>
			</div>
		{:else if status === 'error'}
			<div class="p-4 text-sm text-red-600">{errorMsg}</div>
		{/if}
		<div bind:this={mountPoint} class="flex-1 min-h-0 h-full flex flex-col"></div>
	</div>
</div>

<style>
	.ai-assistant-panel {
		position: fixed;
		width: 100%;
	}

	@media (min-width: 1024px) {
		.ai-assistant-panel {
			width: var(--panel-width);
		}
	}

	.resize-handle {
		position: absolute;
		top: 0;
		left: 0;
		width: 12px;
		height: 100%;
		cursor: col-resize;
		z-index: 50;
		touch-action: none;
	}

	@media (max-width: 1023px) {
		.resize-handle {
			display: none;
		}

		.ai-assistant-panel {
			border-left: none;
		}
	}

	.resize-handle::after {
		content: '';
		position: absolute;
		top: 0;
		bottom: 0;
		left: 0;
		width: 3px;
		background: transparent;
		transition: background 0.15s ease;
	}

	.resize-handle:hover::after,
	.is-resizing .resize-handle::after {
		background: #6366f1;
	}

	.panel-header,
	.panel-body {
		position: relative;
		z-index: 1;
	}

	.is-resizing {
		transition: none;
	}
</style>
