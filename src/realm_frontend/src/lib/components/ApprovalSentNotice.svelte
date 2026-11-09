<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { delegationsAsDelegate } from '$lib/stores/delegation.js';
	import { sentForApproval } from '$lib/stores/approvalSent';

	// Tells a member who acted for another that the act was not done but sent to that member to approve.
	const sent = $derived($sentForApproval);
	const member = $derived.by(() => {
		const grant = $delegationsAsDelegate.find((d: { grantor: string }) => d.grantor === sent?.grantor);
		return grant?.label || (sent?.grantor ? `${sent.grantor.slice(0, 8)}…${sent.grantor.slice(-6)}` : '');
	});
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
