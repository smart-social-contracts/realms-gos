<script lang="ts">import { onMount } from "svelte";
import { browser } from "$app/environment";
import { goto } from "$app/navigation";
import { page } from "$app/stores";
import { _, locale } from "svelte-i18n";
import { initI18n, setLocale, supportedLocales } from "$lib/i18n";
import "../index.scss";
import { bootstrapAuth, isAuthenticated, login, logout, principalStore } from "$lib/auth";
import { invalidateActor, marketplace } from "$lib/canisters";
import { shortPrincipal } from "$lib/format";
import { resolveCasalsUrlLive } from "$lib/config";
import { CONTACT_EMAIL, CONTACT_MAILTO } from "$lib/contact";

let casalsUrl = "";
$: isHome = $page.url.pathname === "/";
$: isFlush = isHome || $page.url.pathname === "/about";
let booted = false;
let isController = false;
let searchTerm = "";
let i18nReady = false;
let showLanguageMenu = false;
let heroScrolled = false;
function submitSearch() {
  const q = searchTerm.trim();
  if (!q) return;
  goto(`/?q=${encodeURIComponent(q)}`);
}
function onWindowScroll() {
  heroScrolled = window.scrollY > 24;
}
onMount(async () => {
  if (!browser) return;
  await initI18n();
  i18nReady = true;
  await bootstrapAuth();
  booted = true;
  refreshController();
  onWindowScroll();
  try {
    casalsUrl = await resolveCasalsUrlLive();
  } catch {
    casalsUrl = "";
  }
});
$: if (browser && $locale) document.documentElement.lang = $locale;
async function refreshController() {
  try {
    const r = await marketplace.status();
    const status = r?.Ok ?? r;
    isController = Boolean(status?.is_caller_controller);
  } catch {
    isController = false;
  }
}
async function handleLogin() {
  await login();
  invalidateActor();
  refreshController();
}
async function handleLogout() {
  await logout();
  invalidateActor();
  refreshController();
}
$: routeIsActive = (path) => {
  if (path === "/") return $page.url.pathname === "/";
  return $page.url.pathname.startsWith(path);
};
</script>

<svelte:window on:click={() => (showLanguageMenu = false)} on:scroll={onWindowScroll} />
<svelte:head>
  <title>Realms GOS - The Governance Operating System</title>
</svelte:head>

