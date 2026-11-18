<script lang="ts">
	import { MetaTags } from 'svelte-meta-tags';
	import { SITE_NAME } from '$lib/globals';
	import { realmName } from '$lib/stores/realmInfo';
	import { pageTitle } from '$lib/utils/page-title';
	export let path: string = '';
	export let description: string = '';
	export let title: string = '';
	export let subtitle: string = '';

	let imgsrc = `https://open-graph-vercel.vercel.app/api/flowbite-svelte-admin-dashboard?title=${subtitle}`;

	let og_url: string = `https://flowbite-svelte-admin-dashboard.vercel.app${path}`;
</script>

<MetaTags
	title={pageTitle(title, $realmName)}
	{description}
	openGraph={{
		type: 'website',
		url: `${og_url}`,
		title: `${title}`,
		description: `${description}`,
		images: [
			{
				url: imgsrc,
				width: 1200,
				height: 630,
				alt: `${title}`
			}
		],
		siteName: SITE_NAME + ''
	}}
	twitter={{
		handle: '@shinokada',
		cardType: 'summary_large_image',
		title: `${title}`,
		description: `${description}`,
		image: imgsrc,
		imageAlt: `${title}`
	}}
/>
