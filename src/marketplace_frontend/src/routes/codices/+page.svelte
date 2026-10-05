<script lang="ts">import { onMount } from "svelte";
import { browser } from "$app/environment";
import { goto } from "$app/navigation";
import { page as pageStore } from "$app/stores";
import { _ } from "svelte-i18n";
import ItemCard from "$lib/components/ItemCard.svelte";
import SkeletonCard from "$lib/components/SkeletonCard.svelte";
import { isAuthenticated } from "$lib/auth";
import { marketplaceClient } from "$lib/marketplace-client";
import { parseVerifiedOnlyParam, setVerifiedOnlySearchParam } from "$lib/verified-filter";
let items = [];
let total = 0;
let page = 1;
let perPage = 24;
let loading = true;
let error = "";
let searchQuery = "";
let verifiedOnly = true;
let sortBy = "newest";
let likedSet = new Set();
let mounted = false;
let lastUrlQ = "";
onMount(() => {
  const params = $pageStore.url.searchParams;
  searchQuery = params.get("q") ?? "";
  verifiedOnly = parseVerifiedOnlyParam(params.get("verified"));
  lastUrlQ = searchQuery;
  mounted = true;
});
$: if (mounted) void load(page, perPage, verifiedOnly);
$: if (mounted) adoptUrlQuery($pageStore.url.searchParams);
$: void refreshLikes($isAuthenticated);
function adoptUrlQuery(params) {
  const q = params.get("q") ?? "";
  if (q === lastUrlQ) return;
  lastUrlQ = q;
  searchQuery = q;
  verifiedOnly = parseVerifiedOnlyParam(params.get("verified"));
  page = 1;
  load(page, perPage, verifiedOnly);
}
function syncUrl() {
  if (!browser) return;
  const params = new URLSearchParams();
  if (searchQuery.trim()) params.set("q", searchQuery.trim());
  setVerifiedOnlySearchParam(params, verifiedOnly);
  const qs = params.toString();
  lastUrlQ = searchQuery.trim();
  goto(qs ? `/codices?${qs}` : "/codices", { replaceState: true, keepFocus: true, noScroll: true });
}
$: sorted = applySort(items, sortBy);
function applySort(list, key) {
  const copy = [...list];
  if (key === "installs") {
    copy.sort((a, b) => b.installs - a.installs || b.likes - a.likes);
  } else if (key === "likes") {
    copy.sort((a, b) => b.likes - a.likes || b.installs - a.installs);
  } else if (key === "updated") {
    copy.sort((a, b) => b.updated_at - a.updated_at);
  } else {
    copy.sort((a, b) => b.created_at - a.created_at);
  }
  return copy;
}
async function refreshLikes(_authed) {
  if (!_authed) {
    likedSet = new Set();
    return;
  }
  try {
    const my = await marketplaceClient.myLikes();
    likedSet = new Set(my.filter((m) => m.item_kind === "codex").map((m) => `codex|${m.item_id}`));
  } catch {
    likedSet = new Set();
  }
}
async function load(p, pp, v) {
  loading = true;
  error = "";
  try {
    if (searchQuery.trim()) {
      items = await marketplaceClient.searchCodices(searchQuery.trim(), v);
      total = items.length;
    } else {
      const r = await marketplaceClient.listCodices(p, pp, v);
      items = r.listings;
      total = r.total_count;
    }
  } catch (e) {
    error = e?.message ?? String(e);
    items = [];
    total = 0;
  } finally {
    loading = false;
  }
}
function doSearch() {
  page = 1;
  syncUrl();
  load(page, perPage, verifiedOnly);
}
</script>

<header class="head">
  <h1>{$_('codices.title')}</h1>
  <p>{$_('codices.subtitle')}</p>
</header>

<div class="controls">
  <input
    type="search"
    placeholder={$_('codices.search_placeholder')}
    bind:value={searchQuery}
    on:keydown={(e) => e.key === 'Enter' && doSearch()}
  />
  <button class="btn" on:click={doSearch}>{$_('nav.search')}</button>
  <label class="sort">
    {$_('sort.label')}
    <select bind:value={sortBy}>
      <option value="newest">{$_('sort.newest')}</option>
      <option value="updated">{$_('sort.recently_updated')}</option>
      <option value="installs">{$_('sort.most_installs')}</option>
      <option value="likes">{$_('sort.most_likes')}</option>
    </select>
  </label>
  <label class="verified-toggle">
    <input type="checkbox" bind:checked={verifiedOnly} on:change={syncUrl} />
    {$_('filter.verified_only')}
  </label>
</div>

{#if loading}
  <div class="grid">
    {#each Array(8) as _}
      <SkeletonCard />
    {/each}
  </div>
{:else if error}
  <div class="state error">{$_('codices.load_error', { values: { error } })}</div>
{:else if sorted.length === 0}
  <div class="state empty">
    <p>{$_('codices.empty')} <a href="/upload">{$_('codices.empty_action')}</a></p>
  </div>
{:else}
  <div class="grid">
    {#each sorted as c}
      <ItemCard
        kind="codex"
        id={c.codex_id}
        name={c.name}
        description={c.description}
        version={c.version}
        developer={c.developer}
        developerName={c.developer_name}
        icon={c.icon}
        priceE8s={c.price_e8s}
        installs={c.installs}
        likes={c.likes}
        categoriesStr={c.categories}
        verificationStatus={c.verification_status}
        liked={likedSet.has(`codex|${c.codex_id}`)}
        href={`/codices/${encodeURIComponent(c.codex_id)}`}
      />
    {/each}
  </div>
{/if}

<style>
  .head h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
  .head p { margin: 0 0 1.5rem; color: var(--text-muted); }
  .controls { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 1.25rem; }
  input[type='search'] {
    flex: 1; min-width: 240px;
    padding: 0.7rem 0.9rem; border: 1px solid var(--border);
    border-radius: 0.5rem; background: var(--surface); font-size: 0.95rem;
  }
  .verified-toggle { display: inline-flex; align-items: center; gap: 0.35rem; color: var(--text-muted); font-size: 0.85rem; }
  .sort { display: inline-flex; align-items: center; gap: 0.35rem; color: var(--text-muted); font-size: 0.85rem; }
  .sort select {
    border: 1px solid var(--border); background: var(--surface);
    border-radius: 0.4rem; padding: 0.35rem 0.5rem; font: inherit; color: var(--text);
  }
  .btn { background: var(--primary); color: #fff; border: 1px solid var(--primary); padding: 0.7rem 1rem; border-radius: 0.5rem; }
  .btn:hover { background: var(--primary-hover); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 1.25rem; }
  .state { text-align: center; padding: 3rem; color: var(--text-muted); }
  .state.error { color: var(--danger); }
</style>
