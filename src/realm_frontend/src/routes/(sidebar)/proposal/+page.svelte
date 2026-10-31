<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import {
    Card,
    Heading,
    Button,
    Badge
  } from 'flowbite-svelte';
  import { must, read } from '$lib/realm';

  let id;
  let organizationId;
  let proposalData = null;
  let error = null;

  function parseTimestamp(timestamp) {
    if (!timestamp) return null;
    const match = timestamp.match(/\((\d+)\)$/);
    if (match) {
      return parseInt(match[1]);
    }
    return null;
  }

  function formatDateTime(timestamp) {
    if (!timestamp) return '';
    
    const parsedTimestamp = parseTimestamp(timestamp);
    if (!parsedTimestamp) return 'Invalid date';
    
    const date = new Date(parsedTimestamp);
    const options = { 
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZoneName: 'short'
    };
    return date.toLocaleDateString(undefined, options);
  }

  async function loadProposalData() {
    if (!id || !organizationId) {
      console.log('Missing proposal ID or organization ID');
      return;
    }
    
    try {
      console.log('Fetching proposal:', id);
      proposalData = (await must(read('proposal_get', { proposal: id }))).proposal;
    } catch (err) {
      console.error('Error loading proposal data:', err);
      error = err.message;
    }
  }

  onMount(async () => {
    // Get proposal ID from path parameter
    id = $page.params.id;
    // Get organization ID from query parameter
    organizationId = new URL(window.location.href).searchParams.get('org');
    console.log(`onMount:id = ${id}, org = ${organizationId}`);
    await loadProposalData();
  });
</script>

<svelte:head>
  <title>Proposal Details | Dashboard</title>
</svelte:head>

<div class="mt-px space-y-4">
  {#if error}
    <div class="p-4 mb-4 text-sm text-red-800 rounded-lg bg-red-50 dark:bg-gray-800 dark:text-red-400" role="alert">
      <span class="font-medium">Error!</span> {error}
    </div>
  {/if}

  {#if proposalData}
    <Card size="xl" padding="xl" class="max-w-none">
      <div class="flex justify-between items-start mb-6">
        <div>
          <Heading tag="h3" class="text-2xl font-bold dark:text-white mb-2">
            {proposalData.title}
          </Heading>
          <p class="text-gray-600 dark:text-gray-400">
            Created: {formatDateTime(proposalData.timestamp_created)}
          </p>
        </div>
        <Badge
          color={proposalData.status === 'Approved' ? 'green' :
            proposalData.status === 'Voting' ? 'yellow' :
            proposalData.status === 'Rejected' ? 'red' : 'gray'}
          large
        >
          {proposalData.status}
        </Badge>
      </div>

      {#if proposalData.description}
        <div class="mb-6">
          <Heading tag="h4" class="text-lg font-semibold dark:text-white mb-2">
            Description
          </Heading>
          <p class="text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
            {proposalData.description}
          </p>
        </div>
      {/if}

      <div class="flex justify-between items-center">
        <Button color="alternative" href="/organization?id={organizationId}">
          Back to Organization
        </Button>
        {#if proposalData.status === 'Voting'}
          <div class="space-x-2">
            <Button color="green">Approve</Button>
            <Button color="red">Reject</Button>
          </div>
        {/if}
      </div>
    </Card>
  {:else}
    <Card>
      <div class="h-24 animate-pulse">
        <div class="h-4 w-3/4 rounded bg-gray-200 dark:bg-gray-700"></div>
        <div class="mt-4 h-4 w-1/2 rounded bg-gray-200 dark:bg-gray-700"></div>
      </div>
    </Card>
  {/if}
</div>
