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

  let id = $page.params.id;
  let organizationId = $page.url.searchParams.get('org');
  let proposalData = null;
  let error = null;
  let mounted = false;

  console.log('Initial load - id:', id, 'org:', organizationId);

  // Format timestamp handling both numeric and string formats
  function formatTimestamp(timestamp) {
    if (!timestamp) return '';
    
    try {
      // If it's a number or numeric string, treat as milliseconds
      if (!isNaN(timestamp)) {
        return new Date(Number(timestamp)).toLocaleString();
      }
      
      // If it's a string with timestamp in parentheses
      if (typeof timestamp === 'string') {
        const match = timestamp.match(/\((\d+)\)/);
        if (match) {
          return new Date(Number(match[1])).toLocaleString();
        }
        
        // Try parsing the string directly
        const date = new Date(timestamp);
        if (!isNaN(date.getTime())) {
          return date.toLocaleString();
        }
      }
    } catch (err) {
      console.error('Error formatting timestamp:', timestamp, err);
    }
    
    return 'Invalid date';
  }


  // Calculate provisional result based on current votes
  function calculateProvisionalResult(proposal) {
    if (!proposal?.vote_counts) return "Insufficient data";
    
    const yay = proposal.vote_counts.Yay || 0;
    const nay = proposal.vote_counts.Nay || 0;
    const total = yay + nay;
    
    if (total === 0) return "No votes cast";
    
    const approval_rate = (yay / total) * 100;
    const quorum_met = total >= (proposal.quorum || 0);
    
    if (!quorum_met) return `Pending (${Math.round(approval_rate)}% approval, quorum not met)`;
    if (approval_rate >= 50) return `Likely to pass (${Math.round(approval_rate)}% approval)`;
    return `Likely to fail (${Math.round(approval_rate)}% approval)`;
  }

  // Get detailed status information
  function getDetailedStatus(proposal) {
    const status = proposal?.status || 'Unknown';
    const now = new Date();
    const deadline = proposal?.deadline ? new Date(proposal.deadline) : null;
    
    switch(status) {
      case 'Voting':
        if (deadline && deadline > now) {
          return { 
            phase: 'Active Voting', 
            description: 'Proposal is currently accepting votes from eligible members',
            next_action: 'Vote before deadline'
          };
        }
        return { 
          phase: 'Voting Ended', 
          description: 'Voting period has concluded, awaiting result calculation',
          next_action: 'Results pending'
        };
      case 'Approved':
        return { 
          phase: 'Approved', 
          description: 'Proposal has been approved by the community and is ready for execution',
          next_action: 'Awaiting execution'
        };
      case 'Rejected':
        return { 
          phase: 'Rejected', 
          description: 'Proposal did not meet approval requirements',
          next_action: 'None - proposal closed'
        };
      case 'Executed':
        return { 
          phase: 'Executed', 
          description: 'Proposal has been successfully implemented',
          next_action: 'Monitor outcomes'
        };
      default:
        return { 
          phase: 'Pending', 
          description: 'Proposal is being prepared for voting',
          next_action: 'Awaiting voting period'
        };
    }
  }

  // Calculate time remaining
  function getTimeRemaining(timestamp) {
    if (!timestamp) return '';
    
    try {
      let targetDate;
      
      // Handle numeric timestamp
      if (!isNaN(timestamp)) {
        targetDate = new Date(Number(timestamp));
      } 
      // Handle string with timestamp in parentheses
      else if (typeof timestamp === 'string') {
        const match = timestamp.match(/\((\d+)\)/);
        if (match) {
          targetDate = new Date(Number(match[1]));
        } else {
          targetDate = new Date(timestamp);
        }
      }
      
      if (!targetDate || isNaN(targetDate.getTime())) {
        return '';
      }
      
      const now = new Date();
      const diffMs = targetDate - now;
      
      if (diffMs < 0) return '(Ended)';
      
      const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
      const hours = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
      const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
      
      if (days > 0) return `(${days}d ${hours}h remaining)`;
      if (hours > 0) return `(${hours}h ${minutes}m remaining)`;
      if (minutes > 0) return `(${minutes}m remaining)`;
      return '(<1m remaining)';
      
    } catch (err) {
      console.error('Error calculating time remaining:', timestamp, err);
      return '';
    }
  }

  $: if (mounted && id) {
    console.log('Loading proposal data - mounted:', mounted, 'id:', id);
    loadProposalData();
  }

  $: voteStats = {
    totalVotePower: proposalData?.tokens_total || 0,
    approvalCount: proposalData?.vote_counts?.Yay || 0,
    rejectionCount: proposalData?.vote_counts?.Nay || 0,
    abstainCount: proposalData?.vote_counts?.Abstain || 0,
    nullVoteCount: proposalData?.vote_counts?.Null || 0,
    quorum: proposalData?.quorum || 0,
  };

  $: console.log('Vote stats:', voteStats);

  $: quorumRequired = voteStats.quorum > 0 ? 
    (voteStats.totalVotePower * (voteStats.quorum / 100)) : 0;

  $: quorumProgress = quorumRequired > 0 ? 
    ((voteStats.approvalCount + voteStats.rejectionCount) / quorumRequired) * 100 : 0;

  $: approvalProgress = voteStats.totalVotePower > 0 ? 
    (voteStats.approvalCount / voteStats.totalVotePower) * 100 : 0;

  $: rejectionProgress = voteStats.totalVotePower > 0 ? 
    (voteStats.rejectionCount / voteStats.totalVotePower) * 100 : 0;

  $: quorumNeeded = quorumRequired - 
    (voteStats.approvalCount + voteStats.rejectionCount);


  async function loadProposalData() {
    if (!id) {
      console.log('Missing proposal ID');
      return;
    }
    
    try {
      console.log('Fetching proposal data for ID:', id);
      const { proposal: p, tally, eligible } = await must(read('proposal_get', { proposal: id }));
      const proposal = {
        ...p,
        author: p.proposer,
        deadline: p.deadline * 1000,
        quorum: p.quorum_percent,
        tokens_total: eligible,
        vote_counts: { Yay: tally.yes, Nay: tally.no, Abstain: tally.abstain }
      };
      console.log('Proposal data:', proposal);
      
      // Transform the proposal data with mock enhancements
      proposalData = {
        ...proposal,
        vote_counts: proposal.vote_counts || {},
        tokens_total: proposal.tokens_total || 0,
        created_formatted: formatTimestamp(proposal.timestamp_created),
        deadline_formatted: formatTimestamp(proposal.deadline),
        time_remaining: getTimeRemaining(proposal.deadline),
        source_code: proposal.extension_code?.source_code || '',
        author: proposal.author || '',
        forum_url: proposal.forum_url || '',
        discussion_url: proposal.discussion_url || '',
        ai_opinions: proposal.ai_opinions || [],
        provisional_result: calculateProvisionalResult(proposal),
        detailed_status: getDetailedStatus(proposal)
      };
      
      console.log('Processed proposal data:', proposalData);
    } catch (err) {
      error = err instanceof Error ? err.message : 'Failed to load proposal';
      console.error('Error loading proposal:', err);
      proposalData = null;
    }
  }

  onMount(() => {
    mounted = true;
    console.log('onMount:id =', id);
  });
