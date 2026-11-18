<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { get } from 'svelte/store';
	import MetaTag from '../../utils/MetaTag.svelte';
	import { _ } from 'svelte-i18n';
	import { isAuthenticated } from '$lib/stores/auth';
	import { hasJoined } from '$lib/stores/profiles';

	export let data;

	const path: string = '/dashboard';
	const description: string = 'Public Dashboard - redirecting to extension';
	const title: string = $_('extensions.public_dashboard.title');
	const subtitle: string = $_('extensions.public_dashboard.title');

	onMount(() => {
		if (get(isAuthenticated) && hasJoined()) {
			goto('/extensions/member_dashboard');
		} else {
			goto('/extensions/public_dashboard');
		}
	});
</script>

<MetaTag {path} {description} {title} {subtitle} />
