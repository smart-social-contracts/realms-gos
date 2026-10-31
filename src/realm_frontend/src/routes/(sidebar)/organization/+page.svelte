<script lang="ts">
	import { onMount } from 'svelte';
	import {
		Table,
		TableBody,
		TableBodyCell,
		TableBodyRow,
		TableHead,
		TableHeadCell,
		Button,
		Card,
		Heading,
		Input,
		Checkbox,
		Dropdown,
		Tooltip
	} from 'flowbite-svelte';
	import {
		ArrowUpRightFromSquareOutline,
		CalendarMonthOutline,
		ChevronDownOutline
	} from 'flowbite-svelte-icons';
	import { must, read } from '$lib/realm';
	import Traffic from '../../utils/dashboard/Traffic.svelte';
	import Map from '../../utils/widgets/Map.svelte';
	import CodeBlock from '../../utils/dashboard/CodeBlock.svelte';

	let id;
	let organizationData = null;
	let table_data = [];
	let error = '';
	let loading = true;
	let geometry = null;

	const headers = ['Title', 'Code', 'Deadline', 'Status'];

	function parseTimestamp(timestamp) {
		if (!timestamp) return null;
		// Handle timestamp format: "2025-01-30 11:57:29.059 (1738234649059)"
		const match = timestamp.match(/\((\d+)\)$/);
		if (match) {
			return parseInt(match[1]);
		}
		return null;
	}

	function formatRelativeTime(timestamp) {
		if (!timestamp) return 'No deadline';
		
		const parsedTimestamp = parseTimestamp(timestamp);
		if (!parsedTimestamp) return 'Invalid date';
		
		const now = new Date();
		const target = new Date(parsedTimestamp);
		const diffMs = target - now;
		const diffSecs = Math.floor(diffMs / 1000);
		const diffMins = Math.floor(diffSecs / 60);
		const diffHours = Math.floor(diffMins / 60);
		const diffDays = Math.floor(diffHours / 24);
		
		// Past
		if (diffMs < 0) {
			const absDiffDays = Math.abs(diffDays);
			const absDiffHours = Math.abs(diffHours);
			const absDiffMins = Math.abs(diffMins);
			
			if (absDiffMins < 1) return 'just now';
			if (absDiffMins < 60) return `${absDiffMins} minutes ago`;
			if (absDiffHours < 24) return `${absDiffHours} hours ago`;
			if (absDiffDays < 7) return `${absDiffDays} days ago`;
			if (absDiffDays < 30) return `${Math.floor(absDiffDays / 7)} weeks ago`;
			if (absDiffDays < 365) return `${Math.floor(absDiffDays / 30)} months ago`;
			return `${Math.floor(absDiffDays / 365)} years ago`;
		}
		
		// Future
		if (diffMins < 1) return 'in a few seconds';
		if (diffMins < 60) return `in ${diffMins} minutes`;
		if (diffHours < 24) return `in ${diffHours} hours`;
		if (diffDays < 7) return `in ${diffDays} days`;
		if (diffDays < 30) return `in ${Math.floor(diffDays / 7)} weeks`;
		if (diffDays < 365) return `in ${Math.floor(diffDays / 30)} months`;
		return `in ${Math.floor(diffDays / 365)} years`;
	}

	function formatExactTime(timestamp) {
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

	function extractGeometryFromTokens(orgData) {
		console.log('=== GEOMETRY EXTRACTION START ===');
		console.log('Input orgData:', JSON.stringify(orgData, null, 2));
		
		if (!orgData?.wallet) {
			console.log('No wallet data found');
			return null;
		}

		// Look through all tokens for land data
		for (const [symbol, token] of Object.entries(orgData.wallet.tokens)) {
			console.log('\nChecking token:', symbol);
			console.log('Token data:', JSON.stringify(token, null, 2));
			
			if (token.type === 'LandToken') {
				console.log('Found LandToken');
				if (token.land?.coordinates) {
					console.log('Found coordinates:', JSON.stringify(token.land.coordinates, null, 2));
					return token.land.coordinates;
				} else {
					console.log('No coordinates in land data');
				}
			} else {
				console.log('Not a LandToken, type:', token.type);
			}
		}
		console.log('No land token with coordinates found');
		console.log('=== GEOMETRY EXTRACTION END ===');
		return null;
	}

	async function loadOrganizationData() {
		if (!id) {
			console.log('No organization ID provided');
			return;
		}
		
		try {
			console.log('=== ORGANIZATION DATA LOADING START ===');
			console.log('Fetching organization data for ID:', id);
			organizationData = (await must(read('organization_get', { organization: id }))).organization;
			console.log('Raw organization data:', JSON.stringify(organizationData, null, 2));

			// Extract geometry data from tokens
			geometry = extractGeometryFromTokens(organizationData);
			console.log('\nExtracted geometry:', JSON.stringify(geometry, null, 2));
			console.log('=== ORGANIZATION DATA LOADING END ===');

			// Update table data from proposals
			if (organizationData?.proposals) {
				console.log('Processing proposals:', organizationData.proposals);
				table_data = organizationData.proposals.map(proposal => {
					console.log('Processing proposal:', proposal);
					const relativeDeadline = formatRelativeTime(proposal.timestamp_created);
					const exactDeadline = formatExactTime(proposal.timestamp_created);
					console.log('Formatted deadlines:', { relativeDeadline, exactDeadline });
						
					return {
						title: proposal.title || 'Untitled',
						code: 'Link',  // TODO: Add actual proposal link
						deadlines: [relativeDeadline, exactDeadline], // Pass both formats
						status: proposal.status || 'Unknown',
						id: proposal.id
					};
				});
				console.log('Processed table data:', table_data);
			} else {
				console.log('No proposals found in organization data');
				table_data = [];
			}
		} catch (err) {
			console.error('Error loading organization data:', err);
			error = err.message;
		}
	}

	onMount(async () => {
		const url = new URL(window.location.href);
		id = url.searchParams.get('id');
		console.log(`onMount:id = ${id}`);
		if (id) {
			await loadOrganizationData();
		} else {
			error = 'No token ID provided';
		}
	});

	const getStatusColor = (status) => {
		switch (status) {
			case 'Approved':
				return 'bg-green-100 text-green-800';
			case 'Voting':
				return 'bg-yellow-100 text-yellow-800';
			case 'Rejected':
				return 'bg-red-100 text-red-800';
			case 'Draft':
				return 'bg-gray-100 text-gray-800';
			default:
				return '';
		}
	}
</script>

<svelte:head>
	<title>Organization | Dashboard</title>
</svelte:head>

<!-- <p>Parameter id: {id}</p> -->

<div class="mt-px space-y-4">
	{#if error}
		<div class="p-4 mb-4 text-sm text-red-800 rounded-lg bg-red-50 dark:bg-gray-800 dark:text-red-400" role="alert">
			<span class="font-medium">Error!</span> {error}
		</div>
	{/if}
	
	<pre class="text-sm text-gray-500">
		Data loaded: {!!organizationData}
		Number of proposals: {table_data.length}
	</pre>
	
	<div class="grid gap-4 xl:grid-cols-2 2xl:grid-cols-3">
		{#if organizationData}
			<Card>
				<h5 class="mb-2 text-2xl font-bold tracking-tight text-gray-900 dark:text-white">
					{organizationData.name || organizationData.id || 'Organization'}
				</h5>
				{#if organizationData.token}
					<p class="font-normal text-gray-700 dark:text-gray-400">
						Token: {organizationData.token.name}
					</p>
				{/if}
			</Card>

			<Traffic organizationId={id} />
		{:else}
			<Card>
				<div class="h-24 animate-pulse">
					<div class="h-4 w-3/4 rounded bg-gray-200 dark:bg-gray-700"></div>
					<div class="mt-4 h-4 w-1/2 rounded bg-gray-200 dark:bg-gray-700"></div>
				</div>
			</Card>
		{/if}
	</div>

	<Card size="xl" padding="xl" class="max-w-none">
		<div class="items-center justify-between lg:flex">
			<div class="mb-4 lg:mb-0">
				<Heading tag="h3" class="mb-1 text-xl font-semibold dark:text-white">
					Proposals ({table_data.length})
				</Heading>
			</div>
			<div class="items-center justify-between gap-3 sm:flex">
				<div class="flex items-center">
					<Button color="alternative">
						Filter by status
						<ChevronDownOutline size="md" />
					</Button>
					<Dropdown class="w-44 divide-y divide-gray-100 rounded-lg bg-white shadow dark:divide-gray-600 dark:bg-gray-700">
						<div class="p-3">
							<ul class="space-y-3">
								<li><Checkbox>Approved</Checkbox></li>
								<li><Checkbox>Rejected</Checkbox></li>
								<li><Checkbox>Voting</Checkbox></li>
								<li><Checkbox>Draft</Checkbox></li>
							</ul>
						</div>
					</Dropdown>
				</div>
				<div class="flex items-center space-x-4">
					<Input size="sm" type="date" />
					<Input size="sm" type="date" />
				</div>
			</div>
		</div>

		<div class="overflow-x-auto relative">
			<table class="w-full text-left">
				<thead class="text-xs uppercase bg-gray-50">
					<tr>
						<th scope="col" class="px-6 py-3 w-1/3">Title</th>
						<th scope="col" class="px-6 py-3 w-24">Code</th>
						<th scope="col" class="px-6 py-3 w-1/3">Deadline</th>
						<th scope="col" class="px-6 py-3 w-1/4">Status</th>
					</tr>
				</thead>
				<tbody>
					{#if table_data.length > 0}
						{#each table_data as row}
							<tr class="bg-white border-b hover:bg-gray-50">
								<td class="px-6 py-4">
									<a href={`/proposal/${row.id}?org=${id}`} class="font-medium text-gray-900 hover:text-blue-600">
										{row.title}
									</a>
								</td>
								<td class="px-6 py-4 text-center">
									<button class="text-gray-500 hover:text-gray-700">
										<ArrowUpRightFromSquareOutline />
									</button>
								</td>
								<td class="px-6 py-4 text-gray-600" title={row.deadlines[1]}>
									{row.deadlines[0]}
								</td>
								<td class="px-6 py-4">
									<span class="inline-flex items-center rounded-full px-2.5 py-0.5 text-sm font-medium
										{getStatusColor(row.status)}
									">
										{row.status}
									</span>
								</td>
							</tr>
						{/each}
					{:else}
						<tr>
							<td colspan="4" class="px-6 py-4 text-center text-gray-500">
								{#if organizationData}
									No proposals found
								{:else}
									<div class="flex justify-center">
										<div class="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600"></div>
									</div>
								{/if}
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</Card>

	{#if organizationData?.token}
		<Button size="sm" class="px-3" color="alternative">See {organizationData.token.name} distribution</Button>
	{:else}
		<Button size="sm" class="px-3" color="alternative">See token distribution</Button>
	{/if}

	{#if organizationData?.extension_code}
		{#if typeof organizationData.extension_code === 'string'}
			<CodeBlock
				title="Legislative program"
				language="python"
				content={organizationData.extension_code}
			/>
		{:else}
			<CodeBlock
				title="Legislative program"
				language="python"
				content={organizationData.extension_code?.source_code || '# No code available'}
			/>
		{/if}
	{:else}
		<CodeBlock
			title="Legislative program"
			language="python"
			content="# Loading..."
		/>
	{/if}

	{#if geometry}
		<div class="mt-4">
			<Card size="xl" padding="xl">
				<Heading tag="h3" class="mb-4 text-xl font-semibold dark:text-white">
					Land Distribution
				</Heading>
				<Map {geometry} />
			</Card>
		</div>
	{/if}
</div>
