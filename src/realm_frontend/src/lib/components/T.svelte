<script>
  import { _ } from "svelte-i18n";
  export let key;
  export let values = {};
  export let fallback = "";
  
  $: translatedText = $_(key, { values, default: fallback });
  // Check if we got back the raw key (meaning translation not loaded)
  $: showingRawKey = translatedText === key && !fallback;
</script>

{#if showingRawKey}
  <span class="text-gray-500 text-sm">•••</span>
{:else}
  {translatedText}
{/if}
