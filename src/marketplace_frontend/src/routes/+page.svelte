<script lang="ts">import { onMount } from "svelte";
import { browser } from "$app/environment";
import { goto } from "$app/navigation";
import { page } from "$app/stores";
import { _ } from "svelte-i18n";
import ItemCard from "$lib/components/ItemCard.svelte";
import SkeletonCard from "$lib/components/SkeletonCard.svelte";
import InfoTip from "$lib/components/InfoTip.svelte";
import CapitolBackdrop from "$lib/components/CapitolBackdrop.svelte";
import { categories as parseCategories } from "$lib/format";
import { listingScreenshotUrls } from "$lib/file-registry-client";
import {
  marketplaceClient
} from "$lib/marketplace-client";
import { isAuthenticated, principalStore } from "$lib/auth";
import { parseVerifiedOnlyParam, setVerifiedOnlySearchParam } from "$lib/verified-filter";
const KIND_VALUES = ["ext", "codex", "assistant"];
const KIND_I18N = {
  ext: "kind.extensions",
  codex: "kind.codices",
  assistant: "kind.assistants"
};
const PUBLISH_I18N = {
  ext: "discover.publish_ext",
  codex: "discover.publish_codex",
  assistant: "discover.publish_assistant"
};
const LANG_NAMES = {
  en: "English",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  it: "Italiano",
  pt: "Português",
  zh: "\u4E2D\u6587",
  ja: "\u65E5\u672C\u8A9E",
  ko: "\uD55C\uAD6D\uC5B4",
  ar: "\u0627\u0644\u0639\u0631\u0628\u064A\u0629",
  ru: "\u0420\u0443\u0441\u0441\u043A\u0438\u0439",
  nl: "Nederlands",
  pl: "Polski",
  tr: "T\xFCrk\xE7e",
  hi: "\u0939\u093F\u0928\u094D\u0926\u0940"
};
function parseLangs(raw) {
  return raw.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
}
function langLabel(code) {
  return LANG_NAMES[code] ?? code.toUpperCase();
}
function stripBullet(text) {
  return String(text ?? "").replace(/^\s*•\s*/, "");
}
let kind = "ext";
let metric = "downloads";
let verifiedOnly = true;
let selectedCategory = "";
let selectedLanguage = "";
let loading = true;
let error = "";
let mounted = false;
let items = [];
let likedSet = new Set();
let searchQuery = "";
let catalogSection;
function scrollToCatalog(k) {
  if (k) kind = k;
  catalogSection?.scrollIntoView({ behavior: "smooth", block: "start" });
}
onMount(async () => {
  const params = $page.url.searchParams;
  const k = params.get("kind");
  if (k === "ext" || k === "codex" || k === "assistant") kind = k;
  const s = params.get("sort");
  if (s === "likes" || s === "downloads" || s === "newest") metric = s;
  verifiedOnly = parseVerifiedOnlyParam(params.get("verified"));
  if (kind === "ext") selectedCategory = params.get("category") ?? "";
  if (kind === "ext") selectedLanguage = params.get("lang") ?? "";
  searchQuery = params.get("q") ?? "";
  if (searchQuery.trim() && !k) {
    kind = await resolveKindForQuery(searchQuery.trim(), verifiedOnly);
  }
  mounted = true;
});
$: void load(kind, metric, verifiedOnly, searchQuery);
$: if (kind !== "ext" && selectedCategory) selectedCategory = "";
$: if (kind !== "ext" && selectedLanguage) selectedLanguage = "";
$: availableCategories = kind === "ext" ? Array.from(new Set(items.flatMap((it) => parseCategories(categoriesFor(it))))).sort() : [];
$: availableLanguages = kind === "ext" ? Array.from(new Set(items.flatMap((it) => parseLangs(it.languages ?? "")))).sort() : [];
$: displayItems = kind === "ext" ? items.filter((it) => !selectedCategory || parseCategories(categoriesFor(it)).includes(selectedCategory)).filter((it) => !selectedLanguage || parseLangs(it.languages ?? "").includes(selectedLanguage)) : items;
$: if (browser && mounted) syncUrl(kind, metric, verifiedOnly, selectedCategory, selectedLanguage);
function syncUrl(k, m, v, cat, lang) {
  const params = new URLSearchParams();
  if (searchQuery.trim()) params.set("q", searchQuery.trim());
  if (k !== "ext") params.set("kind", k);
  if (m !== "downloads") params.set("sort", m);
  setVerifiedOnlySearchParam(params, v);
  if (k === "ext" && cat) params.set("category", cat);
  if (k === "ext" && lang) params.set("lang", lang);
  const qs = params.toString();
  goto(qs ? `/?${qs}` : "/", { replaceState: true, keepFocus: true, noScroll: true });
}
async function resolveKindForQuery(q, verified) {
  try {
    const [assistants, extensions, codices] = await Promise.all([
      marketplaceClient.searchAssistants(q, verified),
      marketplaceClient.searchExtensions(q, verified),
      marketplaceClient.searchCodices(q, verified),
    ]);
    if (assistants.length) return "assistant";
    if (extensions.length) return "ext";
    if (codices.length) return "codex";
  } catch {
    /* fall through */
  }
  return "ext";
}
$: void refreshLikes($isAuthenticated, $principalStore?.toText());
async function refreshLikes(_authed, _principal) {
  if (!_authed) {
    likedSet = new Set();
    return;
  }
  try {
    const my = await marketplaceClient.myLikes();
    likedSet = new Set(my.map((m) => `${m.item_kind}|${m.item_id}`));
  } catch {
    likedSet = new Set();
  }
}
function byNewest(list) {
  return [...list].sort((a, b) => b.created_at - a.created_at);
}
async function load(k, m, v, q) {
  loading = true;
  error = "";
  try {
    const query = (q ?? "").trim();
    if (query) {
      if (k === "ext") items = await marketplaceClient.searchExtensions(query, v);
      else if (k === "codex") items = await marketplaceClient.searchCodices(query, v);
      else items = await marketplaceClient.searchAssistants(query, v);
    } else if (k === "ext") {
      items = m === "downloads" ? await marketplaceClient.topExtensionsByDownloads(20, v) : m === "likes" ? await marketplaceClient.topExtensionsByLikes(20, v) : byNewest((await marketplaceClient.listExtensions(1, 20, v)).listings);
    } else if (k === "codex") {
      items = m === "downloads" ? await marketplaceClient.topCodicesByDownloads(20, v) : m === "likes" ? await marketplaceClient.topCodicesByLikes(20, v) : byNewest((await marketplaceClient.listCodices(1, 20, v)).listings);
    } else {
      items = m === "downloads" ? await marketplaceClient.topAssistantsByDownloads(20, v) : m === "likes" ? await marketplaceClient.topAssistantsByLikes(20, v) : byNewest((await marketplaceClient.listAssistants(1, 20, v)).listings);
    }
  } catch (e) {
    error = e?.message ?? String(e);
    items = [];
  } finally {
    loading = false;
  }
}
function idOf(it) {
  return it.extension_id ?? it.codex_id ?? it.assistant_id ?? "";
}
function hrefOf(k, it) {
  if (k === "ext") return `/extensions/${encodeURIComponent(idOf(it))}`;
  if (k === "codex") return `/codices/${encodeURIComponent(idOf(it))}`;
  return `/assistants/${encodeURIComponent(idOf(it))}`;
}
function defaultIcon(k) {
  if (k === "codex") return "file-code";
  if (k === "assistant") return "robot";
  return "puzzle";
}
function categoriesFor(it) {
  if (it.categories) return it.categories;
  if (it.domains) return it.domains;
  return "";
}
function extensionThumbnail(ext) {
  return listingScreenshotUrls(ext)[0] || "";
}
</script>

