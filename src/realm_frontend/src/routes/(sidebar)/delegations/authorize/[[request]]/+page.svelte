<script lang="ts">
	import MetaTag from '../../../../utils/MetaTag.svelte';
	import { _ } from 'svelte-i18n';
	import { page } from '$app/stores';
	import ConsentShell from '$lib/components/ConsentShell.svelte';
	import AuthorizeRequest from '$lib/components/delegation/AuthorizeRequest.svelte';

	const path = '/delegations/authorize';
	const requestId = $derived($page.params.request || $page.url.searchParams.get('request') || '');
</script>

<MetaTag {path} title={$_('delegation.authorize.title')} description={$_('delegation.authorize.description')} />

<ConsentShell
	id={requestId}
	signInTitle={$_('delegation.authorize.sign_in_title')}
	signInText={$_('delegation.authorize.sign_in_text')}
	missingText={$_('delegation.authorize.missing')}
>
	<AuthorizeRequest {requestId} />
</ConsentShell>
