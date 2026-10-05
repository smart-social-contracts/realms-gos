<script lang="ts">import { page } from "$app/stores";
import { onMount } from "svelte";
import { _ } from "svelte-i18n";
import LikeButton from "$lib/components/LikeButton.svelte";
import Spinner from "$lib/components/Spinner.svelte";
import VerifiedBadge from "$lib/components/VerifiedBadge.svelte";
import ItemIcon from "$lib/components/ItemIcon.svelte";
import { isAuthenticated, principalStore } from "$lib/auth";
import {
  fileRegistryBaseUrl,
  fileUrl,
  listFiles,
  listingScreenshotUrls
} from "$lib/file-registry-client";
import {
  marketplaceClient
} from "$lib/marketplace-client";
import { categories, formatCount, formatPrice, formatTimeAgo, shortPrincipal } from "$lib/format";
let item = null;
let loading = true;
let error = "";
let files = [];
let filesError = "";
let liked = false;
let purchased = false;
let busy = false;
let busyAudit = false;
let auditMsg = "";
let activeTab = "overview";
let showInstallGuide = false;
let lightboxIndex = -1;
let lightboxEl;
function lightboxAction(node) {
  lightboxEl = node;
  return {
    destroy() {
      if (lightboxEl === node) lightboxEl = null;
    }
  };
}
function openLightbox(i) {
  lightboxIndex = i;
  lightboxEl?.showModal();
}
function closeLightbox() {
  lightboxIndex = -1;
  if (lightboxEl?.open) lightboxEl.close();
}
function onLightboxClick(e) {
  if (e.target === lightboxEl) closeLightbox();
}
$: id = decodeURIComponent($page.params.id);
$: void load(id);
$: void refreshLikes($isAuthenticated, id);
$: void refreshPurchased($isAuthenticated, $principalStore?.toText() ?? null, id);
$: screenshotUrls = item ? listingScreenshotUrls(item) : [];
async function load(extId) {
  loading = true;
  error = "";
  try {
    item = await marketplaceClient.getExtensionDetails(extId);
    filesError = "";
    if (item.file_registry_canister_id && item.file_registry_namespace) {
      try {
        files = await listFiles(item.file_registry_canister_id, item.file_registry_namespace);
      } catch (e) {
        filesError = e?.message ?? String(e);
      }
    }
  } catch (e) {
    error = e?.message ?? String(e);
    item = null;
  } finally {
    loading = false;
  }
}
async function refreshLikes(_a, extId) {
  if (!_a) {
    liked = false;
    return;
  }
  try {
    const principal = $principalStore?.toText();
    if (!principal) return;
    liked = await marketplaceClient.hasLiked(principal, "ext", extId);
  } catch {
    liked = false;
  }
}
async function refreshPurchased(_a, principal, extId) {
  if (!_a || !principal) {
    purchased = false;
    return;
  }
  try {
    purchased = await marketplaceClient.hasPurchasedExtension(principal, extId);
  } catch {
    purchased = false;
  }
}
async function doBuy() {
  if (!item || busy) return;
  busy = true;
  try {
    await marketplaceClient.buyExtension(item.extension_id);
    purchased = true;
    item = await marketplaceClient.getExtensionDetails(item.extension_id);
  } catch (e) {
    alert($_("detail.purchase_failed", { values: { error: e?.message ?? e } }));
  } finally {
    busy = false;
  }
}
async function doRequestAudit() {
  if (!item || busyAudit) return;
  busyAudit = true;
  auditMsg = "";
  try {
    await marketplaceClient.requestAudit("ext", item.extension_id);
    auditMsg = $_("detail.audit_requested");
    item = await marketplaceClient.getExtensionDetails(item.extension_id);
  } catch (e) {
    auditMsg = `${e?.message ?? e}`;
  } finally {
    busyAudit = false;
  }
}
async function doDelist() {
  if (!item) return;
  if (!confirm($_("detail.delist_confirm", { values: { id: item.extension_id } }))) return;
  try {
    await marketplaceClient.delistExtension(item.extension_id);
    auditMsg = $_("detail.delisted");
    item = null;
    setTimeout(() => {
      window.location.href = "/extensions";
    }, 800);
  } catch (e) {
    auditMsg = $_("detail.could_not_delist", { values: { error: e?.message ?? e } });
  }
}
function isOwner() {
  return Boolean(item && $principalStore && $principalStore.toText() === item.developer);
}
</script>

<a class="back" href="/extensions">{$_('detail.back_extensions')}</a>