<section class="landing-hero" aria-labelledby="landing-title">
  <div class="landing-bg" aria-hidden="true">
    <CapitolBackdrop />
    <div class="landing-overlay"></div>
  </div>
  <div class="landing-card">
    <div class="landing-wordmark">
      <img src="/images/logo_horizontal.svg" alt="Realms" />
    </div>
    <h1 id="landing-title" class="landing-tagline">{$_('about.hero.title')}</h1>
    <div class="landing-stanza">
      <p>{stripBullet($_('about.hero.subtitle1'))}</p>
      <p>{stripBullet($_('about.hero.subtitle2'))}</p>
      <p>{stripBullet($_('about.hero.subtitle3'))}</p>
    </div>
  </div>
  <p class="landing-credit">
    <span>{$_('landing.credit')}</span>
    <svg class="swiss" viewBox="0 0 32 32" aria-label={$_('landing.credit_country')}>
      <rect width="32" height="32" rx="2.5" fill="#D52B1E" />
      <path d="M13 7h6v6h6v6h-6v6h-6v-6H7v-6h6z" fill="#fff" />
    </svg>
  </p>
  <button type="button" class="scroll-cue" on:click={() => scrollToCatalog()} aria-label={$_('landing.scroll_hint')}>
    <span class="scroll-chevron" aria-hidden="true"></span>
  </button>
