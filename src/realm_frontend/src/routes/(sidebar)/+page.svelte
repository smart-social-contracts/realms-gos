<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { isAuthenticated } from '$lib/stores/auth';
	import { hasJoined } from '$lib/stores/profiles';
	import { sidebarConfig } from '$lib/stores/sidebar';
	import { isEmbeddedInPortal } from '$lib/portal-bridge.ts';
	import { restoreAuthSession } from '$lib/auth';
	import { hrefWithPreservedTestIdentityParams } from '$lib/test-identity-query.ts';
	import { get } from 'svelte/store';

	onMount(async () => {
		if (isEmbeddedInPortal()) {
			await restoreAuthSession();
			// Profiles may still be loading after restore — wait briefly so we
			// don't bounce a real member to the public dashboard on a race.
			const { profilesLoading } = await import('$lib/stores/profiles');
			for (let i = 0; i < 40 && get(profilesLoading); i++) {
				await new Promise((r) => setTimeout(r, 50));
			}
		}
		const search = typeof window !== 'undefined' ? window.location.search : '';
		// The user may have opened another page while sign-in was restoring.
		if (get(page).url.pathname !== '/') return;
		if (get(isAuthenticated) && hasJoined()) {
			const config = get(sidebarConfig);
			goto(hrefWithPreservedTestIdentityParams(search, config?.defaultPath || '/extensions/public_dashboard'));
		} else {
			// Anonymous / not-yet-joined visitors see the public dashboard
			// (same on portal embeds and standalone). Join remains available
			// from the sidebar / CTA.
			goto(hrefWithPreservedTestIdentityParams(search, '/extensions/public_dashboard'));
		}
	});
</script>
