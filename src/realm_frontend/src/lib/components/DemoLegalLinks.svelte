<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { DEMO_LEGAL_LINKS } from '$lib/config/demoLegal';
</script>

<!-- A new tab: an embedded realm would otherwise open the page inside the portal's frame. -->
<span class="demo-legal-links">
	{#each DEMO_LEGAL_LINKS as link, i (link.key)}
		{#if i > 0}<span aria-hidden="true">{' · '}</span>{/if}
		<a class="underline" href={link.href} target="_blank" rel="noopener noreferrer">{$_(link.key)}</a>
	{/each}
</span>
