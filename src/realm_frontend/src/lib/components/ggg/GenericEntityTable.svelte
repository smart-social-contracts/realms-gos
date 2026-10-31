<script>
  export let entityType = '';
  export let items = [];
  export let loading = false;
  export let pagination = null;
  export let onPageChange = (page) => {
    console.log(`Page change requested for ${entityType} to page ${page}`);
  };
  
  console.log('🔧 GenericEntityTable: entityType', entityType);
  console.log('🔧 GenericEntityTable: items received', items);
  console.log('🔧 GenericEntityTable: items length', items?.length);
  console.log('🔧 GenericEntityTable: loading', loading);
  console.log('🔧 GenericEntityTable: pagination', pagination);
  
  // Alias for backward compatibility
  $: entities = items;
  
  // Reactive logging to track changes
  $: if (entities) {
    console.log('🔧 GenericEntityTable: entities updated for', entityType, 'length:', entities.length);
    if (entities.length > 0) {
      console.log('🔧 GenericEntityTable: first entity:', entities[0]);
    }
  }
  
  // Dynamically determine columns based on entity structure
  $: columns = entities.length > 0 ? 
    Object.keys(entities[0]).filter(key => 
      !key.startsWith('_internal') && 
      key !== 'timestamp_updated' &&
      key !== 'relations'
    ) : [];
  
  function formatValue(value, key) {
    if (value === null || value === undefined) return 'N/A';
    
    if (typeof value === 'object' && value !== null) {
      if (Array.isArray(value)) return `[${value.length} items]`;
      try {
        const parsed = JSON.parse(value);
        if (parsed.description) return parsed.description;
        if (parsed.type) return parsed.type;
        return JSON.stringify(parsed).substring(0, 50) + '...';
      } catch (e) {
        return JSON.stringify(value).substring(0, 50) + '...';
      }
    }
    
    if (key.includes('timestamp') || key.includes('date') || key.includes('created_at')) {
      try {
        // Handle different date formats
        if (typeof value === 'string') {
          // Try parsing the date string
          const date = new Date(value);
          if (isNaN(date.getTime())) {
            return value; // Return original if not a valid date
          }
          return date.toLocaleDateString();
        }
        return new Date(value).toLocaleDateString();
      } catch (e) {
        return value;
      }
    }
    
    if (key === 'amount' && typeof value === 'number') {
      return value.toLocaleString();
    }
    
    return String(value).length > 50 ? String(value).substring(0, 50) + '...' : value;
  }
  
  function getDisplayName(key) {
    return key.split('_').map(word => 
      word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
  }
  
  function getEntityIcon(entityType) {
    const icons = {
      'users': '👤',
      'mandates': '📜',
      'tasks': '📋',
      'transfers': '🔄',
      'organizations': '🏢',
      'disputes': '⚖️',
      'licenses': '📄',
      'instruments': '💰',
      'codexes': '💻',
      'trades': '🤝',
      'realms': '🏛️',
      'treasury': '🏦',
      'proposals': '🗳️',
      'votes': '✅'
    };
    return icons[entityType] || '📊';
  }
  
  // Extract meaningful information from metadata
  function getEntityDescription(entity, entityType) {
    if (entity.metadata) {
      try {
        const metadata = JSON.parse(entity.metadata);
        switch(entityType) {
          case 'proposals':
            return metadata.title || entity._id;
          case 'votes':
            return `${metadata.voter}: ${metadata.vote} on ${metadata.proposal_id}`;
          case 'transfers':
            return metadata.purpose || `Transfer: ${entity.amount}`;
          case 'mandates':
            return metadata.description || entity.name;
          case 'tasks':
            return metadata.description || entity._id;
          case 'licenses':
            return `${metadata.type} for ${metadata.holder}`;
          case 'instruments':
            return `${metadata.symbol}: ${metadata.description}`;
          default:
            return metadata.description || metadata.title || entity.name || entity._id;
        }
      } catch (e) {
        return entity.name || entity._id;
      }
    }
    return entity.name || entity._id;
  }
  
  // Get relationship info
  function getRelationshipInfo(entity) {
    if (entity.relations) {
      const relationCount = Object.values(entity.relations).reduce((sum, rel) => {
        return sum + (Array.isArray(rel) ? rel.length : 0);
      }, 0);
      if (relationCount > 0) {
        return `${relationCount} relations`;
      }
    }
    return '';
  }
  
  // Pagination helpers for users
  $: safePagination = pagination ? {
    page_num: Number(pagination.page_num),
    total_pages: Number(pagination.total_pages),
    total_items_count: Number(pagination.total_items_count),
    page_size: Number(pagination.page_size)
  } : null;
  $: currentPage = (safePagination?.page_num ?? 0) + 1;
  $: totalPages = safePagination?.total_pages ?? 1;
  $: hasNextPage = safePagination ? (safePagination.page_num + 1 < safePagination.total_pages) : false;
  $: hasPrevPage = safePagination ? (safePagination.page_num > 0) : false;
  
  $: if (pagination) console.log('GENERIC TABLE PAGINATION:', entityType, pagination);
</script>

<div class="w-full overflow-x-auto">
  <h2 class="text-xl font-bold mb-4 flex items-center">
    <span class="mr-2">{getEntityIcon(entityType)}</span>
    {getDisplayName(entityType)} ({entities.length})
  </h2>
  <p class="text-sm text-gray-600 mb-4">
    {#if entityType === 'users'}
      Manage and monitor user accounts and citizen profiles
    {:else if entityType === 'mandates'}
      Governance mandates and policies that define system rules
    {:else if entityType === 'tasks'}
      Automated processes and scheduled operations
    {:else if entityType === 'transfers'}
      Asset transfers and financial transactions between entities
    {:else if entityType === 'proposals'}
      Governance proposals for citizen voting and decision making
    {:else if entityType === 'votes'}
      Citizen votes on governance proposals and democratic participation
    {:else if entityType === 'instruments'}
      Financial instruments and digital assets in the treasury
    {:else if entityType === 'licenses'}
      Issued licenses and permits for various activities
    {:else if entityType === 'disputes'}
      Open disputes and conflicts requiring resolution
    {:else}
      {getDisplayName(entityType)} in the system
    {/if}
  </p>
  {#if loading}
    <div class="text-center py-10">
      <div class="inline-block animate-spin h-8 w-8 border-4 border-blue-500 border-t-transparent rounded-full"></div>
      <p class="mt-2 text-gray-600">Loading {entityType}...</p>
    </div>
  {:else if entities.length === 0}
    <div class="text-center py-10 bg-gray-50 rounded-lg">
      <p class="text-gray-600">No {entityType} found</p>
    </div>
  {:else}
    <div class="space-y-4">
      {#each entities as entity, index}
        <div class="bg-white border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow">
          <div class="flex justify-between items-start mb-3">
            <div class="flex-1">
              <h3 class="font-semibold text-gray-900 mb-1">
                {getEntityDescription(entity, entityType)}
              </h3>
              <p class="text-sm text-gray-600">ID: {entity._id}</p>
              {#if entity.timestamp_created}
                <p class="text-xs text-gray-500">
                  Created: {formatValue(entity.timestamp_created, 'timestamp_created')}
                </p>
              {/if}
            </div>
            <div class="flex items-center space-x-2">
              {#if getRelationshipInfo(entity)}
                <span class="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded">
                  {getRelationshipInfo(entity)}
                </span>
              {/if}
              <button class="text-blue-600 hover:text-blue-900 font-medium text-sm">
                View Details
              </button>
            </div>
          </div>
          <!-- Entity-specific information -->
          {#if entity.metadata}
            <div class="mt-3 p-3 bg-gray-50 rounded text-sm">
              <pre class="text-xs text-gray-600 whitespace-pre-wrap">{JSON.stringify(JSON.parse(entity.metadata), null, 2)}</pre>
            </div>
          {/if}
          <!-- Relationships -->
          {#if entity.relations && Object.keys(entity.relations).length > 0}
            <div class="mt-3 p-3 bg-blue-50 rounded text-sm">
              <strong class="text-blue-800">Relationships:</strong>
              <div class="mt-2 space-y-1">
                {#each Object.entries(entity.relations) as [relType, relEntities]}
                  {#if Array.isArray(relEntities) && relEntities.length > 0}
                    <div class="flex items-center">
                      <span class="font-medium text-blue-700">{getDisplayName(relType)}:</span>
                      <div class="ml-2 flex flex-wrap gap-1">
                        {#each relEntities.slice(0, 3) as relEntity}
                          <span class="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded">
                            {relEntity._id}
                          </span>
                        {/each}
                        {#if relEntities.length > 3}
                          <span class="text-xs text-blue-600">+{relEntities.length - 3} more</span>
                        {/if}
                      </div>
                    </div>
                  {/if}
                {/each}
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
  {#if pagination}
    <div class="flex justify-center items-center mt-4 space-x-2">
      <button 
        class="px-3 py-1 rounded border {hasPrevPage ? 'bg-blue-100 hover:bg-blue-200' : 'bg-gray-100 text-gray-400 cursor-not-allowed'}" 
        disabled={!hasPrevPage}
        on:click={() => onPageChange(currentPage - 2)}
      >
        Previous
      </button>
      <div class="flex space-x-1">
        {#if totalPages <= 7}
          {#each Array(totalPages) as _, i}
            <button 
              class="w-8 h-8 rounded-full {currentPage === i+1 ? 'bg-blue-500 text-white' : 'bg-gray-100 hover:bg-gray-200'}"
              on:click={() => onPageChange(i)}
            >
              {i+1}
            </button>
          {/each}
        {:else}
          <button 
            class="w-8 h-8 rounded-full {currentPage === 1 ? 'bg-blue-500 text-white' : 'bg-gray-100 hover:bg-gray-200'}"
            on:click={() => onPageChange(0)}
          >
            1
          </button>
          {#if currentPage > 3}
            <span class="px-1">...</span>
          {/if}
          {#each Array(3).fill(0) as _, i}
            {@const pageNum = Math.max(2, Math.min(currentPage - 1 + i, totalPages - 1))}
            {#if pageNum > 1 && pageNum < totalPages}
              <button 
                class="w-8 h-8 rounded-full {currentPage === pageNum ? 'bg-blue-500 text-white' : 'bg-gray-100 hover:bg-gray-200'}"
                on:click={() => onPageChange(pageNum - 1)}
              >
                {pageNum}
              </button>
            {/if}
          {/each}
          {#if currentPage < totalPages - 2}
            <span class="px-1">...</span>
          {/if}
          <button 
            class="w-8 h-8 rounded-full {currentPage === totalPages ? 'bg-blue-500 text-white' : 'bg-gray-100 hover:bg-gray-200'}"
            on:click={() => onPageChange(totalPages - 1)}
          >
            {totalPages}
          </button>
        {/if}
      </div>
      <button 
        class="px-3 py-1 rounded border {hasNextPage ? 'bg-blue-100 hover:bg-blue-200' : 'bg-gray-100 text-gray-400 cursor-not-allowed'}" 
        disabled={!hasNextPage}
        on:click={() => onPageChange(currentPage)}
      >
        Next
      </button>
    </div>
    <div class="text-xs text-gray-500 mt-2 text-center">
      Showing {entities.length} of {safePagination?.total_items_count || entities.length} {getDisplayName(entityType).toLowerCase()} (Page {currentPage} of {totalPages})
    </div>
  {/if}
</div> 