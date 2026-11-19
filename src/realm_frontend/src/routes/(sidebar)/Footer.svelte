<script lang="ts">
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
</script>

<!-- One quiet line: a page's footer is not content. -->
<footer class="mx-auto mt-10 w-full max-w-screen-xl px-4 pb-6">
	<div class="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-xs font-normal text-gray-500 dark:text-gray-400">
		<span>Realms GOS {version}</span>
		<a
			href="https://github.com/smart-social-contracts/realms-gos"
			target="_blank"
			rel="noopener noreferrer"
			aria-label="Realms on GitHub"
			class="inline-flex items-center hover:text-gray-700 dark:hover:text-gray-300"
		>
			<GithubSolid class="block h-3 w-3" size="xs" />
		</a>
		<a
			href="https://github.com/smart-social-contracts/realms-gos/commit/{fullCommitHash}"
			target="_blank"
			rel="noopener noreferrer"
			class="hover:underline"
		>
			{commitHash}
		</a>
		<span>{commitDatetime}{typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname.endsWith('.localhost')) ? ' - Local deployment' : ''}</span>
		<span aria-hidden="true">·</span>
		<a href="https://internetcomputer.org" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 hover:text-gray-700 dark:hover:text-gray-300">
			<img src="/images/internet-computer-icp-logo.svg" alt="" width="12" height="12" class="block h-3 w-3 grayscale" />
			<span>{$_('footer.built_on_ic')}</span>
		</a>
		{#if $testMode}
			<span aria-hidden="true">·</span>
			<button
				type="button"
				class="rounded border border-amber-500 px-1.5 text-xs font-medium text-amber-700 hover:bg-amber-50 dark:border-amber-500 dark:text-amber-400 dark:hover:bg-gray-700"
				onclick={() => (testFlagsOpen = true)}
			>
				{$_('footer.test_flags')}
			</button>
		{/if}
	</div>
	{#if $testMode}
		<TestFlagsModal bind:open={testFlagsOpen} />
	{/if}
</footer>
