<script>
	import modeobserver from './utils/modeobserver';
	import { onMount } from 'svelte';
	import { afterNavigate, goto } from '$app/navigation';
	import { browser } from '$app/environment';
	import '$lib/i18n';
	import { locale } from 'svelte-i18n';
	import '../app.pcss';
	import { initializeTheme } from '$lib/theme/init';
	import { restoreAuthSession, resetAuthSessionRestore, getPortalRedirectUrl } from '$lib/auth';
	import { isEmbeddedInPortal, portalNavPush, getPortalHostEmbeddedPath } from '$lib/portal-bridge.ts';
	import { portalSharePathFromUrl, resolvePortalNavSyncHref, shouldPortalEnterPush } from '$lib/portal-redirect-path.ts';
	import { dismissAppSplash } from '$lib/app-splash';
	import { applyFavicon } from '$lib/branding/favicon';
	import { realmInfo } from '$lib/stores/realmInfo';
	import SetupStageGate from '$lib/components/SetupStageGate.svelte';
	import BridgeModalHost from '$lib/components/BridgeModalHost.svelte';

	export const SITE_NAME = "Realms GOS";

	// The tab's icon is the realm's mark, or the default one when the realm has none.
	$: if (browser) applyFavicon($realmInfo.logoUrl);

	// Drop the globe as soon as this module evaluates. If a child throw
	// aborts hydration, onMount never runs — leaving splash up forever.
	if (browser) {
		dismissAppSplash();
	}

	// Debug locale changes
	if (browser) {
		locale.subscribe(value => {
			console.log('Layout: locale changed to', value);
			// Update HTML lang attribute directly
			document.documentElement.lang = value || 'en';
		});
	}

	function pushPortalShare(url, { replace }) {
		portalNavPush(portalSharePathFromUrl(url), { replace });
	}

	function syncPortalIfHostStale(url) {
		if (!browser || !isEmbeddedInPortal() || !url) return;
		if (!shouldPortalEnterPush(url.pathname, getPortalHostEmbeddedPath())) return;
		pushPortalShare(url, { replace: true });
	}

	// Mirror every in-realm navigation onto the portal address bar so shared
	// links and hard-refresh keep the current extension/path
	// (`/r/<slug>/extensions/justice_litigation`, …).
	afterNavigate((navigation) => {
		if (!browser || !isEmbeddedInPortal()) return;
		const url = navigation.to?.url;
		if (!url) return;
		// Initial enter: the host already has the user-facing URL (including
		// `?ti=` / `skip_ii` / `test_mode`). Pushing when paths already match
		// used to replace `/join?ti=1` with `/join` when the embed src omitted
		// them. A *full iframe reload* (sidebar click before hydration) leaves
		// the host bar stale — e.g. /identities while Messages is on screen.
		if (navigation.type === 'enter') {
			syncPortalIfHostStale(url);
			return;
		}
		pushPortalShare(url, { replace: navigation.type !== 'link' });
	});

	onMount(async () => {
		dismissAppSplash();

		// Standalone visit to the raw canister origin → bounce to the federation
		// portal (https://…/r/<slug>/<same-path>), where the single II login is
		// bridged into the embedded realm. Bookmarks and shared raw-URL links
		// transparently become portal links. ?standalone=1 opts out (tests/ops);
		// test-mode II bypass keeps working because the bypass URL carries it.
		if (browser) {
			const portalRedirect = getPortalRedirectUrl();
			if (portalRedirect) {
				console.log(`[portal] standalone visit — redirecting to ${portalRedirect}`);
				window.location.replace(portalRedirect);
				return;
			}
		}

		modeobserver();
		initializeTheme();

		let bridgeDispose = () => {};
		if (browser) {
			const { initPortalBridge } = await import('$lib/portal-bridge.ts');
			bridgeDispose = initPortalBridge();
			const onPortalAuth = async () => {
				// A fresh delegation just arrived; the memoized restore may have
				// resolved as "not authenticated" before it, so force a re-check.
				resetAuthSessionRestore();
				await restoreAuthSession();
			};
			// Host asked us to navigate (e.g. after a hard-load deep link sync).
			const onPortalNavSync = (event) => {
				const path = event?.detail?.path;
				if (!path || typeof path !== 'string') return;
				const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
				const next = resolvePortalNavSyncHref(current, path);
				if (!next) return;
				void goto(next, { replaceState: true, noScroll: true });
			};
			const onPortalConfig = () => {
				syncPortalIfHostStale(window.location);
			};
			window.addEventListener('portal:auth', onPortalAuth);
			window.addEventListener('portal:nav-sync', onPortalNavSync);
			window.addEventListener('portal:config', onPortalConfig);
			void restoreAuthSession();
			const prevDispose = bridgeDispose;
			bridgeDispose = () => {
				prevDispose?.();
				window.removeEventListener('portal:auth', onPortalAuth);
				window.removeEventListener('portal:nav-sync', onPortalNavSync);
				window.removeEventListener('portal:config', onPortalConfig);
			};
		}

		return () => {
			bridgeDispose?.();
		};
	});
</script>

<div class="app">
	<SetupStageGate>
		<slot />
	</SetupStageGate>
	<BridgeModalHost />
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
		min-height: 100dvh;
	}
</style>
