<script lang="ts">
	import { Frame, type LinkType } from 'flowbite-svelte';
	import { GithubSolid } from 'flowbite-svelte-icons';
	import { _ } from 'svelte-i18n';
	import { testMode } from '$lib/stores/realmInfo';
	import TestFlagsModal from '$lib/components/TestFlagsModal.svelte';

	let testFlagsOpen = $state(false);
	
	// Get commit hash from meta tag
	let commitHash = $state('');
	let fullCommitHash = $state('');
	// Get commit datetime from meta tag
	let commitDatetime = $state('');
	// Get version from meta tag
	let version = $state('');
	
	// This runs on the client side only
	if (typeof document !== 'undefined') {
		let hash = '';
		let fullHash = '';
		let datetime = '';
		let ver = '';

		const commitHashMeta = document.querySelector('meta[name="commit-hash"]');
		if (commitHashMeta) {
			fullHash = commitHashMeta.getAttribute('content') || '';
			hash = fullHash;
			// Format to show only first 7 characters if it's a full hash
			if (hash && hash !== 'COMMIT_HASH_PLACEHOLDER' && hash.length > 7) {
				hash = hash.substring(0, 7);
			}
		}
		
		const commitDatetimeMeta = document.querySelector('meta[name="commit-datetime"]');
		if (commitDatetimeMeta) {
			datetime = commitDatetimeMeta.getAttribute('content') || '';
		}
		
		const versionMeta = document.querySelector('meta[name="version"]');
		if (versionMeta) {
			ver = versionMeta.getAttribute('content') || '';
		}
		
		// Use build-time values as fallback for local development
		// These are injected by Vite at build time via define config
		if (!ver || ver === 'VERSION_PLACEHOLDER') {
			// @ts-ignore - Vite injects this at build time
			ver = typeof __BUILD_VERSION__ !== 'undefined' ? __BUILD_VERSION__ : 'dev';
		}
		if (!hash || hash === 'COMMIT_HASH_PLACEHOLDER') {
			// @ts-ignore - Vite injects this at build time
			fullHash = typeof __BUILD_COMMIT__ !== 'undefined' ? __BUILD_COMMIT__ : 'local';
			hash = fullHash;
			if (hash.length > 7) {
				hash = hash.substring(0, 7);
			}
		}
		if (!datetime || datetime === 'COMMIT_DATETIME_PLACEHOLDER') {
			// @ts-ignore - Vite injects this at build time
			datetime = typeof __BUILD_TIME__ !== 'undefined' ? __BUILD_TIME__ : new Date().toISOString().replace('T', ' ').substring(0, 19);
		}

		fullCommitHash = fullHash;
		commitHash = hash;
		commitDatetime = datetime;
		version = ver;
	}

	const links: LinkType[] = [
		{ name: 'Terms and conditions', href: '#' },
		{ name: 'Privacy Policy', href: '#' },
		{ name: 'Licensing', href: '#' },
		{ name: 'Cookie Policy', href: '#' },
		{ name: 'Contact', href: '#' }
	];
</script>

<Frame
	tag="footer"
	rounded
	shadow
	class="mx-auto mt-6 w-full max-w-screen-xl rounded-lg bg-white p-6 shadow dark:bg-gray-800 pl-2"
>
	<!-- <ul class="flex flex-wrap items-center justify-center space-x-4 space-y-1 md:space-x-6 xl:space-x-8">
		{#each links as { name, href }}
			<li>
				<a
					{href}
					class="text-sm font-normal text-gray-500 hover:underline dark:text-gray-400"
				>
					{name}
				</a>
			</li>
		{/each}
	</ul> -->
	
	<!-- App name, version and commit hash display -->
	<div class="mt-3 flex items-center justify-center gap-1 text-xs font-normal text-gray-400 dark:text-gray-500">
		<span>Realms GOS {version}</span>
		<a
			href="https://github.com/smart-social-contracts/realms-gos"
			target="_blank"
			rel="noopener noreferrer"
			aria-label="Realms on GitHub"
			class="inline-flex items-center text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400"
		>
			<GithubSolid class="block h-3 w-3" size="xs" />
		</a>
		<a
			href="https://github.com/smart-social-contracts/realms-gos/commit/{fullCommitHash}"
			target="_blank"
			rel="noopener noreferrer"
			class="text-gray-400 hover:underline dark:text-gray-500"
		>
			{commitHash}
		</a>
		<span>{commitDatetime}{typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname.endsWith('.localhost')) ? ' - Local deployment' : ''}</span>
	</div>

	{#if $testMode}
		<div class="mt-2 text-center">
			<button
				type="button"
				class="rounded border border-amber-400 px-2 py-0.5 text-xs font-medium text-amber-600 hover:bg-amber-50 dark:border-amber-500 dark:text-amber-400 dark:hover:bg-gray-700"
				onclick={() => (testFlagsOpen = true)}
			>
				{$_('footer.test_flags')}
			</button>
		</div>
		<TestFlagsModal bind:open={testFlagsOpen} />
	{/if}
	
	<!-- Built on Internet Computer section -->
	<div class="mt-3 flex justify-center">
		<a href="https://internetcomputer.org" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-xs font-normal text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400">
			<img src="/images/internet-computer-icp-logo.svg" alt="Internet Computer Logo" width="12" height="12" class="block h-3 w-3 grayscale" />
			<span>{$_('footer.built_on_ic')}</span>
		</a>
	</div>
</Frame>
