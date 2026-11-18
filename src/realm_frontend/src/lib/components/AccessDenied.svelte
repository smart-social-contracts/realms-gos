<script>
  import { goto } from '$app/navigation';
  import { activeQuarterId } from '$lib/stores/quarters';
  import { realmInfo } from '$lib/stores/realmInfo';
  import { setActiveQuarter } from '$lib/canisters';
  import { formatQuarterLabel } from '$lib/utils/quarterLabels';

  let { operation = '', message = '', onRetry = null } = $props();

  let switching = $state(false);

  const quarters = $derived($realmInfo.quarters ?? []);
  const activeQuarter = $derived(
    $activeQuarterId
      ? quarters.find((q) => q.canister_id === $activeQuarterId) ?? null
      : null
  );
  const quarterLabel = $derived(
    $activeQuarterId
      ? activeQuarter
        ? formatQuarterLabel(activeQuarter)
        : `quarter ${String($activeQuarterId).slice(0, 5)}…`
      : ''
  );

  async function backToCapital() {
    switching = true;
    try {
      activeQuarterId.set(null);
      await setActiveQuarter(null);
      if (typeof localStorage !== 'undefined') localStorage.removeItem('home_quarter');
      if (typeof onRetry === 'function') onRetry();
      else window.location.reload();
    } finally {
      switching = false;
    }
  }

  function joinQuarter() {
    goto('/join');
  }
</script>

<div class="flex items-start gap-3 rounded-lg border border-gray-200 bg-gray-50 px-5 py-4 dark:border-gray-700 dark:bg-gray-800/50">
  <svg class="mt-0.5 h-5 w-5 flex-shrink-0 text-gray-500 dark:text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M13 16h-1v-4h-1m1-4h.01M12 2a10 10 0 100 20 10 10 0 000-20z" />
  </svg>
  <div class="flex-1">
    {#if quarterLabel}
      <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">No access to {quarterLabel}</h3>
      <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
        This page is loading data from <span class="font-medium">{quarterLabel}</span>, where your account
        doesn't have access. Your account lives on a different quarter — switch back to the capital,
        or join {quarterLabel} first.
      </p>
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="rounded-lg bg-primary-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          onclick={backToCapital}
          disabled={switching}
        >
          {switching ? 'Switching…' : 'Back to capital'}
        </button>
        <button
          type="button"
          class="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
          onclick={joinQuarter}
        >
          Join a quarter
        </button>
      </div>
    {:else}
      <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">You don't have permission</h3>
      <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{message || "You don't have permission to view this."}</p>
    {/if}
    {#if operation}
      <p class="mt-2 text-xs text-gray-500 dark:text-gray-500 font-mono">{operation}</p>
    {/if}
  </div>
</div>
