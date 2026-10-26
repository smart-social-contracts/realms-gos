<script lang="ts">
	import { CUSTOM_TOKEN_ID, type SharedTokenOption } from '$lib/setup/sharedTokens';
	import {
		isTokenChoiceSelectable,
		monetaryUnavailableLabel
	} from '$lib/config/hostTestFlags';

	interface Props {
		/** The realm's token catalog (`token_catalog`). */
		options: SharedTokenOption[];
		selectedId: string;
		monetaryDisabled?: boolean;
		locale?: string;
		name?: string;
		onSelect: (id: string) => void;
	}

	let {
		options,
		selectedId,
		monetaryDisabled = false,
		locale = 'en',
		name = 'token',
		onSelect
	}: Props = $props();

	const unavailable = $derived(monetaryUnavailableLabel(locale));

	function selectable(id: string): boolean {
		const token = options.find((t) => t.id === id);
		return isTokenChoiceSelectable(token?.monetary ?? false, monetaryDisabled);
	}

	function choose(id: string) {
		if (!selectable(id)) return;
		onSelect(id);
	}
</script>

<div class="setup-wizard__codex-list">
	{#each options as token (token.id)}
		<label
			class="setup-wizard__codex-card setup-wizard__codex-card--compact"
			class:setup-wizard__codex-card--selected={selectedId === token.id}
			class:setup-wizard__codex-card--disabled={!selectable(token.id)}
		>
			<input
				type="radio"
				{name}
				value={token.id}
				checked={selectedId === token.id}
				disabled={!selectable(token.id)}
				onchange={() => choose(token.id)}
			/>
			<div class="setup-wizard__codex-card-body">
				<strong>{token.name}</strong>
				<p class="setup-wizard__codex-description text-sm text-gray-600">
					{token.description}
				</p>
				{#if !selectable(token.id)}
					<p class="text-xs text-gray-500 mt-1">{unavailable}</p>
				{/if}
			</div>
		</label>
	{/each}
	<label
		class="setup-wizard__codex-card setup-wizard__codex-card--compact"
		class:setup-wizard__codex-card--selected={selectedId === CUSTOM_TOKEN_ID}
		class:setup-wizard__codex-card--disabled={!selectable(CUSTOM_TOKEN_ID)}
	>
		<input
			type="radio"
			{name}
			value={CUSTOM_TOKEN_ID}
			checked={selectedId === CUSTOM_TOKEN_ID}
			disabled={!selectable(CUSTOM_TOKEN_ID)}
			onchange={() => choose(CUSTOM_TOKEN_ID)}
		/>
		<div class="setup-wizard__codex-card-body">
			<strong>Custom token</strong>
			<p class="setup-wizard__codex-description text-sm text-gray-600">
				Your own ICRC-1 ledger and its index canister. The symbol is read from the ledger.
			</p>
			{#if !selectable(CUSTOM_TOKEN_ID)}
				<p class="text-xs text-gray-500 mt-1">{unavailable}</p>
			{/if}
		</div>
	</label>
</div>

<style>
	.setup-wizard__codex-list {
		display: grid;
		gap: 0.75rem;
	}

	.setup-wizard__codex-card {
		display: flex;
		flex-direction: row;
		gap: 0.85rem;
		align-items: flex-start;
		border: 1px solid #e2e8f0;
		border-radius: 0.75rem;
		padding: 0.85rem 1rem;
		cursor: pointer;
		background: #ffffff;
	}

	.setup-wizard__codex-card--selected {
		border-color: #0b1120;
		background: #f8fafc;
	}

	.setup-wizard__codex-card--disabled {
		opacity: 0.65;
		cursor: not-allowed;
		background: #f3f4f6;
	}

	.setup-wizard__codex-card input {
		margin-top: 0.2rem;
	}

	.setup-wizard__codex-description {
		margin: 0.25rem 0 0;
	}
</style>
