<script>
  import { _ } from "svelte-i18n";
  export let key;
  export let values = {};
  export let fallback = "";
  export let spinnerSize = "sm";
  
  $: translatedText = $_(key, { values, default: fallback });
  $: showingRawKey = translatedText === key && !fallback;
</script>

{#if showingRawKey}
  <span class="text-gray-500 text-sm">•••</span>
{:else}
  {translatedText}
{/if}