{#if browser && i18nReady}
{#if isHome}
  <div class="hero-chrome" class:hidden={heroScrolled}>
    <a class="hero-chrome-btn hero-chrome-about" href="/about">
      <span class="hero-chrome-label">{$_('landing.what_is_this')}</span>
    </a>
    {#if $isAuthenticated && $principalStore}
      <a class="hero-chrome-btn" href="/my-purchases" aria-label={$_('nav.my_purchases')} title={$principalStore.toText()}>
        <span class="hero-chrome-label">{shortPrincipal($principalStore.toText())}</span>
      </a>
    {:else}
      <button class="hero-chrome-btn hero-chrome-icon" on:click={handleLogin} aria-label={$_('nav.sign_in')} title={$_('nav.sign_in')}>
        <svg class="nav-svg" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="8" r="4" />
          <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
        </svg>
      </button>
    {/if}
    <div class="lang" on:click|stopPropagation>
      <button
        class="hero-chrome-btn hero-chrome-icon"
        on:click={() => (showLanguageMenu = !showLanguageMenu)}
        aria-label={$_('lang.select')}
        title={$_('lang.select')}
        aria-expanded={showLanguageMenu}
      >
        <svg class="nav-svg" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18" />
          <path d="M12 3c2.6 3 3.9 6 3.9 9s-1.3 6-3.9 9c-2.6-3-3.9-6-3.9-9s1.3-6 3.9-9z" />
        </svg>
      </button>
      {#if showLanguageMenu}
        <ul class="lang-menu" role="menu">
          {#each supportedLocales as loc}
            <li>
              <button
                role="menuitemradio"
                aria-checked={$locale === loc.id}
                class:active={$locale === loc.id}
                on:click={() => { setLocale(loc.id); showLanguageMenu = false; }}
              >{loc.name}</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </div>
{/if}
<header class="topbar" class:overlay={isHome} class:revealed={!isHome || heroScrolled}>
  <div class="bar">
    <div class="brand-group">
      <a href="/" class="brand" aria-label="Realms Marketplace home">
        <img src="/images/logo_sphere_only.svg" alt="" />
      </a>
    </div>

    <form class="search" on:submit|preventDefault={submitSearch} role="search">
      <input
        type="search"
        bind:value={searchTerm}
        placeholder={$_('nav.search_placeholder')}
        aria-label={$_('nav.search')}
      />
      <button type="submit" class="icon-btn" aria-label={$_('nav.search')} title={$_('nav.search')}>
        <svg class="nav-svg" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
      </button>
    </form>

    <nav class="actions" aria-label="Account and tools">
      {#if isController}
        <a class="icon-btn" href="/admin" class:active={routeIsActive('/admin')} aria-label={$_('nav.admin')} title={$_('nav.admin')}>
          <i class="ti ti-shield-lock" aria-hidden="true"></i>
        </a>
      {/if}

      <div class="lang" on:click|stopPropagation>
        <button
          class="icon-btn"
          on:click={() => (showLanguageMenu = !showLanguageMenu)}
          aria-label={$_('lang.select')}
          title={$_('lang.select')}
          aria-expanded={showLanguageMenu}
        >
          <svg class="nav-svg" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <path d="M3 12h18" />
            <path d="M12 3c2.6 3 3.9 6 3.9 9s-1.3 6-3.9 9c-2.6-3-3.9-6-3.9-9s1.3-6 3.9-9z" />
          </svg>
        </button>
        {#if showLanguageMenu}
          <ul class="lang-menu" role="menu">
            {#each supportedLocales as loc}
              <li>
                <button
                  role="menuitemradio"
                  aria-checked={$locale === loc.id}
                  class:active={$locale === loc.id}
                  on:click={() => { setLocale(loc.id); showLanguageMenu = false; }}
                >{loc.name}</button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>

      <span class="divider" aria-hidden="true"></span>

      {#if !booted}
        <span class="muted">…</span>
      {:else if $isAuthenticated && $principalStore}
        <a class="who" href="/my-purchases" title={$principalStore.toText()}>
          <i class="ti ti-user" aria-hidden="true"></i>
          <span class="who-id">{shortPrincipal($principalStore.toText())}</span>
        </a>
        <button class="icon-btn" on:click={handleLogout} aria-label={$_('nav.sign_out')} title={$_('nav.sign_out')}>
          <i class="ti ti-logout" aria-hidden="true"></i>
        </button>
      {:else}
        <button class="icon-btn" on:click={handleLogin} aria-label={$_('nav.sign_in')} title={$_('nav.sign_in')}>
          <svg class="nav-svg" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="8" r="4" />
            <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
          </svg>
        </button>
      {/if}
    </nav>
  </div>
</header>

<main class="main" class:flush={isFlush}>
  <slot />
</main>

<footer class="footer">
  <div class="footer-card">
    <div class="socials">
      <a
        class="social"
        href="https://github.com/smart-social-contracts/realms-gos"
        target="_blank"
        rel="noreferrer"
        aria-label={$_('footer.github')}
        title={$_('footer.github')}
      >
        <i class="ti ti-brand-github" aria-hidden="true"></i>
      </a>
      {#if casalsUrl}
        <a
          class="social casals"
          href={casalsUrl}
          target="_blank"
          rel="noreferrer"
          aria-label={$_('footer.casals')}
          title={$_('footer.casals')}
        >
          <img src="/images/casals-logo.png" alt="" width="28" height="28" />
        </a>
      {/if}
      <a
        class="social mail"
        id="contact"
        href={CONTACT_MAILTO}
        aria-label="{$_('footer.contact')} {CONTACT_EMAIL}"
        title={CONTACT_EMAIL}
      >
        <i class="ti ti-mail" aria-hidden="true"></i>
      </a>
    </div>
    <div class="build-line">{$_('footer.open_source')}</div>
    <div class="build-meta">{$_('footer.build', { values: { version: __BUILD_VERSION__, commit: __BUILD_COMMIT__ } })}</div>
    <a class="icp" href="https://internetcomputer.org" target="_blank" rel="noreferrer">
      <img src="/images/internet-computer-icp-logo.svg" alt="Internet Computer Logo" width="24" height="24" />
      <span>{$_('footer.built_on_ic')}</span>
    </a>
  </div>
</footer>

<a
  class="brain-mark"
  href="/"
  aria-label="Realms"
>
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"></path>
    <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"></path>
    <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"></path>
    <path d="M12 21v-3"></path>
  </svg>
</a>
{:else}
<div class="loading-screen"><div class="spinner"></div></div>
{/if}

<style>
  .topbar {
    position: sticky;
    top: 0;
    z-index: 30;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }
  .topbar.overlay {
    position: fixed;
    left: 0;
    right: 0;
    top: 0;
    overflow: hidden;
    transition: transform 0.25s ease, opacity 0.2s ease, visibility 0.2s;
  }
  .topbar.overlay:not(.revealed) {
    transform: translateY(-110%);
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
  }
  .topbar.overlay.revealed {
    transform: none;
    opacity: 1;
    visibility: visible;
    pointer-events: auto;
  }
  .hero-chrome {
    position: fixed;
    z-index: 40;
    box-sizing: border-box;
    top: max(0.5rem, env(safe-area-inset-top, 0px));
    left: 0;
    width: 100%;
    max-width: 100vw;
    max-width: 100svw;
    padding: 0 0.65rem;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: center;
    column-gap: 0.4rem;
    pointer-events: none;
    transition: opacity 0.2s ease, visibility 0.2s;
    animation: hero-chrome-appear 0.5s ease-out 2s both;
    text-size-adjust: 100%;
    -webkit-text-size-adjust: 100%;
  }
  .hero-chrome > * { pointer-events: auto; min-width: 0; }
  .hero-chrome-about { justify-self: start; }
  .hero-chrome .lang { justify-self: end; }
  .hero-chrome-btn.hero-chrome-icon {
    width: 2.25rem;
    padding: 0;
    flex: none;
  }
  .nav-svg {
    width: 1.1rem;
    height: 1.1rem;
    display: block;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.75;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .hero-chrome.hidden {
    animation: none;
    opacity: 0;
    visibility: hidden;
    pointer-events: none;
  }
  .hero-chrome-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.4rem;
    height: 2.4rem;
    padding: 0 0.95rem;
    border: 1px solid var(--border-strong);
    border-radius: 999px;
    background: #ffffff;
    color: var(--text);
    font-size: 0.875rem;
    font-weight: 600;
    text-decoration: none;
    white-space: nowrap;
    cursor: pointer;
    box-shadow: 0 1px 2px #0000000a;
    transition: background 0.15s ease, border-color 0.15s ease;
  }
  .hero-chrome-btn .ti { font-size: 1.15rem; line-height: 1; }
  .hero-chrome-btn:hover { background: var(--surface); border-color: var(--border-strong); }
  .bar {
    display: flex;
    align-items: center;
    gap: 1.25rem;
    max-width: 1200px;
    margin: 0 auto;
    padding: 0 1.5rem;
    height: 60px;
  }
  .brand-group {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    flex-shrink: 0;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    text-decoration: none;
  }
  .brand img {
    height: 36px;
    width: auto;
    display: block;
  }
  .search {
    flex: 1;
    display: flex;
    gap: 0.4rem;
    max-width: 520px;
    margin: 0 auto;
  }
  .search input {
    flex: 1;
    min-width: 0;
    padding: 0.5rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 0.5rem;
    background: var(--surface-2);
    font-size: 0.9rem;
    color: var(--text);
  }
  .search input:focus {
    outline: none;
    border-color: var(--border-strong);
    background: var(--surface);
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    flex-shrink: 0;
  }
  .icon-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 38px;
    height: 38px;
    border: 1px solid transparent;
    background: none;
    color: var(--text-muted);
    border-radius: 0.5rem;
    text-decoration: none;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .icon-btn .ti { font-size: 1.25rem; line-height: 1; }
  .icon-btn:hover { background: var(--surface-2); color: var(--text); }
  .icon-btn.active { background: var(--surface-3); color: var(--text); }
  .search .icon-btn { border-color: var(--border); }
  .text-btn {
    display: inline-flex;
    align-items: center;
    height: 38px;
    padding: 0 0.75rem;
    border: none;
    background: none;
    color: var(--text);
    font-size: 0.875rem;
    font-weight: 600;
    border-radius: 0.5rem;
    cursor: pointer;
    white-space: nowrap;
  }
  .text-btn:hover { background: var(--surface-2); }
  .divider {
    width: 1px;
    height: 24px;
    background: var(--border);
    margin: 0 0.25rem;
  }
  .who {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.85rem;
    color: var(--text-muted);
    white-space: nowrap;
    text-decoration: none;
    padding: 0.35rem 0.5rem;
    border-radius: 0.5rem;
    transition: all 0.12s ease;
  }
  .who:hover { background: var(--surface-2); color: var(--text); }
  .who .ti { font-size: 1.1rem; }
  .who-id { font-family: 'SF Mono', 'Fira Code', monospace; }
  .muted { color: var(--text-faint); }

  .lang { position: relative; display: inline-flex; }
  .lang-menu {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    min-width: 160px;
    margin: 0;
    padding: 0.35rem;
    list-style: none;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.6rem;
    box-shadow: 0 10px 30px -12px rgba(0, 0, 0, 0.25);
    z-index: 60;
  }
  .lang-menu li { margin: 0; }
  .lang-menu button {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.5rem 0.7rem;
    border: none;
    background: none;
    border-radius: 0.4rem;
    color: var(--text-muted);
    font-size: 0.875rem;
    cursor: pointer;
    transition: all 0.12s ease;
  }
  .lang-menu button:hover { background: var(--surface-2); color: var(--text); }
  .lang-menu button.active { color: var(--text); font-weight: 600; background: var(--surface-3); }

  .loading-screen {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }
  .spinner {
    width: 32px;
    height: 32px;
    border: 3px solid var(--surface-2);
    border-top-color: var(--primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @keyframes hero-chrome-appear {
    0% { opacity: 0; }
    to { opacity: 1; }
  }
  @media (prefers-reduced-motion: reduce) {
    .hero-chrome { animation: hero-chrome-appear 0.01s linear 2s both; }
  }
  .main {
    max-width: 1200px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }
  .main.flush {
    max-width: none;
    padding: 0 0 4rem;
  }

  /* Footer: centered links and build line, no card chrome. */
  .footer {
    padding: 0 1.5rem 2rem;
  }
  .footer-card {
    max-width: 1200px;
    margin: 0 auto;
    padding: 1.25rem 0 0.5rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
  }
  .footer-card .socials {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
  }
  .footer-card .social {
    color: var(--text-muted);
    text-decoration: none;
    transition: color 0.15s ease;
  }
  .footer-card .social:hover { color: var(--text); }
  .footer-card .social .ti { font-size: 1.4rem; }
  .footer-card .social.casals img {
    width: 28px;
    height: 28px;
    display: block;
    object-fit: contain;
  }
  .footer-card .social.mail .ti {
    font-size: 28px;
    line-height: 1;
  }
  .brain-mark {
    position: fixed;
    z-index: 45;
    right: max(1rem, env(safe-area-inset-right, 0px));
    bottom: max(1rem, env(safe-area-inset-bottom, 0px));
    display: flex;
    align-items: center;
    justify-content: center;
    width: 3rem;
    height: 3rem;
    border-radius: 999px;
    background: #ffffff;
    color: var(--text);
    border: 1px solid var(--border);
    box-shadow: 0 8px 24px -12px rgba(0, 0, 0, 0.45);
    text-decoration: none;
  }
  .brain-mark svg {
    width: 1.35rem;
    height: 1.35rem;
    display: block;
  }
  .brain-mark:hover { color: var(--text); background: var(--surface); }
  .footer-card .build-line {
    font-size: 0.75rem;
    color: var(--text-faint);
  }
  .footer-card .build-meta {
    font-size: 0.7rem;
    color: var(--text-faint);
    font-family: 'SF Mono', 'Fira Code', monospace;
  }
  .footer-card .icp {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    color: var(--text-muted);
    text-decoration: none;
    font-size: 0.875rem;
    transition: color 0.15s ease;
  }
  .footer-card .icp:hover { color: var(--text); }
  .footer-card .icp img { width: 24px; height: 24px; display: block; }

  @media (max-width: 760px) {
    .hero-chrome {
      top: max(0.45rem, env(safe-area-inset-top, 0px));
      padding: 0 0.55rem;
      column-gap: 0.3rem;
    }
    .hero-chrome-btn { height: 2rem; padding: 0 0.65rem; font-size: 0.75rem; }
    .hero-chrome-btn.hero-chrome-icon { width: 2rem; padding: 0; }
    .bar {
      flex-wrap: nowrap;
      height: 52px;
      padding: 0 0.65rem;
      gap: 0.35rem;
    }
    .brand img { height: 28px; }
    .search { max-width: none; margin: 0; min-width: 0; gap: 0.25rem; }
    .search input { padding: 0.4rem 0.55rem; font-size: 0.8rem; }
    .icon-btn { width: 34px; height: 34px; }
    .divider, .who-id { display: none; }
  }
</style>
