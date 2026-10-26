<script>
  import { onMount } from 'svelte';
  import { read, run } from '$lib/realm';

  let status = null;
  let loading = true;
  let error = null;
  let notice = null;
  let busy = false;

  function slotOf(id, version, hash) {
    return { name: id.split('@')[0], version, hash };
  }

  async function loadStatus() {
    loading = true;
    error = null;
    try {
      const data = await read('codex_status', { count: 1 });
      if (!data.ok) {
        error = data.error;
        status = null;
      } else {
        const codex = data.codex;
        status = {
          current: codex && slotOf(codex.id, codex.version, codex.checksum),
          previous: codex?.previous ? slotOf(codex.previous, codex.previous.split('@')[1]) : null,
          has_previous: !!codex?.previous
        };
      }
    } catch (e) {
      error = e?.message || String(e);
      status = null;
    } finally {
      loading = false;
    }
  }

  function slotLabel(slot) {
    if (!slot) return 'none';
    const name = slot.name || slot.id || 'codex';
    const version = slot.version ? ` ${slot.version}` : '';
    const hash = slot.hash ? String(slot.hash).slice(0, 12) : '';
    return hash ? `${name}${version} (${hash}…)` : `${name}${version}`;
  }

  async function revertOverlay() {
    if (busy) return;
    const ok = window.confirm(
      'Revert the realm codex to the previous package? This replaces the current overlay and does not merge files.'
    );
    if (!ok) return;
    busy = true;
    error = null;
    notice = null;
    try {
      const data = await run('codex_revert');
      if (!data.ok) {
        error = data.error;
      } else if (data.proposal) {
        notice = `Proposal ${data.proposal} filed: the codex reverts once it is accepted.`;
      }
      await loadStatus();
    } catch (e) {
      error = e?.message || String(e);
    } finally {
      busy = false;
    }
  }

  onMount(loadStatus);
</script>

<div class="mb-8 bg-amber-50 border border-amber-200 rounded-xl p-6">
  <div class="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
    <div>
      <h3 class="text-xl font-bold text-gray-900 mb-1">Codex overlay</h3>
      <p class="text-sm text-gray-600 max-w-2xl">
        One codex per realm. An update is replace, not merge. Safe mode stops
        host hook calls without wiping users, departments, or cases. Revert
        flips to the previous package. After beta these controls are used by
        whoever has <code class="text-xs">codex.revert</code> (root / Congress).
      </p>
    </div>
    <button
      class="text-sm text-amber-800 underline disabled:opacity-50"
      on:click={loadStatus}
      disabled={busy}
    >
      Refresh
    </button>
  </div>

  {#if loading}
    <p class="mt-4 text-sm text-gray-600">Loading overlay status…</p>
  {:else if status}
    <dl class="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
      <div class="bg-white rounded-lg border p-3">
        <dt class="text-gray-500">Current</dt>
        <dd class="font-medium text-gray-900 break-all">{slotLabel(status.current)}</dd>
      </div>
      <div class="bg-white rounded-lg border p-3">
        <dt class="text-gray-500">Previous</dt>
        <dd class="font-medium text-gray-900 break-all">{slotLabel(status.previous)}</dd>
      </div>
      <div class="bg-white rounded-lg border p-3">
        <dt class="text-gray-500">Safe mode</dt>
        <dd class="font-medium text-gray-900">Not supported by this realm version</dd>
      </div>
    </dl>
  {/if}

  {#if error}
    <p class="mt-3 text-sm text-red-700">{error}</p>
  {/if}
  {#if notice}
    <p class="mt-3 text-sm text-amber-800">{notice}</p>
  {/if}

  <div class="mt-4 flex flex-wrap gap-3">
    <button
      class="px-4 py-2 rounded text-sm font-medium border border-amber-300 bg-white text-amber-900 hover:bg-amber-100 disabled:opacity-50"
      disabled
    >
      Enable safe mode
    </button>
    <button
      class="px-4 py-2 rounded text-sm font-medium bg-amber-800 text-white hover:bg-amber-900 disabled:opacity-50"
      on:click={revertOverlay}
      disabled={busy || !status?.has_previous}
    >
      Revert to previous
    </button>
  </div>
</div>
