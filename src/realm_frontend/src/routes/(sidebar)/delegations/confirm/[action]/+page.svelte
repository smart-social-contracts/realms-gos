<script lang="ts">
	import MetaTag from '../../../../utils/MetaTag.svelte';
	import { _ } from 'svelte-i18n';
	import { page } from '$app/stores';
	import ConsentShell from '$lib/components/ConsentShell.svelte';
	import ConfirmAction from '$lib/components/delegation/ConfirmAction.svelte';

	const path = '/delegations/confirm';
	const id = $derived($page.params.action ?? '');
</script>

<MetaTag {path} title={$_('delegation.confirm.title')} description={$_('delegation.confirm.description')} />

<ConsentShell
	{id}
	signInTitle={$_('delegation.confirm.sign_in_title')}
	signInText={$_('delegation.confirm.sign_in_text')}
	missingText={$_('delegation.confirm.missing')}
>
	<ConfirmAction {id} />
</ConsentShell>
