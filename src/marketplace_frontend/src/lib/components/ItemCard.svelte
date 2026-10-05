<script lang="ts">import { _ } from "svelte-i18n";
import VerifiedBadge from "./VerifiedBadge.svelte";
import LikeButton from "./LikeButton.svelte";
import ItemIcon from "./ItemIcon.svelte";
import { categories, developerLabel, formatCount, formatPrice, looksLikePrincipal } from "$lib/format";
export let kind;
export let id;
export let name;
export let description;
export let version;
export let developer;
export let developerName = "";
export let icon = "";
export let priceE8s = 0;
export let installs = 0;
export let likes = 0;
export let categoriesStr = "";
export let verificationStatus = "unverified";
export let liked = false;
export let href;
export let thumbnail = "";
</script>

<a class="card" class:has-thumbnail={!!thumbnail} {href}>
  {#if thumbnail}
    <img class="thumbnail" src={thumbnail} alt={name} loading="lazy" />
  {/if}
  <div class="inner">
  <div class="row top">
    <div class="icon"><ItemIcon {icon} {kind} /></div>
    <div class="title-block">
      <div class="title-line">
        <h3>{name}</h3>
      </div>
      <p class="meta">
        <span>v{version}</span>
        <span class="dot">·</span>
        <span class="dev" class:mono={!developerName && looksLikePrincipal(developer)}>{$_('card.by')} {developerLabel(developer, developerName)}</span>
      </p>
    </div>
    <div class="price-badge">{priceE8s ? formatPrice(priceE8s) : $_('card.free')}</div>
  </div>

  <p class="description">{description}</p>

  {#if categoriesStr}
    <div class="categories">
      {#each categories(categoriesStr) as c}
        <span class="cat">{c.replace(/_/g, ' ')}</span>
      {/each}
    </div>
  {/if}

  <div class="row footer">
    <div class="footer-left">
      <VerifiedBadge status={verificationStatus} />
      {#if installs > 0}
        <span class="stat">{$_('card.installs', { values: { count: formatCount(installs) } })}</span>
      {/if}
    </div>
    <LikeButton {kind} itemId={id} {liked} count={likes} />
  </div>
  </div>
</a>

<style>
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    text-decoration: none;
    color: inherit;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.85rem;
    padding: 1.1rem 1.2rem;
    transition: all 0.15s ease;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  }
  .card.has-thumbnail {
    padding: 0;
    gap: 0;
    overflow: hidden;
  }
  .card:not(.has-thumbnail) .inner { display: contents; }
  .card.has-thumbnail .inner {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    padding: 1.1rem 1.2rem;
  }
  .thumbnail {
    width: 100%;
    aspect-ratio: 16 / 9;
    object-fit: cover;
    border-radius: 0.85rem 0.85rem 0 0;
    display: block;
  }
  .card:hover {
    border-color: var(--border-strong);
    transform: translateY(-1px);
    box-shadow: 0 8px 22px -10px rgba(0, 0, 0, 0.18);
  }
  .row { display: flex; align-items: center; gap: 0.75rem; }
  .row.top { align-items: flex-start; }
  .icon {
    width: 44px; height: 44px;
    border-radius: 0.6rem;
    background: var(--surface-2);
    display: flex; align-items: center; justify-content: center;
    font-size: 1.4rem; flex-shrink: 0;
    color: var(--text);
  }
  .icon .ti { font-size: 1.5rem; line-height: 1; }
  .title-block { flex: 1; min-width: 0; }
  .title-line { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  h3 { margin: 0; font-size: 1rem; font-weight: 600; color: var(--text); }
  .meta {
    margin: 3px 0 0; font-size: 0.75rem; color: var(--text-faint);
    display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap;
  }
  .meta .dot { color: var(--border-strong); }
  .meta .dev.mono { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; }
  .price-badge {
    background: var(--surface-2); color: var(--text-muted);
    padding: 0.3rem 0.6rem; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
    flex-shrink: 0; align-self: center;
  }
  .description {
    margin: 0; font-size: 0.85rem; color: var(--text-muted); line-height: 1.55;
    display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2;
    -webkit-box-orient: vertical; overflow: hidden;
  }
  .categories { display: flex; flex-wrap: wrap; gap: 0.35rem; }
  .cat {
    background: var(--surface-2); color: var(--text-muted);
    padding: 0.15rem 0.55rem; border-radius: 0.35rem;
    font-size: 0.7rem; text-transform: capitalize;
  }
  .footer { justify-content: space-between; padding-top: 0.5rem; border-top: 1px solid var(--surface-2); }
  .footer-left { display: flex; align-items: center; gap: 0.5rem; min-width: 0; }
  .stat { color: var(--text-faint); font-size: 0.8rem; }
</style>
