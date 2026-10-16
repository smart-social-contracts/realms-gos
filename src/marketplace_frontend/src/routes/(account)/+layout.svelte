<script lang="ts">
  import { _ } from 'svelte-i18n';
  import { page } from '$app/stores';
  import { ACCOUNT_ALPHA } from '$lib/config';
  import { roles } from '$lib/roles';

  $: active = (path: string) => $page.url.pathname.startsWith(path);
  $: alpha = ACCOUNT_ALPHA && active('/my-purchases');
</script>

<div class="account">
  <aside class="sidebar">
    <nav class="side-nav" aria-label="Account navigation">
      <a class="item" href="/my-purchases" class:active={active('/my-purchases')}>
        <i class="ti ti-shopping-bag" aria-hidden="true"></i>
        <span>{$_('nav.my_purchases')}</span>
        {#if ACCOUNT_ALPHA}<span class="soon">{$_('account.coming_soon_badge')}</span>{/if}
      </a>
      <a class="item" href="/upload" class:active={active('/upload')}>
        <i class="ti ti-upload" aria-hidden="true"></i>
        <span>{$_('nav.upload')}</span>
      </a>
      <a class="item" href="/developer" class:active={active('/developer')}>
        <i class="ti ti-code" aria-hidden="true"></i>
        <span>{$_('nav.developer')}</span>
      </a>
      {#if $roles.is_admin}
        <span class="sep" aria-hidden="true"></span>
        <a class="item" href="/admin" class:active={active('/admin')}>
          <i class="ti ti-shield-lock" aria-hidden="true"></i>
          <span>{$_('nav.admin')}</span>
        </a>
      {/if}
    </nav>
  </aside>

  <div class="content">
    {#if alpha}
      <div class="alpha-banner" role="status">
        <strong>{$_('account.coming_soon_title')}</strong>
        <p>{$_('account.coming_soon_body')}</p>
      </div>
      <div class="alpha-preview" inert>
        <slot />
      </div>
    {:else}
      <slot />
    {/if}
  </div>
</div>

<style>
  .account {
    display: grid;
    grid-template-columns: 200px 1fr;
    gap: 2rem;
    align-items: flex-start;
  }
  .sidebar {
    position: sticky;
    top: 80px;
  }
  .side-nav {
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.75rem;
    padding: 0.5rem;
  }
  .item {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    padding: 0.65rem 0.85rem;
    border-radius: 0.5rem;
    color: var(--text-muted);
    text-decoration: none;
    font-size: 0.9rem;
    transition: all 0.12s ease;
  }
  .item:hover { background: var(--surface-2); color: var(--text); }
  .item.active { background: var(--surface-2); color: var(--text); font-weight: 500; }
  .item .ti { font-size: 1.1rem; flex-shrink: 0; }
  .sep { height: 1px; background: var(--border); margin: 0.35rem 0.5rem; }
  .soon {
    margin-left: auto;
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: var(--text-faint);
    background: var(--surface-2);
    border-radius: 999px;
    padding: 0.1rem 0.4rem;
  }
  .content { min-width: 0; }
  .alpha-banner {
    margin: 0 0 1rem;
    padding: 0.85rem 1rem;
    border: 1px solid var(--border);
    border-radius: 0.75rem;
    background: var(--surface-2);
  }
  .alpha-banner strong { display: block; margin-bottom: 0.25rem; }
  .alpha-banner p { margin: 0; color: var(--text-muted); font-size: 0.9rem; }
  .alpha-preview { opacity: 0.55; filter: grayscale(0.4); }

  @media (max-width: 760px) {
    .account { grid-template-columns: 1fr; gap: 1rem; }
    .sidebar { position: static; min-width: 0; }
    .side-nav { flex-direction: row; overflow-x: auto; padding: 0.4rem; }
    .item { padding: 0.5rem 0.75rem; white-space: nowrap; font-size: 0.85rem; }
    .sep { width: 1px; height: auto; margin: 0.35rem 0.15rem; }
  }
</style>
