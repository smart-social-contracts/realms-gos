<script>
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import Footer from '$lib/../routes/(sidebar)/Footer.svelte';
  import { _ } from 'svelte-i18n';
  import { resolveRealmMarkSrc } from '$lib/branding/realmMark';
  import { realmInfo, realmName, realmWelcomeMessage, realmManifesto } from '$lib/stores/realmInfo';
  import { isAuthenticated } from '$lib/stores/auth';
  import DemoDataBanner from '$lib/components/DemoDataBanner.svelte';
  
  // Fetch realm info on mount
  onMount(() => {
    realmInfo.fetch();
  });
</script>

<svelte:head>
  <title>{$realmName || $_('extensions.welcome.page_title')}</title>
</svelte:head>

<DemoDataBanner />
<div class="welcome-container">
  {#if browser}
    <div class="background-photo-container">
      <img
        src="/images/background.png"
        alt="{$realmName || 'Realm'} - {$_('extensions.welcome.alt_text.background')}"
        class="background-photo active"
      />
    </div>
    
    <!-- "Powered by" text -->
    <div class="built-with-love">
      <a href="https://realmsgos.org" target="_blank" rel="noopener noreferrer" class="built-with-link">
        Powered by
        <img src="/images/logo_horizontal_white.svg" alt="Realms GOS" width="100" height="25" class="inline-logo" />
      </a>
    </div>
  {/if}
  
  <!-- Top bar with logo -->
  <div class="top-bar">
    <div class="realms-logo">
      <img src={resolveRealmMarkSrc($realmInfo.logoUrl)} alt="{$realmName}" class="logo-img" onerror={(e) => { e.currentTarget.remove(); }} />
    </div>
  </div>

  <div class="content">
    <div class="hero-text">
      {#if $realmInfo.loading}
        <h1>{$_('extensions.welcome.loading')}</h1>
      {:else}
        <h1>{$_('extensions.welcome.hero.welcome_to')} {$realmName || $_('extensions.welcome.hero.this_realm')}</h1>
        {#if $realmWelcomeMessage}
          <p class="hero-subtitle welcome-message">{$realmWelcomeMessage}</p>
        {/if}
        {#if $realmManifesto}
          <p class="hero-subtitle realm-manifesto">{$realmManifesto}</p>
        {/if}
      {/if}
    </div>
    
    <div class="button-container">
      {#if $isAuthenticated}
        <a href="/" class="btn btn-primary">
          {$_('extensions.welcome.hero.enter_realm')}
        </a>
      {:else}
        <a href="/join" class="btn btn-primary">
          Log In
        </a>
      {/if}
    </div>
  </div>
</div>

<!-- Footer -->
<Footer />

<style>
  .welcome-container {
    position: relative;
    width: 100%;
    height: 100vh;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .background-photo-container {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: -2;
  }
  
  .background-photo {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    opacity: 0;
    transition: opacity 1s ease-in-out;
  }
  
  .background-photo.active {
    opacity: 1;
  }
  
  .photo-info {
    position: absolute;
    bottom: 1rem;
    left: 1rem;
    color: white;
    font-size: 0.9rem;
    font-weight: 400;
    text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.7);
    z-index: 1;
    background-color: rgba(0, 0, 0, 0.3);
    padding: 0.5rem 0.8rem;
    border-radius: 4px;
    backdrop-filter: blur(4px);
  }
  
  .built-with-love {
    position: absolute;
    bottom: 4rem;
    right: 1rem;
    background: rgba(0, 0, 0, 0.7);
    backdrop-filter: blur(10px);
    border-radius: 8px;
    padding: 0.6rem 1rem;
    font-size: 1.1rem;
    z-index: 20;
  }

  .built-with-link {
    color: white;
    text-decoration: none;
    display: flex;
    align-items: center;
    gap: 0.3rem;
  }

  .built-with-link:hover {
    color: rgba(255, 255, 255, 0.8);
    text-decoration: none;
  }
  
  .inline-logo {
    display: inline-block;
    vertical-align: middle;
    margin: 0 0.2rem;
  }

  
  .top-bar {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 80px;
    background-color: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(8px);
    z-index: 20;
    display: flex;
    align-items: center;
  }

  .realms-logo {
    margin-left: 2rem;
    z-index: 30;
  }

  .logo-img {
    height: 48px; /* Mobile size */
    width: auto;
    filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.3));
  }

  /* Desktop logo size */
  @media (min-width: 768px) {
    .logo-img {
      height: 52px; /* Larger size for desktop */
    }
  }

  /* Large desktop logo size */
  @media (min-width: 1024px) {
    .logo-img {
      height: 56px; /* Even larger for large screens */
    }
  }

  .content {
    position: relative;
    z-index: 1;
    text-align: center;
    color: #ffffff;
    padding: 2rem;
    display: flex;
    flex-direction: column;
    align-items: center;
    background-color: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(8px);
    border-radius: 12px;
    margin: 2rem;
  }
  
  /* Desktop layout - content on the right */
  @media (min-width: 768px) {
    .content {
      position: absolute;
      right: 0;
      top: 50%;
      transform: translateY(-50%);
      width: 50%;
      max-width: 600px;
      padding: 3rem;
      align-items: flex-start;
      text-align: left;
      background-color: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(8px);
      border-radius: 12px;
      margin-right: 2rem;
    }
  }

  .hero-text {
    text-align: center;
    margin-bottom: 2rem;
  }
  
  /* Desktop hero text alignment */
  @media (min-width: 768px) {
    .hero-text {
      text-align: left;
    }
  }
  
  h1 {
    font-size: 2.2rem;
    margin-bottom: 1.5rem;
    text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.5);
    line-height: 1.2;
    font-weight: 600;
  }
  
  .hero-subtitle {
    font-size: 1.1rem;
    margin-bottom: 0.5rem;
    text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5);
    font-weight: 400;
    opacity: 0.95;
  }
  
  .button-container {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin-top: 1rem;
    width: 100%;
    max-width: 300px;
  }

  /* Center button in desktop view */
  @media (min-width: 768px) {
    .button-container {
      align-self: center;
      align-items: center;
    }
  }
  
  .btn {
    display: inline-block;
    padding: 1rem 1.5rem;
    text-decoration: none;
    border-radius: 30px;
    font-weight: 600;
    transition: all 0.3s ease;
    text-align: center;
    letter-spacing: 0.5px;
  }
  
  .btn-primary {
    background-color: rgba(59, 130, 246, 0.9);
    color: white;
    border: 2px solid rgba(59, 130, 246, 1);
  }
  
  .btn-primary:hover {
    background-color: rgba(59, 130, 246, 1);
    transform: translateY(-2px);
    box-shadow: 0 4px 15px rgba(59, 130, 246, 0.4);
  }
  
  .btn-secondary {
    background-color: transparent;
    color: white;
    border: 2px solid rgba(255, 255, 255, 0.6);
  }
  
  .btn-secondary:hover {
    background-color: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.8);
    transform: translateY(-2px);
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.2);
  }
  
  .welcome-message {
    font-size: 1.3rem;
    font-weight: 500;
    margin-bottom: 1rem;
  }
  
  .realm-manifesto {
    font-size: 1rem;
    opacity: 0.9;
  }
  
  /* For mobile screens */
  @media (max-width: 767px) {
    h1 {
      font-size: 1.8rem;
      margin-bottom: 1rem;
    }
    
    .hero-subtitle {
      font-size: 1rem;
      margin-bottom: 0.4rem;
    }
    
    .welcome-message {
      font-size: 1.1rem;
    }
    
    .hero-text {
      margin-bottom: 1.5rem;
    }
    
    .button-container {
      max-width: 250px;
    }
    
    .btn {
      padding: 0.8rem 1.2rem;
    }
    
    /* Adjust positioning for mobile */
    .built-with-love {
      bottom: 1rem;
      right: 0.5rem;
      font-size: 0.8rem;
      padding: 0.4rem 0.6rem;
    }
  }
</style>
