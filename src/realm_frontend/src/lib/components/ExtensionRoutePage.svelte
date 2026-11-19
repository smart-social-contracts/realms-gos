<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { browser } from '$app/environment';
	import ExtensionRuntimeHost from '$lib/components/ExtensionRuntimeHost.svelte';
	import { sidebarConfig } from '$lib/stores/sidebar';
	import { isMemberInboxExtension, MEMBER_INBOX_HREF } from '$lib/utils/messages';
	import { resolveExtensionLabel } from '$lib/utils/breadcrumb';
	import { pageTitle } from '$lib/utils/page-title';
	import { realmName } from '$lib/stores/realmInfo';

	const id = $derived($page.params.id);
	const subpath = $derived($page.params.subpath ?? '');

	// Codex extension overrides (issue #242): navigating to a base system
	// extension (e.g. /extensions/member_dashboard) redirects to its
	// codex-specific replacement when one is installed.
	// The notifications extension is the same inbox as ME → Messages.
	$effect(() => {
		if (!browser || !id) return;
		if (isMemberInboxExtension(id)) {
			goto(MEMBER_INBOX_HREF, { replaceState: true });
			return;
		}
		const override = $sidebarConfig?.extensionOverrides?.[id];
		if (override && override !== id) {
			goto(`/extensions/${override}${subpath ? `/${subpath}` : ''}`, { replaceState: true });
		}
	});
</script>

<svelte:head>
	<title>{pageTitle(id ? resolveExtensionLabel(id, $sidebarConfig) : '', $realmName)}</title>
</svelte:head>

{#if id && !isMemberInboxExtension(id)}
	{#key id}
		<ExtensionRuntimeHost extensionId={id} />
	{/key}
{/if}
