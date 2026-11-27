<script lang="ts">
  // The demo notice the CLFO requires on every page. It collapses to one line and never goes away: this is not
  // the test-mode banner, and it has no close button on purpose.
  import { onMount } from 'svelte';
  import { _ } from 'svelte-i18n';
  import { PRIVACY_URL, REPORT_URL, TERMS_URL, readCollapsed, writeCollapsed } from '$lib/demo-notice';

  let collapsed = false;

  onMount(() => {
    collapsed = readCollapsed(() => localStorage);
  });

  function toggle() {
    collapsed = !collapsed;
    writeCollapsed(() => localStorage, collapsed);
  }
</script>

<aside class="demo-notice" class:collapsed aria-labelledby="demo-notice-title">
  <div class="inner">
    <p class="text">
      <strong id="demo-notice-title">{$_('demo_notice.title')}</strong>
      {#if !collapsed}<span class="body">{$_('demo_notice.body')}</span>{/if}
    </p>
    <p class="links">
      <a href={PRIVACY_URL} target="_blank" rel="noreferrer">{$_('demo_notice.privacy')}</a>
      <span aria-hidden="true">·</span>
      <a href={TERMS_URL} target="_blank" rel="noreferrer">{$_('demo_notice.terms')}</a>
      <span aria-hidden="true">·</span>
      <a href={REPORT_URL}>{$_('demo_notice.report')}</a>
    </p>
    <button type="button" class="toggle" aria-expanded={!collapsed} on:click={toggle}>
      {collapsed ? $_('demo_notice.expand') : $_('demo_notice.collapse')}
    </button>
  </div>
</aside>

<style>
  /* Sticky at the end of the page: always on screen, and it never covers the footer. */
  .demo-notice {
    position: sticky;
    bottom: 0;
    z-index: 45;
    background: var(--primary);
    color: #ffffff;
    font-size: 0.85rem;
    line-height: 1.45;
  }
  .inner {
    max-width: 1200px;
    margin: 0 auto;
    padding: 0.75rem 1.5rem;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.4rem 1rem;
  }
  .text {
    flex: 1 1 32rem;
    margin: 0;
  }
  .collapsed .text {
    flex-basis: auto;
  }
  .links {
    margin: 0;
    display: inline-flex;
    gap: 0.4rem;
    white-space: nowrap;
  }
  .links a {
    color: #ffffff;
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .toggle {
    margin-left: auto;
    padding: 0.25rem 0.7rem;
    border: 1px solid #ffffff80;
    border-radius: 999px;
    background: none;
    color: #ffffff;
    font-size: 0.8rem;
    white-space: nowrap;
  }
  .toggle:hover {
    background: #ffffff1a;
  }
  @media (max-width: 760px) {
    .demo-notice {
      font-size: 0.8rem;
    }
    .inner {
      padding: 0.6rem 1rem;
    }
  }
</style>