</section>

<section class="catalog" id="catalog" bind:this={catalogSection}>
  <header class="catalog-header">
    <h2>{$_('discover.title')}</h2>
    <p>{$_('discover.subtitle')}</p>
  </header>

<div class="kind-tabs" role="tablist" aria-label="Listing type">
  {#each KIND_VALUES as k}
    <button
      role="tab"
      aria-selected={kind === k}
      class:active={kind === k}
      on:click={() => (kind = k)}
    >{$_(KIND_I18N[k])}</button>
  {/each}
</div>

<div class="filters">
  <div class="toggle" role="group" aria-label="Sort by">
    <button aria-pressed={metric === 'downloads'} class:active={metric === 'downloads'} on:click={() => (metric = 'downloads')}>{$_('sort.most_downloaded')}</button>
    <button aria-pressed={metric === 'likes'} class:active={metric === 'likes'} on:click={() => (metric = 'likes')}>{$_('sort.most_liked')}</button>
    <button aria-pressed={metric === 'newest'} class:active={metric === 'newest'} on:click={() => (metric = 'newest')}>{$_('sort.newest')}</button>
  </div>
  <label class="verified-toggle">
    <input type="checkbox" bind:checked={verifiedOnly} />
    <span>{$_('filter.verified_only')}</span>
    <InfoTip text={$_('filter.verified_help')} label={$_('filter.verified_help_label')} />
  </label>
</div>

{#if kind === 'ext' && availableCategories.length > 0}
  <div class="cat-filter" role="group" aria-label="Filter extensions by category">
    <button class:active={selectedCategory === ''} on:click={() => (selectedCategory = '')}>{$_('filter.all')}</button>
    {#each availableCategories as c}
      <button
        class:active={selectedCategory === c}
        on:click={() => (selectedCategory = selectedCategory === c ? '' : c)}
      >{c.replace(/_/g, ' ')}</button>
    {/each}
  </div>
{/if}

{#if kind === 'ext' && availableLanguages.length > 0}
  <div class="cat-filter lang-filter" role="group" aria-label="Filter extensions by language">
    <span class="filter-label">{$_('filter.language')}:</span>
    <button class:active={selectedLanguage === ''} on:click={() => (selectedLanguage = '')}>{$_('filter.all')}</button>
    {#each availableLanguages as l}
      <button
        class:active={selectedLanguage === l}
        on:click={() => (selectedLanguage = selectedLanguage === l ? '' : l)}
      >{langLabel(l)}</button>
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
  <div class="state error"><p>{$_('discover.load_error', { values: { error } })}</p></div>
{:else if items.length === 0}
  <div class="state empty">
    <h2>{$_('discover.empty_title')}</h2>
    <p><a href="/upload">{$_(PUBLISH_I18N[kind])}</a></p>
  </div>
{:else if displayItems.length === 0}
  <div class="state empty">
    <h2>{$_('discover.no_matches_title')}</h2>
    <p>
      {#if selectedLanguage}
        {$_('discover.no_matches_lang', { values: { language: langLabel(selectedLanguage) } })}
      {:else}
        {$_('discover.no_matches', { values: { category: selectedCategory.replace(/_/g, ' ') } })}
      {/if}
      <button class="link" on:click={() => { selectedCategory = ''; selectedLanguage = ''; }}>{$_('discover.clear_filter')}</button>
    </p>
  </div>
{:else}
  <div class="grid">
    {#each displayItems as it}
      <ItemCard
        kind={kind}
        id={idOf(it)}
        name={it.name}
        description={it.description}
        version={it.version}
        developer={it.developer}
        developerName={it.developer_name}
        icon={it.icon || defaultIcon(kind)}
        priceE8s={it.price_e8s}
        installs={it.installs}
        likes={it.likes}
        categoriesStr={categoriesFor(it)}
        verificationStatus={it.verification_status}
        liked={likedSet.has(`${kind}|${idOf(it)}`)}
        href={hrefOf(kind, it)}
        thumbnail={kind === "ext" ? extensionThumbnail(it) : ""}
      />
    {/each}
  </div>
{/if}
</section>

<style>
  .landing-hero {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 2.5rem 1.25rem 5.5rem;
    overflow: hidden;
    background: #ffffff;
  }
  .landing-bg {
    position: absolute;
    inset: 0;
    background: #ffffff;
  }
  .landing-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: linear-gradient(to bottom, #ffffff55, #ffffff00 48%, #ffffffaa);
  }
  .landing-card {
    position: relative;
    z-index: 1;
    width: min(32rem, calc(100% - 2.5rem));
    padding: 2.35rem 2.1rem 2.05rem;
    background: #ffffff55;
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border: 1px solid #ffffff99;
    border-radius: 0.75rem;
    box-shadow: 0 1px 2px #0000000a;
    text-align: center;
    animation: hero-rise 0.5s ease-out 2s both;
  }
  .landing-card > :global(*) {
    animation: hero-fade 0.5s ease-out both;
  }
  .landing-wordmark {
    margin: 0 0 1.6rem;
    animation-delay: 2.14s;
  }
  .landing-wordmark img {
    display: block;
    margin: 0 auto;
    height: clamp(3.5rem, 8vw, 5.25rem);
    width: auto;
  }
  .landing-tagline {
    margin: 0 0 1.25rem;
    text-align: center;
    font-size: clamp(1.55rem, 3.2vw, 2.15rem);
    font-weight: 700;
    line-height: 1.18;
    letter-spacing: -0.02em;
    color: var(--text);
    text-wrap: balance;
    animation-delay: 2.22s;
  }
  .landing-stanza {
    margin: 0 auto;
    max-width: 26rem;
    font-size: 0.95rem;
    font-weight: 400;
    line-height: 1.65;
    text-align: center;
    color: var(--text-muted);
    animation-delay: 2.3s;
  }
  .landing-stanza p { margin: 0.15rem 0; }
  .landing-credit {
    position: absolute;
    z-index: 2;
    right: 1.5rem;
    bottom: 1.15rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0;
    font-size: 1.05rem;
    font-weight: 400;
    color: var(--text-faint);
    letter-spacing: 0.01em;
    white-space: nowrap;
    animation: hero-appear 0.5s ease-out 2s both;
  }
  .landing-credit .swiss {
    flex: none;
    width: 1.23rem;
    height: 1.23rem;
    border-radius: 0.15rem;
    box-shadow: 0 1px 6px #00000059;
  }
  .scroll-cue {
    position: absolute;
    z-index: 2;
    left: 50%;
    bottom: 1.35rem;
    transform: translate(-50%);
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2.4rem;
    height: 2.4rem;
    padding: 0;
    border: none;
    background: none;
    color: #111;
    cursor: pointer;
    animation: hero-appear 0.5s ease-out 2s both;
  }
  .scroll-chevron {
    width: 0.72rem;
    height: 0.72rem;
    margin-top: -0.2rem;
    border-right: 2px solid #111;
    border-bottom: 2px solid #111;
    transform: rotate(45deg);
    animation: scroll-chevron 1.45s ease-in-out 2s infinite;
  }
  .scroll-cue:hover { opacity: 0.7; }
  @keyframes scroll-chevron {
    0%, 100% { opacity: 0.25; transform: rotate(45deg) translateY(-3px); }
    45% { opacity: 1; transform: rotate(45deg) translateY(2px); }
  }
  @keyframes hero-fade {
    0% { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: none; }
  }
  @keyframes hero-rise {
    0% { opacity: 0; transform: translateY(14px); }
    to { opacity: 1; transform: none; }
  }
  @keyframes hero-appear {
    0% { opacity: 0; }
    to { opacity: 1; }
  }
  @media (prefers-reduced-motion: reduce) {
    .landing-card {
      animation: hero-appear 0.01s linear 2s both;
    }
    .landing-card > :global(*) { animation: none; }
    .landing-credit,
    .scroll-cue {
      animation: hero-appear 0.01s linear 2s both;
    }
    .scroll-chevron { animation: none; }
  }
  .catalog {
    max-width: 1200px;
    margin: 0 auto;
    padding: 2.5rem 1.5rem 0;
  }
  .catalog-header h2 {
    font-size: 1.35rem;
    margin: 0 0 0.35rem;
  }
  .catalog-header p {
    color: var(--text-muted);
    margin: 0 0 1.5rem;
  }

  @media (max-width: 760px) {
    .landing-hero {
      align-items: center;
      padding: 5.5rem 1.5rem 4.25rem;
    }
    .landing-overlay {
      background: linear-gradient(to bottom, #ffffff77, #ffffff22 42%, #ffffff11 55%, #ffffffc4);
    }
    .landing-card {
      width: 100%;
      padding: 1.5rem 1.2rem 1.35rem;
      background: #ffffff59;
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      border: 1px solid #ffffff99;
    }
    .landing-wordmark { margin-bottom: 0.9rem; }
    .landing-wordmark img { height: 2.4rem; }
    .landing-tagline { font-size: 1.35rem; margin-bottom: 0.75rem; }
    .landing-stanza { font-size: 0.86rem; line-height: 1.5; max-width: none; }
    .landing-credit {
      display: flex;
      left: 0.75rem;
      right: 0.75rem;
      bottom: 3.05rem;
      justify-content: center;
      white-space: normal;
      text-align: center;
      font-size: 0.62rem;
      line-height: 1.35;
    }
    .landing-credit .swiss { width: 0.72rem; height: 0.72rem; }
    .scroll-cue { bottom: 0.85rem; }
  }

  /* Primary navigation between listing types â reads as content tabs. */
  .kind-tabs {
    display: flex;
    gap: 1.5rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.25rem;
  }
  .kind-tabs button {
    background: none;
    border: none;
    padding: 0.6rem 0;
    margin-bottom: -1px;
    border-bottom: 2px solid transparent;
    font-size: 0.95rem;
    color: var(--text-faint);
    transition: color 0.15s ease, border-color 0.15s ease;
  }
  .kind-tabs button:hover { color: var(--text); }
  .kind-tabs button.active {
    color: var(--text);
    font-weight: 600;
    border-bottom-color: var(--primary);
  }

  /* Secondary filters, right-aligned and visually distinct from the tabs. */
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    margin-bottom: 2rem;
    align-items: center;
    justify-content: flex-end;
  }
  .toggle {
    display: inline-flex;
    background: var(--surface-2);
    border-radius: 0.5rem;
    padding: 0.2rem;
    gap: 0.2rem;
  }
  .toggle button {
    background: transparent;
    border: none;
    padding: 0.45rem 0.9rem;
    border-radius: 0.4rem;
    font-size: 0.85rem;
    color: var(--text-faint);
    transition: all 0.15s ease;
  }
  .toggle button:hover { color: var(--text); }
  .toggle button.active {
    background: var(--surface);
    color: var(--text);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  }
  .verified-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
  .cat-filter {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: -0.75rem 0 1.75rem;
  }
  .cat-filter .filter-label {
    font-size: 0.8rem;
    color: var(--text-faint);
    align-self: center;
  }
  .cat-filter button {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-muted);
    padding: 0.35rem 0.8rem;
    border-radius: 999px;
    font-size: 0.8rem;
    text-transform: capitalize;
    transition: all 0.15s ease;
  }
  .cat-filter button:hover { border-color: var(--border-strong); color: var(--text); }
  .cat-filter button.active {
    background: var(--primary);
    border-color: var(--primary);
    color: #fff;
    font-weight: 600;
  }
  .link {
    background: none;
    border: none;
    padding: 0;
    color: var(--text);
    text-decoration: underline;
    cursor: pointer;
    font: inherit;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
    gap: 1.25rem;
  }
  .state {
    text-align: center;
    padding: 4rem 2rem;
    color: var(--text-muted);
  }
  .state.error { color: var(--danger); }
  .state.empty h2 { margin: 0 0 0.5rem; color: var(--text); }
</style>
