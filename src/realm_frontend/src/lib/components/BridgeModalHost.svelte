<script lang="ts">
	import { fade, scale } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import type { Attachment } from 'svelte/attachments';
	import {
		bridgeModalRequest,
		dismissBridgeModal,
		type BridgeModalAction
	} from '$lib/stores/bridge-modal';

	// Extension panes sit inside containers that create a containing block for
	// `fixed`, which would pin the dialog to the pane instead of the viewport.
	const portal: Attachment<HTMLElement> = (node) => {
		document.body.appendChild(node);
	};

	function focusOnMount(shouldFocus: boolean): Attachment<HTMLElement> {
		return (node) => {
			if (shouldFocus) node.focus();
		};
	}

	function actionClass(tone: BridgeModalAction['tone']): string {
		switch (tone) {
			case 'danger':
				return 'bg-red-600 text-white hover:bg-red-700';
			case 'secondary':
				return 'bg-white text-gray-700 ring-1 ring-inset ring-gray-300 hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-200 dark:ring-gray-600 dark:hover:bg-gray-700';
			default:
				return 'bg-primary-600 text-white hover:bg-primary-700';
		}
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && $bridgeModalRequest) {
			dismissBridgeModal();
		}
	}
</script>

<svelte:window onkeydown={onKeydown} />

{#if $bridgeModalRequest}
	<div {@attach portal} class="fixed inset-0 z-[1100] flex items-center justify-center p-4">
		<button
			type="button"
			aria-label="Close dialog"
			class="absolute inset-0 cursor-default bg-gray-900/20 backdrop-blur-[2px]"
			transition:fade={{ duration: 120 }}
			onclick={dismissBridgeModal}
		></button>

		<div
			class="relative w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-gray-900/5 dark:bg-gray-900 dark:ring-white/10"
			role="dialog"
			aria-modal="true"
			aria-labelledby="bridge-modal-title"
			transition:scale={{ duration: 150, start: 0.97, easing: cubicOut }}
		>
			<div class="px-6 pt-6 pb-2">
				<div class="flex items-start justify-between gap-4">
					<h3
						id="bridge-modal-title"
						class="text-base font-semibold text-gray-900 dark:text-white"
					>
						{$bridgeModalRequest.title}
					</h3>
					<button
						type="button"
						aria-label="Close"
						class="-mr-1 -mt-1 rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-200"
						onclick={dismissBridgeModal}
					>
						<svg class="h-4 w-4" viewBox="0 0 20 20" fill="none" aria-hidden="true">
							<path
								d="M5 5l10 10M15 5L5 15"
								stroke="currentColor"
								stroke-width="1.75"
								stroke-linecap="round"
							/>
						</svg>
					</button>
				</div>
				<p
					class="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-gray-600 dark:text-gray-300"
				>
					{$bridgeModalRequest.body}
				</p>
			</div>

			<div class="flex flex-wrap justify-end gap-2 px-6 pt-4 pb-5">
				{#each $bridgeModalRequest.actions as action, i (action.id)}
					<button
						type="button"
						{@attach focusOnMount(i === $bridgeModalRequest.actions.length - 1)}
						class="rounded-lg px-4 py-2 text-sm font-medium shadow-sm transition-colors {actionClass(
							action.tone
						)}"
						onclick={() => $bridgeModalRequest?.resolve(action.id)}
					>
						{action.label}
					</button>
				{/each}
			</div>
		</div>
	</div>
{/if}