{#if loading}
  <div class="state"><Spinner size={32} /></div>
{:else if error || !item}
  <div class="state error">{error || $_('detail.not_found')}</div>
{:else}
  <article class="detail">
    <header>
      <div class="icon-large"><ItemIcon icon={item.icon} kind="ext" /></div>
      <div class="title-block">
        <div class="title-row">
          <h1>{item.name}</h1>
          <VerifiedBadge status={item.verification_status} size="md" />
        </div>
        <p class="meta">
          v{item.version} · {$_('card.by')} {#if item.developer_name}{item.developer_name}{:else}<code>{shortPrincipal(item.developer)}</code>{/if} · {$_('detail.updated', { values: { time: formatTimeAgo(item.updated_at) } })}
        </p>
        <div class="badges">
          <span class="badge">{$_('detail.installs', { values: { count: formatCount(item.installs) } })}</span>
          <span class="badge">{item.price_e8s ? formatPrice(item.price_e8s) : $_('card.free')}</span>
          {#each categories(item.categories) as c (c)}
            <span class="badge cat">{c.replace(/_/g, ' ')}</span>
          {/each}
        </div>
      </div>
      <div class="cta">
        <LikeButton kind="ext" itemId={item.extension_id} liked={liked} count={item.likes} />
      </div>
    </header>

    <div class="tabs" role="tablist">
      <button
        class="tab"
        class:active={activeTab === 'overview'}
        on:click={() => (activeTab = 'overview')}
        role="tab"
        aria-selected={activeTab === 'overview'}
      >{$_('detail.overview')}</button>
      <button
        class="tab"
        class:active={activeTab === 'files'}
        on:click={() => (activeTab = 'files')}
        role="tab"
        aria-selected={activeTab === 'files'}
      >{$_('detail.files')} {files.length ? `(${files.length})` : ''}</button>
    </div>

    {#if activeTab === 'overview'}
      <section class="block" role="tabpanel">
        <p class="description">{item.description || $_('detail.no_description')}</p>

        {#if screenshotUrls.length > 0}
          <h3>{$_('detail.screenshots')}</h3>
          <div class="screenshot-gallery">
            {#each screenshotUrls as url, i (url)}
              <button
                type="button"
                class="screenshot-link"
                on:click={() => openLightbox(i)}
                aria-label={$_('detail.enlarge_screenshot', { values: { n: i + 1, total: screenshotUrls.length } })}
              >
                <img
                  src={url}
                  alt="{item.name} — {i + 1} / {screenshotUrls.length}"
                  loading="lazy"
                />
              </button>
            {/each}
          </div>
        {/if}

        <div class="install-guide">
          <button class="install-toggle" on:click={() => (showInstallGuide = !showInstallGuide)} aria-expanded={showInstallGuide}>
            <i class="ti ti-download" aria-hidden="true"></i>
            {$_('detail.how_to_btn')}
            <i class="ti {showInstallGuide ? 'ti-chevron-up' : 'ti-chevron-down'} chevron" aria-hidden="true"></i>
          </button>
          {#if showInstallGuide}
            <div class="install-body">
              <p class="intro">{$_('detail.how_to_intro_ext')}</p>
              <ol>
                <li>{$_('detail.how_to_step1')}</li>
                <li>{$_('detail.how_to_step2')}</li>
                <li>{$_('detail.how_to_step3')}</li>
                <li>{$_('detail.how_to_step4')}</li>
              </ol>
              <p class="id-row"><span class="id-label">{$_('detail.how_to_id')}:</span> <code>{item.extension_id}</code></p>
            </div>
          {/if}
        </div>
        {#if categories(item.categories).length > 0}
          <h3>{$_('detail.categories')}</h3>
          <div class="badges">
            {#each categories(item.categories) as c (c)}
              <span class="badge cat">{c.replace(/_/g, ' ')}</span>
            {/each}
          </div>
        {/if}
      </section>
    {:else if activeTab === 'files'}
      <section class="block" role="tabpanel">
        {#if !item.file_registry_canister_id || !item.file_registry_namespace}
          <p class="muted">{$_('detail.no_namespace')}</p>
        {:else if filesError}
          <p class="error">{$_('detail.files_load_error', { values: { error: filesError } })}</p>
        {:else if files.length === 0}
          <p class="muted">{$_('detail.no_files', { values: { namespace: item.file_registry_namespace } })}</p>
        {:else}
          <table class="files">
            <thead><tr><th>{$_('detail.col_path')}</th><th>{$_('detail.col_size')}</th><th>{$_('detail.col_type')}</th><th></th></tr></thead>
            <tbody>
              {#each files as f (f.path)}
                <tr>
                  <td><code>{f.path}</code></td>
                  <td>{formatBytes(f.size)}</td>
                  <td><code>{f.content_type}</code></td>
                  <td>
                    <a class="link" href={fileUrl(item.file_registry_canister_id, item.file_registry_namespace, f.path)} target="_blank" rel="noreferrer">{$_('detail.open')}</a>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
          <p class="muted small">
            {$_('detail.served_from')} <code>{item.file_registry_canister_id}</code>
            (<a href={fileRegistryBaseUrl(item.file_registry_canister_id)} target="_blank" rel="noreferrer">{$_('detail.registry_root')}</a>)
          </p>
        {/if}
      </section>
    {/if}

    {#if isOwner()}
      <section class="block owner">
        <h2>{$_('detail.owner_actions')}</h2>
        <div class="owner-actions">
          <button class="btn" disabled={busyAudit} on:click={doRequestAudit}>
            {busyAudit ? $_('detail.working') : $_('detail.request_audit')}
          </button>
          <a class="btn" href={`/upload?prefill=${encodeURIComponent(item.extension_id)}`}>{$_('detail.edit_new_version')}</a>
          <button class="btn danger" on:click={doDelist}>{$_('detail.delist')}</button>
          {#if auditMsg}<span class="audit-msg">{auditMsg}</span>{/if}
        </div>
        {#if item.verification_notes}
          <p class="audit-notes"><strong>{$_('detail.curator_notes')}</strong> {item.verification_notes}</p>
        {/if}
      </section>
    {/if}
  </article>
{/if}

<dialog
  use:lightboxAction
  class="lightbox"
  aria-label={$_('detail.screenshots')}
  on:click={onLightboxClick}
  on:close={() => (lightboxIndex = -1)}
>
  <button
    type="button"
    class="lightbox-close"
    on:click={closeLightbox}
    aria-label={$_('detail.close_screenshot')}
    title={$_('detail.close_screenshot')}
  >
    <i class="ti ti-x" aria-hidden="true"></i>
  </button>
  {#if item && lightboxIndex >= 0 && screenshotUrls[lightboxIndex]}
    <img
      class="lightbox-img"
      src={screenshotUrls[lightboxIndex]}
      alt="{item.name} — {lightboxIndex + 1} / {screenshotUrls.length}"
    />
  {/if}
</dialog>

<script lang="ts" context="module">function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
</script>

<style>
  .back { display: inline-block; margin-bottom: 1rem; color: var(--text-muted); text-decoration: none; }
  .back:hover { color: var(--text); }
  .state { text-align: center; padding: 4rem; color: var(--text-muted); }
  .state.error { color: var(--danger); }
  .detail { background: var(--surface); border: 1px solid var(--border); border-radius: 0.85rem; padding: 2rem; }
  header {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 1.5rem;
    align-items: flex-start;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
  }
  .icon-large {
    width: 88px; height: 88px; border-radius: 1rem;
    background: var(--surface-2); display: flex; align-items: center; justify-content: center;
    font-size: 2.5rem;
  }
  .title-block { min-width: 0; }
  .title-row { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
  h1 { margin: 0; font-size: 1.6rem; }
  .meta { margin: 0.4rem 0 0.85rem; color: var(--text-faint); font-size: 0.85rem; }
  .meta code { font-family: monospace; }
  .badges { display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .badge {
    background: var(--surface-2); color: var(--text-muted);
    font-size: 0.75rem; padding: 0.25rem 0.6rem; border-radius: 999px;
  }
  .badge.cat { text-transform: capitalize; }
  .cta { display: flex; flex-direction: column; gap: 0.5rem; align-items: flex-end; }
  .btn {
    border: 1px solid var(--border); background: var(--surface); color: var(--text-muted);
    padding: 0.5rem 0.95rem; border-radius: 0.5rem; transition: all 0.15s;
  }
  .btn:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
  .btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
  .btn.primary:hover:not(:disabled) { background: var(--primary-hover); }
  .btn.big { padding: 0.7rem 1.4rem; font-size: 0.95rem; }
  .tabs {
    display: flex; gap: 0.5rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 1.5rem;
  }
  .tab {
    background: transparent; border: none; padding: 0.7rem 1rem;
    color: var(--text-muted); border-bottom: 2px solid transparent;
    font-size: 0.9rem; cursor: pointer; transition: color 0.15s;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--primary); font-weight: 500; }
  .block { margin-bottom: 2rem; }
  .block h2 { font-size: 1.1rem; margin: 0 0 0.85rem; }
  .block h3 { font-size: 0.85rem; margin: 1.25rem 0 0.5rem; color: var(--text-faint); font-weight: 500; }
  .block p { color: var(--text-muted); margin: 0; line-height: 1.7; }
  .block .description { color: var(--text); }
  .screenshot-gallery {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 0.95rem;
    margin-top: 0.65rem;
  }
  .screenshot-link {
    display: block;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 0.5rem;
    overflow: hidden;
    background: none;
    cursor: zoom-in;
    transition: border-color 0.15s ease;
  }
  .screenshot-link:hover,
  .screenshot-link:focus-visible { border-color: var(--border-strong); }
  .screenshot-link img {
    width: 100%;
    aspect-ratio: 16 / 9;
    object-fit: cover;
    display: block;
  }
  .lightbox {
    width: 100vw;
    height: 100vh;
    max-width: 100vw;
    max-height: 100vh;
    margin: 0;
    padding: 2.5rem;
    border: none;
    background: rgba(0, 0, 0, 0.82);
    cursor: zoom-out;
  }
  .lightbox[open] {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .lightbox::backdrop {
    background: transparent;
  }
  .lightbox-img {
    max-width: min(96vw, 1400px);
    max-height: 90vh;
    width: auto;
    height: auto;
    object-fit: contain;
    border-radius: 0.4rem;
    cursor: default;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.35);
  }
  .lightbox-close {
    position: absolute;
    top: 1rem;
    right: 1rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2.5rem;
    height: 2.5rem;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.25);
    background: rgba(0, 0, 0, 0.45);
    color: #fff;
    border-radius: 999px;
    cursor: pointer;
  }
  .lightbox-close .ti { font-size: 1.35rem; line-height: 1; }
  .lightbox-close:hover,
  .lightbox-close:focus-visible { background: rgba(0, 0, 0, 0.7); }
  .files {
    width: 100%; border-collapse: collapse;
  }
  .files th, .files td {
    text-align: left; padding: 0.55rem 0.5rem; border-bottom: 1px solid var(--surface-2);
    font-size: 0.85rem;
  }
  .files th { color: var(--text-faint); font-weight: 500; }
  .files td code { font-size: 0.85rem; }
  .link { color: var(--accent); }
  .muted { color: var(--text-faint); }
  .small { font-size: 0.8rem; }
  .error { color: var(--danger); }
  .owner h2 { color: var(--accent); }
  .owner-actions { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
  .owner-actions .btn.danger { background: var(--danger); border-color: var(--danger); color: #fff; }
  .owner-actions .btn.danger:hover { opacity: 0.92; }
  .owner-actions a.btn { text-decoration: none; }
  .audit-msg { color: var(--text-muted); font-size: 0.85rem; }
  .audit-notes { margin-top: 0.85rem; background: var(--surface-2); padding: 0.75rem 1rem; border-radius: 0.5rem; }

  /* Install guide */
  .install-guide { margin-top: 1.5rem; border: 1px solid var(--border); border-radius: 0.6rem; overflow: hidden; }
  .install-toggle {
    display: flex; align-items: center; gap: 0.55rem;
    width: 100%; padding: 0.8rem 1rem;
    background: var(--surface-2); border: none;
    color: var(--text-muted); font-size: 0.9rem; font-weight: 500;
    cursor: pointer; text-align: left; transition: color 0.12s;
  }
  .install-toggle:hover { color: var(--text); }
  .install-toggle .ti { font-size: 1rem; }
  .install-toggle .chevron { margin-left: auto; }
  .install-body { padding: 1rem 1.1rem 1.1rem; background: var(--surface); }
  .install-body .intro { color: var(--text-muted); font-size: 0.875rem; margin: 0 0 0.85rem; line-height: 1.6; }
  .install-body ol { margin: 0 0 1rem 1.2rem; padding: 0; color: var(--text-muted); font-size: 0.875rem; line-height: 1.8; }
  .install-body .id-row { margin: 0; font-size: 0.85rem; }
  .install-body .id-label { color: var(--text-faint); }
  .install-body code { background: var(--surface-2); padding: 0.15rem 0.45rem; border-radius: 0.3rem; font-size: 0.85rem; user-select: all; }

  @media (max-width: 760px) {
    header { grid-template-columns: 1fr; }
    .cta { align-items: flex-start; }
  }
</style>
