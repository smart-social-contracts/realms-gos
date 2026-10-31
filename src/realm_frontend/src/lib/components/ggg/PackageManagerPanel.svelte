<script>
  import { onMount } from 'svelte';
  import { read, must } from '$lib/realm';

  let packages = [];
  let loading = true;
  let error = null;

  async function loadPackages() {
    loading = true;
    error = null;
    try {
      const rows = [];
      let from = 1;
      while (from) {
        const page = await must(read('extension_list', { from, count: 100 }));
        rows.push(...page.extensions.map((e) => ({ id: e.id, kind: 'extension', version: e.version, status: e.status })));
        from = page.next;
      }
      const { codex } = await must(read('codex_status', { count: 1 }));
      if (codex) {
        rows.unshift({ id: codex.id.split('@')[0], kind: 'codex', version: codex.version, status: codex.status });
      }
      packages = rows;
    } catch (e) {
      error = e?.message || String(e);
      packages = [];
    } finally {
      loading = false;
    }
  }

  onMount(loadPackages);
</script>

<div class="mb-8">
  <div class="flex flex-col md:flex-row md:items-start md:justify-between gap-4 mb-4">
    <div>
      <h3 class="text-xl font-bold text-gray-900 mb-1">Package Manager</h3>
      <p class="text-sm text-gray-600 max-w-2xl">
        Installed Codex and extensions. Locking and transferring packages is not supported by
        this realm version.
      </p>
    </div>
    <button
      class="text-sm text-gray-700 underline disabled:opacity-50"
      on:click={loadPackages}
      disabled={loading}
    >
      Refresh
    </button>
  </div>

  {#if loading}
    <p class="text-sm text-gray-600">Loading packages…</p>
  {:else if packages.length === 0}
    <p class="text-sm text-gray-600">No installed packages recorded.</p>
  {:else}
    <div class="overflow-x-auto border rounded-lg">
      <table class="min-w-full text-sm">
        <thead class="bg-gray-50 text-left text-gray-600">
          <tr>
            <th class="px-3 py-2 font-medium">id</th>
            <th class="px-3 py-2 font-medium">version</th>
            <th class="px-3 py-2 font-medium">status</th>
          </tr>
        </thead>
        <tbody>
          {#each packages as row}
            <tr class="border-t">
              <td class="px-3 py-2 font-medium text-gray-900 break-all">
                {row.id}
                {#if row.kind}
                  <span class="ml-1 text-xs text-gray-500">{row.kind}</span>
                {/if}
              </td>
              <td class="px-3 py-2 text-gray-800">{row.version || '—'}</td>
              <td class="px-3 py-2 text-gray-800">{row.status || '—'}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  {#if error}
    <p class="mt-3 text-sm text-red-700">{error}</p>
  {/if}
</div>
