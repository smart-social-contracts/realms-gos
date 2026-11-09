<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { memberNickname } from '$lib/stores/delegation.js';
	import { sentForApproval } from '$lib/stores/approvalSent';

	// Tells a member who acted for another that the act was not done but sent to that member to approve.
	const sent = $derived($sentForApproval);
	const shortened = (principal: string) => `${principal.slice(0, 8)}…${principal.slice(-6)}`;

	let nickname = $state('');
	$effect(() => {
		nickname = '';
		if (sent?.grantor) memberNickname(sent.grantor).then((name: string) => (nickname = name));
	});
	const member = $derived(nickname || (sent?.grantor ? shortened(sent.grantor) : ''));
</script>

{#if sent}
	<div
		class="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900 flex flex-wrap items-center justify-between gap-2"
		role="status"
	>
		<span>
			{$_('delegation.sent.text', { values: { member } })}
			<a class="underline" href={sent.href}>{$_('delegation.sent.link')}</a>
		</span>
		<button
			type="button"
			class="rounded-md border border-amber-300 bg-white px-2 py-0.5 text-xs font-medium hover:bg-amber-100"
			onclick={() => sentForApproval.set(null)}
		>
			{$_('delegation.sent.dismiss')}
		</button>
	</div>
{/if}
