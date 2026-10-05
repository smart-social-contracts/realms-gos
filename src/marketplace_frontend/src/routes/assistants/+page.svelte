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
import { categories } from "$lib/format";
let items = [];
let total = 0;
let page = 1;
let perPage = 24;
let loading = true;
let error = "";
let searchQuery = "";
let verifiedOnly = true;
let sortBy = "newest";
let domainFilter = "";
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
  goto(qs ? `/assistants?${qs}` : "/assistants", { replaceState: true, keepFocus: true, noScroll: true });
}
$: filtered = applyDomainFilter(items, domainFilter);
$: sorted = applySort(filtered, sortBy);
$: knownDomains = collectDomains(items);
function collectDomains(list) {
  const set = new Set();
  for (const a of list) {
    for (const d of categories(a.domains)) set.add(d);
  }
  return Array.from(set).sort();
}
function applyDomainFilter(list, dom) {
  if (!dom) return list;
  const needle = dom.toLowerCase();
  return list.filter((a) => categories(a.domains).map((d) => d.toLowerCase()).includes(needle));
}
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
    likedSet = new Set(my.filter((m) => m.item_kind === "assistant").map((m) => `assistant|${m.item_id}`));
  } catch {
    likedSet = new Set();
  }
}
async function load(p, pp, v) {
  loading = true;
  error = "";
  try {
    if (searchQuery.trim()) {
      items = await marketplaceClient.searchAssistants(searchQuery.trim(), v);
      total = items.length;
    } else {
      const r = await marketplaceClient.listAssistants(p, pp, v);
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
  <h1>{$_('assistants.title')}</h1>
  <p>{$_('assistants.subtitle')}</p>
</header>

<div class="controls">
  <input
    type="search"
    placeholder={$_('assistants.search_placeholder')}
    bind:value={searchQuery}
    on:keydown={(e) => e.key === 'Enter' && doSearch()}
  />
  <button class="btn" on:click={doSearch}>{$_('nav.search')}</button>
  <label class="sort">
    {$_('sort.domain')}
    <select bind:value={domainFilter}>
      <option value="">{$_('filter.all')}</option>
      {#each knownDomains as d}
        <option value={d}>{d.replace(/_/g, ' ')}</option>
      {/each}
    </select>
  </label>
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
  <div class="state error">{$_('assistants.load_error', { values: { error } })}</div>
{:else if sorted.length === 0}
  <div class="state empty">
    <p>{$_('assistants.empty')} <a href="/upload">{$_('assistants.empty_action')}</a></p>
  </div>
{:else}
  <div class="grid">
    {#each sorted as a}
      <ItemCard
        kind="assistant"
        id={a.assistant_id}
        name={a.name}
        description={a.description}
        version={a.version}
        developer={a.developer}
        developerName={a.developer_name}
        icon={a.icon}
        priceE8s={a.price_e8s}
        installs={a.installs}
        likes={a.likes}
        categoriesStr={a.categories || a.domains}
        verificationStatus={a.verification_status}
        liked={likedSet.has(`assistant|${a.assistant_id}`)}
        href={`/assistants/${encodeURIComponent(a.assistant_id)}`}
      />
    {/each}
  </div>
{/if}

<style>
  .head h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
  .head p { margin: 0 0 1.5rem; color: var(--text-muted); max-width: 70ch; }
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
