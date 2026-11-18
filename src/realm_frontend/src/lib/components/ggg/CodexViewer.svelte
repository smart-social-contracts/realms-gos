<script>
  export let codexes = [];
  export let loading = false;
  
  function formatCodexName(codex) {
    if (codex._id) {
      return codex._id.replace('codex_', '').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    }
    return 'Unknown Codex';
  }
  
  function getCodexDescription(codex) {
    if (codex.metadata) {
      try {
        const parsed = JSON.parse(codex.metadata);
        if (parsed.description) return parsed.description;
      } catch (e) {
        // ignore
      }
    }
    return 'No description available';
  }
  
  function formatCode(code) {
    if (!code) return '# No code available';
    
    // Clean up the code formatting
    return code
      .replace(/\\n/g, '\n')
      .replace(/\\t/g, '    ')
      .trim();
  }
  
  function copyToClipboard(code) {
    navigator.clipboard.writeText(code).then(() => {
      // Could add a toast notification here
      console.log('Code copied to clipboard');
    });
  }
</script>

<div class="w-full">
  <h2 class="text-xl font-bold mb-4 flex items-center">
    <span class="mr-2">💻</span>
    Codexes ({codexes.length})
  </h2>
  <p class="text-sm text-gray-600 mb-6">
    Smart contracts and rule engines that govern system behavior
  </p>
  
  {#if codexes.length > 0}
    <div class="space-y-6">
      {#each codexes as codex}
        <div class="bg-white border border-gray-200 rounded-lg shadow-sm">
          <!-- Header -->
          <div class="px-6 py-4 border-b border-gray-200 bg-gray-50 rounded-t-lg">
            <div class="flex justify-between items-start">
              <div>
                <h3 class="text-lg font-semibold text-gray-900">
                  {formatCodexName(codex)}
                </h3>
                <p class="text-sm text-gray-600 mt-1">
                  {getCodexDescription(codex)}
                </p>
                <div class="flex items-center mt-2 text-xs text-gray-500">
                  <span class="mr-4">ID: {codex._id}</span>
                  {#if codex.timestamp_created}
                    <span>Created: {new Date(codex.timestamp_created).toLocaleDateString()}</span>
                  {/if}
                </div>
              </div>
              <button 
                on:click={() => copyToClipboard(formatCode(codex.code))}
                class="px-3 py-1 bg-blue-100 text-blue-700 rounded text-sm hover:bg-blue-200 transition-colors"
                title="Copy code to clipboard"
              >
                📋 Copy
              </button>
            </div>
          </div>
          
          <!-- Code Display -->
          <div class="p-0">
            <div class="bg-gray-900 text-gray-100 rounded-b-lg overflow-x-auto">
              <div class="px-4 py-2 bg-gray-800 text-xs text-gray-500 border-b border-gray-700">
                Python • {formatCode(codex.code).split('\n').length} lines
              </div>
              <pre class="p-4 text-sm leading-relaxed overflow-x-auto"><code class="language-python">{formatCode(codex.code)}</code></pre>
            </div>
          </div>
          
          <!-- Related Tasks -->
          {#if codex.relations && codex.relations.tasks && codex.relations.tasks.length > 0}
            <div class="px-6 py-3 bg-blue-50 border-t border-gray-200">
              <div class="text-sm text-blue-800">
                <strong>Related Tasks:</strong>
                {#each codex.relations.tasks as task}
                  <span class="inline-block bg-blue-100 text-blue-700 px-2 py-1 rounded text-xs mr-2 mt-1">
                    {task._id}
                  </span>
                {/each}
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {:else if !loading}
    <div class="text-center py-12 bg-gray-50 rounded-lg">
      <span class="text-4xl mb-4 block">💻</span>
      <h3 class="text-lg font-semibold text-gray-700 mb-2">No Codexes Found</h3>
      <p class="text-gray-600 mb-4">No smart contracts have been created yet.</p>
      <div class="bg-yellow-50 border border-yellow-200 rounded p-3 text-sm text-yellow-800 max-w-md mx-auto">
        💡 <strong>Tip:</strong> Try clicking "Load Demo Data" to see example governance codexes.
      </div>
    </div>
  {:else}
    <div class="text-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
      <p class="text-gray-600">Loading codexes...</p>
    </div>
  {/if}
</div>

<style>
  /* Enhanced syntax highlighting for code blocks */
  pre {
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    line-height: 1.5;
  }
  
  code {
    font-family: inherit;
  }
  
  /* Simple Python syntax highlighting using CSS */
  :global(.language-python) {
    color: #f8f8f2;
  }
</style> 