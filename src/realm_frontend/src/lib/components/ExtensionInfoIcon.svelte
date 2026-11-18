<script lang="ts">
	import { onMount } from 'svelte';
	import { fade } from 'svelte/transition';
	import { IconInfoCircle, IconBrandGithub, IconExternalLink } from '@tabler/icons-svelte';
	import {
		getExtensionManifest,
		resolveExtensionRepoUrl,
		type ExtensionManifestInfo,
	} from '$lib/utils/extension-manifest';
	import T from '$lib/components/T.svelte';

	export let extensionId: string;

	let open = false;
	let loading = false;
	let manifest: ExtensionManifestInfo | null = null;
	let repoUrl: string | null = null;

	async function loadManifest() {
		if (manifest || loading) return;
		loading = true;
		try {
			manifest = await getExtensionManifest(extensionId);
			repoUrl = resolveExtensionRepoUrl(manifest, extensionId);
		} finally {
			loading = false;
		}
	}

	function toggle(event: MouseEvent) {
		event.stopPropagation();
		open = !open;
		if (open) {
			void loadManifest();
		}
	}

	function close() {
		open = false;
	}

	onMount(() => {
		const handleClickOutside = (event: MouseEvent) => {
			if (open && !(event.target as Element)?.closest('.extension-info-popover')) {
				close();
			}
		};
		const handleEscape = (event: KeyboardEvent) => {
			if (open && event.key === 'Escape') {
				close();
			}
		};
		document.addEventListener('click', handleClickOutside);
		document.addEventListener('keydown', handleEscape);
		return () => {
			document.removeEventListener('click', handleClickOutside);
			document.removeEventListener('keydown', handleEscape);
		};
	});
</script>

<div class="extension-info-popover relative inline-flex items-center">
	<button
		type="button"
		class="inline-flex items-center justify-center rounded-full p-0.5 text-gray-500 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
		aria-label="Extension information"
		aria-expanded={open}
		on:click={toggle}
	>
		<IconInfoCircle size={16} stroke={1.75} />
	</button>

	{#if open}
		<div
			class="absolute left-0 top-full z-50 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-lg border border-gray-200 bg-white p-4 shadow-lg"
			role="dialog"
			aria-label="Extension information"
			transition:fade={{ duration: 120 }}
		>
			{#if loading}
				<p class="text-sm text-gray-500">
					<T key="extension_info.loading" fallback="Loading extension info..." />
				</p>
			{:else if !manifest}
				<p class="text-sm text-gray-500">
					<T key="extension_info.unavailable" fallback="Extension metadata unavailable." />
				</p>
			{:else}
				<div class="space-y-3 text-sm">
					<div>
						<h3 class="font-semibold text-gray-900">{manifest.name || extensionId}</h3>
						{#if manifest.version}
							<p class="mt-1 text-xs text-gray-500">
								<T key="extension_info.version" fallback="Version" />
								<span class="ml-1 font-medium text-gray-900">{manifest.version}</span>
							</p>
						{/if}
					</div>

					{#if manifest.description}
						<p class="text-gray-600">{manifest.description}</p>
					{/if}

					<dl class="space-y-1.5 text-xs text-gray-500">
						{#if manifest.author}
							<div class="flex gap-2">
								<dt class="shrink-0 font-medium text-gray-700">
									<T key="extension_info.author" fallback="Author" />
								</dt>
								<dd>{manifest.author}</dd>
							</div>
						{/if}
						{#if manifest.categories?.length}
							<div class="flex gap-2">
								<dt class="shrink-0 font-medium text-gray-700">
									<T key="extension_info.categories" fallback="Categories" />
								</dt>
								<dd>{manifest.categories.join(', ')}</dd>
							</div>
						{/if}
						{#if manifest.profiles?.length}
							<div class="flex gap-2">
								<dt class="shrink-0 font-medium text-gray-700">
									<T key="extension_info.profiles" fallback="Profiles" />
								</dt>
								<dd>{manifest.profiles.join(', ')}</dd>
							</div>
						{/if}
					</dl>

					{#if repoUrl}
						<a
							href={repoUrl}
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-1.5 text-sm font-medium text-primary-700 hover:text-primary-800"
						>
							<IconBrandGithub size={16} stroke={1.75} />
							<T key="extension_info.view_on_github" fallback="View on GitHub" />
							<IconExternalLink size={14} stroke={1.75} />
						</a>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>