</script>

<svelte:head>
  <title>Proposal Details | Dashboard</title>
</svelte:head>

{#if error}
  <div class="p-4 mb-4 text-sm text-red-800 rounded-lg bg-red-50 dark:bg-gray-800 dark:text-red-400" role="alert">
    <span class="font-medium">Error!</span> {error}
  </div>
{:else if proposalData}
  <div class="p-4">
    <Card size="xl" padding="xl" class="max-w-none">
      <div class="flex justify-between items-start mb-6">
        <div>
          <Heading tag="h3" class="text-2xl font-bold dark:text-white mb-2">
            {proposalData.title}
          </Heading>
          <div class="mt-4 space-y-2">
            {#if proposalData?.created_formatted}
              <p class="text-gray-600 dark:text-gray-400">
                Created: {proposalData.created_formatted}
              </p>
            {/if}
            {#if proposalData?.deadline_formatted}
              <p class="text-gray-600 dark:text-gray-400">
                Deadline: {proposalData.deadline_formatted} 
                <span class="text-sm ml-2 {proposalData.status === 'Approved' ? 'text-green-600' : 'text-gray-500'}">
                  {proposalData.time_remaining}
                </span>
              </p>
            {/if}
          </div>
        </div>
        <Badge
          color={proposalData.status === 'Approved' ? 'green' :
            proposalData.status === 'Voting' ? 'yellow' :
            proposalData.status === 'Rejected' ? 'red' :
            'gray'}
          class="text-xs px-2.5 py-0.5"
        >
          {proposalData.status}
        </Badge>
      </div>

      <!-- Author and Forum Information -->
      <div class="mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <h4 class="text-sm font-semibold text-gray-600 dark:text-gray-400 mb-1">Author</h4>
          <p class="text-gray-900 dark:text-gray-100">{proposalData.author}</p>
        </div>
        <div>
          <h4 class="text-sm font-semibold text-gray-600 dark:text-gray-400 mb-1">Forum Discussion</h4>
          <a href={proposalData.forum_url} target="_blank" rel="noopener noreferrer" 
             class="text-blue-600 dark:text-blue-400 hover:underline">
            View Discussion →
          </a>
        </div>
        <div>
          <h4 class="text-sm font-semibold text-gray-600 dark:text-gray-400 mb-1">Community Forum</h4>
          <a href={proposalData.discussion_url} target="_blank" rel="noopener noreferrer" 
             class="text-blue-600 dark:text-blue-400 hover:underline">
            Join Discourse →
          </a>
        </div>
      </div>

      {#if proposalData.description}
        <div class="mb-6">
          <h4 class="text-lg font-semibold mb-2">Description</h4>
          <p class="text-gray-700 dark:text-gray-300">
            {proposalData.description}
          </p>
        </div>
      {/if}

      <!-- Codex Section -->
      {#if proposalData?.source_code}
        <div class="mt-6">
          <h3 class="text-lg font-semibold mb-2">Codex (Python Implementation)</h3>
          <p class="text-sm text-gray-600 dark:text-gray-400 mb-3">
            Executable governance logic using the GGG (Generalized Global Governance) module
          </p>
          <div class="bg-gray-800 rounded-lg p-4 overflow-x-auto">
            <pre class="text-gray-100 font-mono text-sm whitespace-pre-wrap">{proposalData.source_code}</pre>
          </div>
        </div>
      {/if}

      <!-- AI Assistant Opinions -->
      {#if proposalData?.ai_opinions}
        <div class="mb-8">
          <h3 class="text-lg font-semibold mb-4">AI Assistant Analysis</h3>
          <div class="space-y-4">
            {#each proposalData.ai_opinions as opinion}
              <div class="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center space-x-2">
                    <h4 class="font-semibold text-gray-900 dark:text-gray-100">{opinion.assistant}</h4>
                    <Badge 
                      color={opinion.sentiment === 'positive' ? 'green' : 
                             opinion.sentiment === 'negative' ? 'red' : 'yellow'}
                      class="text-xs"
                    >
                      {opinion.sentiment}
                    </Badge>
                  </div>
                  <span class="text-sm text-gray-500">
                    Confidence: {Math.round(opinion.confidence * 100)}%
                  </span>
                </div>
                <p class="text-gray-700 dark:text-gray-300 mb-3">{opinion.opinion}</p>
                {#if opinion.key_points}
                  <div class="mt-2">
                    <h5 class="text-sm font-medium text-gray-600 dark:text-gray-400 mb-1">Key Points:</h5>
                    <ul class="text-sm text-gray-600 dark:text-gray-400 list-disc list-inside space-y-1">
                      {#each opinion.key_points as point}
                        <li>{point}</li>
                      {/each}
                    </ul>
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        </div>
      {/if}

      <!-- Voting Progress Section -->
      {#if voteStats}
        <div class="mb-6">
          <h3 class="text-lg font-semibold mb-4">Voting Progress</h3>

          <!-- Approve Progress Bar -->
          <div class="mb-4">
            <div class="flex justify-between mb-1">
              <span class="text-base font-medium text-green-700 dark:text-green-400">
                Approve ({voteStats.approvalCount} votes)
              </span>
              <span class="text-sm font-medium text-green-700 dark:text-green-400">
                {voteStats.approvalCount} tokens ({Math.round(approvalProgress)}%)
              </span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
              <div class="bg-green-600 h-2.5 rounded-full transition-all duration-300" 
                style="width: {approvalProgress}%">
              </div>
            </div>
          </div>

          <!-- Reject Progress Bar -->
          <div class="mb-4">
            <div class="flex justify-between mb-1">
              <span class="text-base font-medium text-red-700 dark:text-red-400">
                Reject ({voteStats.rejectionCount} votes)
              </span>
              <span class="text-sm font-medium text-red-700 dark:text-red-400">
                {voteStats.rejectionCount} tokens ({Math.round(rejectionProgress)}%)
              </span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
              <div class="bg-red-600 h-2.5 rounded-full transition-all duration-300" 
                style="width: {rejectionProgress}%">
              </div>
            </div>
          </div>

          <!-- Abstain Progress Bar -->
          <div class="mb-4">
            <div class="flex justify-between mb-1">
              <span class="text-base font-medium text-yellow-700 dark:text-yellow-400">
                Abstain ({voteStats.abstainCount} votes)
              </span>
              <span class="text-sm font-medium text-yellow-700 dark:text-yellow-400">
                {voteStats.abstainCount} tokens ({Math.round((voteStats.abstainCount / Math.max(voteStats.totalVotePower, 1)) * 100)}%)
              </span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
              <div class="bg-yellow-600 h-2.5 rounded-full transition-all duration-300" 
                style="width: {(voteStats.abstainCount / Math.max(voteStats.totalVotePower, 1)) * 100}%">
              </div>
            </div>
          </div>

          <!-- Null Vote Progress Bar -->
          <div class="mb-4">
            <div class="flex justify-between mb-1">
              <span class="text-base font-medium text-gray-700 dark:text-gray-400">
                Null Vote ({voteStats.nullVoteCount} votes)
              </span>
              <span class="text-sm font-medium text-gray-700 dark:text-gray-400">
                {voteStats.nullVoteCount} tokens ({Math.round((voteStats.nullVoteCount / Math.max(voteStats.totalVotePower, 1)) * 100)}%)
              </span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
              <div class="bg-gray-600 h-2.5 rounded-full transition-all duration-300" 
                style="width: {(voteStats.nullVoteCount / Math.max(voteStats.totalVotePower, 1)) * 100}%">
              </div>
            </div>
          </div>

          <!-- Quorum Progress -->
          <div class="mt-6">
            <div class="flex justify-between mb-1">
              <span class="text-base font-medium text-blue-700 dark:text-blue-400">
                Quorum Progress
              </span>
              <span class="text-sm font-medium text-blue-700 dark:text-blue-400">
                {voteStats.approvalCount + voteStats.rejectionCount} / {Math.ceil(quorumRequired)} tokens needed
              </span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700">
              <div class="bg-blue-600 h-2.5 rounded-full transition-all duration-300" 
                style="width: {Math.min(quorumProgress, 100)}%">
              </div>
            </div>
            {#if quorumNeeded > 0}
              <p class="text-sm text-gray-600 mt-1">
                {Math.ceil(quorumNeeded)} more tokens needed for quorum
              </p>
            {:else if proposalData.status !== 'Approved'}
              <p class="text-sm text-green-600 mt-1">
                Quorum reached!
              </p>
            {/if}
          </div>
        </div>
      {/if}

      <!-- Provisional/Final Result -->
      {#if proposalData?.provisional_result}
        <div class="mb-6">
          <h3 class="text-lg font-semibold mb-2">Current Result</h3>
          <div class="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
            <p class="text-blue-800 dark:text-blue-200 font-medium">
              {proposalData.provisional_result}
            </p>
            <p class="text-sm text-blue-600 dark:text-blue-300 mt-1">
              {proposalData.status === 'Voting' ? 'Provisional result - subject to change' : 'Final result'}
            </p>
          </div>
        </div>
      {/if}

      <!-- Detailed Status -->
      {#if proposalData?.detailed_status}
        <div class="mb-6">
          <h3 class="text-lg font-semibold mb-2">Status Details</h3>
          <div class="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <h4 class="font-semibold text-gray-900 dark:text-gray-100">
                {proposalData.detailed_status.phase}
              </h4>
              <Badge 
                color={proposalData.status === 'Approved' ? 'green' :
                       proposalData.status === 'Voting' ? 'yellow' :
                       proposalData.status === 'Rejected' ? 'red' :
                       proposalData.status === 'Executed' ? 'blue' : 'gray'}
                class="text-xs"
              >
                {proposalData.status}
              </Badge>
            </div>
            <p class="text-gray-700 dark:text-gray-300 mb-2">
              {proposalData.detailed_status.description}
            </p>
            <p class="text-sm text-gray-600 dark:text-gray-400">
              <strong>Next Action:</strong> {proposalData.detailed_status.next_action}
            </p>
          </div>
        </div>
      {/if}

      <div class="mt-4">
        <Button href="/organization?id={organizationId}" color="light">
          Back to Organization
        </Button>
      </div>
    </Card>
  </div>
{:else}
  <Card>
    <div class="h-32 animate-pulse">
      <div class="h-4 w-3/4 rounded bg-gray-200 dark:bg-gray-700"></div>
      <div class="mt-4 h-4 w-1/2 rounded bg-gray-200 dark:bg-gray-700"></div>
      <div class="mt-6 h-4 w-1/4 rounded bg-gray-200 dark:bg-gray-700"></div>
    </div>
  </Card>
{/if}
