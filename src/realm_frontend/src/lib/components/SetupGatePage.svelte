<script lang="ts">
	import { Button, P } from 'flowbite-svelte';
	import { _ } from 'svelte-i18n';
	import type { SetupGateVariant } from '$lib/setup/gateLogic';

	interface Props {
		variant: SetupGateVariant;
		realmName?: string;
	}

	let { variant, realmName = '' }: Props = $props();
	const displayName = $derived(realmName || $_('setup.gate.unnamed_realm'));

	const title = $derived(
		variant === 'anonymous'
			? $_('setup.gate.anonymous_title', { values: { name: displayName } })
			: $_('setup.gate.creator_title', { values: { name: displayName } })
	);

	const description = $derived(
		variant === 'anonymous'
			? $_('setup.gate.anonymous_body')
			: $_('setup.gate.creator_body')
	);

	let showLogo = $state(true);
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>

<main class="setup-gate">
	<div class="setup-gate__card">
		{#if showLogo}
			<img
				src="/custom/logo.png"
				alt=""
				class="setup-gate__logo"
				onerror={() => {
					showLogo = false;
				}}
			/>
		{/if}
		<h1>{title}</h1>
		<P class="setup-gate__text">{description}</P>
		{#if variant === 'anonymous'}
			<Button
				href="/join"
				color="none"
				class="setup-gate__button bg-primary-600 text-white hover:bg-primary-700"
			>
				{$_('setup.gate.log_in')}
			</Button>
		{/if}
	</div>
</main>

<style>
	.setup-gate {
		display: flex;
		min-height: 100vh;
		min-height: 100dvh;
		align-items: center;
		justify-content: center;
		background: #f8fafc;
		padding: 1.5rem;
	}

	.setup-gate__card {
		max-width: 32rem;
		width: 100%;
		background: white;
		border: 1px solid #e5e7eb;
		border-radius: 1rem;
		padding: 2rem;
		text-align: center;
		box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
	}

	.setup-gate__logo {
		width: 3.5rem;
		height: 3.5rem;
		margin: 0 auto 1rem;
		opacity: 0.9;
	}

	h1 {
		font-size: 1.5rem;
		font-weight: 600;
		color: #0f172a;
		margin-bottom: 0.75rem;
	}

	:global(.setup-gate__text) {
		color: #64748b;
		margin-bottom: 1.5rem;
	}

	:global(.setup-gate__button) {
		margin: 0 auto;
	}
</style>
