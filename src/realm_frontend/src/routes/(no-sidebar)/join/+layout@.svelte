<script>
	import '../../../app.pcss';
	import DemoDataBanner from '$lib/components/DemoDataBanner.svelte';
</script>

<!-- The join page resets to the root layout, so it carries the demo banner itself. The page fills what the banner
     leaves of the viewport. -->
<div class="flex w-full h-viewport flex-col overflow-hidden">
	<DemoDataBanner />
	<div class="min-h-0 flex-1 overflow-y-auto">
		<slot />
	</div>
</div>
