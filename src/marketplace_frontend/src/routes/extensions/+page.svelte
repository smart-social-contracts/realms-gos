<script lang="ts">import { onMount } from "svelte";
import { browser } from "$app/environment";
import { goto } from "$app/navigation";
import { page as pageStore } from "$app/stores";
import { _ } from "svelte-i18n";
import ItemCard from "$lib/components/ItemCard.svelte";
import SkeletonCard from "$lib/components/SkeletonCard.svelte";
import { isAuthenticated } from "$lib/auth";
import { categories as parseCategories } from "$lib/format";
import { listingScreenshotUrls } from "$lib/file-registry-client";
import { marketplaceClient } from "$lib/marketplace-client";
import { parseVerifiedOnlyParam, setVerifiedOnlySearchParam } from "$lib/verified-filter";
function extensionThumbnail(ext) {
  return listingScreenshotUrls(ext)[0] || "";
}
let items = [];
let total = 0;
let page = 1;
let perPage = 24;
let loading = true;
let error = "";
let searchQuery = "";
let verifiedOnly = true;
let selectedCategory = "";
let sortBy = "newest";
let likedSet = new Set();
let mounted = false;
let lastUrlQ = "";
onMount(() => {
  const params = $pageStore.url.searchParams;
  searchQuery = params.get("q") ?? "";
  verifiedOnly = parseVerifiedOnlyParam(params.get("verified"));
  selectedCategory = params.get("category") ?? "";
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
  if (selectedCategory) params.set("category", selectedCategory);
  const qs = params.toString();
  lastUrlQ = searchQuery.trim();
  goto(qs ? `/extensions?${qs}` : "/extensions", { replaceState: true, keepFocus: true, noScroll: true });
}
$: availableCategories = Array.from(
  new Set(items.flatMap((e) => parseCategories(e.categories)))
).sort();
function selectCategory(c) {
  selectedCategory = selectedCategory === c ? "" : c;
  syncUrl();
}
$: sorted = applySort(
  items.filter((e) => !selectedCategory || parseCategories(e.categories).includes(selectedCategory)),
  sortBy
);
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
    likedSet = new Set(my.filter((m) => m.item_kind === "ext").map((m) => `ext|${m.item_id}`));
  } catch {
    likedSet = new Set();
  }
}
async function load(p, pp, v) {
  loading = true;
  error = "";
  try {
    if (searchQuery.trim()) {
      items = await marketplaceClient.searchExtensions(searchQuery.trim(), v);
      total = items.length;
    } else {
      const r = await marketplaceClient.listExtensions(p, pp, v);
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
  <h1>{$_('extensions.title')}</h1>
  <p>{$_('extensions.subtitle')}</p>
</header>

<div class="controls">
  <input
    type="search"
    placeholder={$_('extensions.search_placeholder')}
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

{#if availableCategories.length > 0}
  <div class="cat-filter" role="group" aria-label="Filter by category">
    <button class:active={selectedCategory === ''} on:click={() => selectCategory('')}>{$_('filter.all')}</button>
    {#each availableCategories as c}
      <button class:active={selectedCategory === c} on:click={() => selectCategory(c)}>
        {c.replace(/_/g, ' ')}
      </button>
    {/each}
  </div>
{/if}

{#if loading}
  <div class="grid">
    {#each Array(8) as _}
      <SkeletonCard />
    {/each}
  </div>
{:else if error}
  <div class="state error">{$_('extensions.load_error', { values: { error } })}</div>
{:else if sorted.length === 0 && selectedCategory}
  <div class="state empty">
    <p>{$_('extensions.no_category', { values: { category: selectedCategory.replace(/_/g, ' ') } })} <button class="link" on:click={() => { selectedCategory = ''; syncUrl(); }}>{$_('discover.clear_filter')}</button></p>
  </div>
{:else if sorted.length === 0}
  <div class="state empty">
    <p>{$_('extensions.empty')} <a href="/upload">{$_('extensions.empty_action')}</a></p>
  </div>
{:else}
  <div class="grid">
    {#each sorted as ext}
      <ItemCard
        kind="ext"
        id={ext.extension_id}
        name={ext.name}
        description={ext.description}
        version={ext.version}
        developer={ext.developer}
        developerName={ext.developer_name}
        icon={ext.icon}
        priceE8s={ext.price_e8s}
        installs={ext.installs}
        likes={ext.likes}
        categoriesStr={ext.categories}
        verificationStatus={ext.verification_status}
        liked={likedSet.has(`ext|${ext.extension_id}`)}
        href={`/extensions/${encodeURIComponent(ext.extension_id)}`}
        thumbnail={extensionThumbnail(ext)}
      />
    {/each}
  </div>

  {#if !searchQuery && total > perPage}
    <div class="pager">
      <button disabled={page <= 1} on:click={() => (page = Math.max(1, page - 1))}>{$_('pager.prev')}</button>
      <span>{$_('pager.page_of', { values: { page, total: Math.max(1, Math.ceil(total / perPage)) } })}</span>
      <button
        disabled={page * perPage >= total}
        on:click={() => (page = page + 1)}
      >{$_('pager.next')}</button>
    </div>
  {/if}
{/if}

<style>
  .head h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
  .head p { margin: 0 0 1.5rem; color: var(--text-muted); }
  .controls {
    display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; margin-bottom: 1.25rem;
  }
  input[type='search'] {
    flex: 1; min-width: 240px;
    padding: 0.7rem 0.9rem; border: 1px solid var(--border);
    border-radius: 0.5rem; background: var(--surface); font-size: 0.95rem;
  }
  .verified-toggle { display: inline-flex; align-items: center; gap: 0.35rem; color: var(--text-muted); font-size: 0.85rem; }
  .cat-filter { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1.5rem; }
  .cat-filter .filter-label { font-size: 0.8rem; color: var(--text-faint); align-self: center; }
  .cat-filter button {
    background: var(--surface); border: 1px solid var(--border); color: var(--text-muted);
    padding: 0.35rem 0.8rem; border-radius: 999px; font-size: 0.8rem; text-transform: capitalize;
    transition: all 0.15s ease;
  }
  .cat-filter button:hover { border-color: var(--border-strong); color: var(--text); }
  .cat-filter button.active { background: var(--primary); border-color: var(--primary); color: #fff; font-weight: 600; }
  .link { background: none; border: none; padding: 0; color: var(--text); text-decoration: underline; cursor: pointer; font: inherit; }
  .sort { display: inline-flex; align-items: center; gap: 0.35rem; color: var(--text-muted); font-size: 0.85rem; }
  .sort select {
    border: 1px solid var(--border); background: var(--surface);
    border-radius: 0.4rem; padding: 0.35rem 0.5rem; font: inherit; color: var(--text);
  }
  .btn {
    background: var(--primary); color: #fff; border: 1px solid var(--primary);
    padding: 0.7rem 1rem; border-radius: 0.5rem;
  }
  .btn:hover { background: var(--primary-hover); }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
    gap: 1.25rem;
  }
  .state { text-align: center; padding: 3rem; color: var(--text-muted); }
  .state.error { color: var(--danger); }
  .pager {
    display: flex; gap: 1rem; align-items: center; justify-content: center; padding: 2rem 0;
  }
  .pager button {
    background: var(--surface); border: 1px solid var(--border); padding: 0.5rem 1rem; border-radius: 0.4rem;
  }
  .pager button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
