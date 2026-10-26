<script lang="ts">
	import { Button, Card, Chart } from 'flowbite-svelte';
	import { ChevronRightOutline } from 'flowbite-svelte-icons';
	import { onMount } from 'svelte';
	import { must, read } from '$lib/realm';
	import { formatUSD, formatNumber } from '$lib/utils';
	import { page } from '$app/stores';
	import { ArrowUpRightFromSquareOutline } from 'flowbite-svelte-icons';

	export let dark: boolean = false;
	export let organizationId: string;
	
	let tokenData = {
		series: [],
		labels: [],
		colors: [],
		balances: [], // Store balances for tooltips
	};

	let organizationData = null;

	// Token prices would come from an oracle or price feed
	const tokenPrices: Record<string, number> = {
		'USDC': 1,
		'DAI': 1,
		'BNB': 320,
		'XRP': 0.55,
		'SOL': 98,
		'DOT': 6.8,
		'MATIC': 0.85,
		'GGG': 10,
		'LAND': 5
	};

	// Default price for tokens not in the mapping
	const DEFAULT_PRICE = 1;

	const chartColors = [
		'#16BDCA', '#FDBA8C', '#1A56DB', '#7E3AF2', '#FF5A1F', 
		'#319795', '#9061F9', '#E74694', '#1C64F2', '#047481'
	];

	const chartOptions = (dark: boolean) => ({
		chart: {
			type: 'donut',
			height: 400,
			fontFamily: 'Inter, sans-serif',
			toolbar: { show: false }
		},
		legend: {
			show: true,
			position: 'right',
			fontSize: '14px',
			labels: {
				colors: dark ? '#9ca3af' : '#4b5563'
			},
			formatter: (seriesName, opts) => {
				const index = opts.seriesIndex;
				return `${seriesName} (${formatUSD(tokenData.series[index])})`;
			}
		},
		labels: tokenData.labels,
		colors: tokenData.colors,
		series: tokenData.series,
		stroke: {
			colors: [dark ? '#1f2937' : '#ffffff']
		},
		plotOptions: {
			pie: {
				donut: {
					size: '70%',
					labels: {
						show: true,
						total: {
							show: true,
							label: 'Total Value',
							formatter: (w) => formatUSD(
								w.globals.series.reduce((a, b) => a + b, 0)
							)
						}
					}
				}
			}
		},
		tooltip: {
			y: {
				formatter: (value) => formatUSD(value)
			}
		},
		dataLabels: {
			enabled: window?.innerWidth > 640, // Only show labels on screens larger than 640px
			formatter: (value, { seriesIndex }) => tokenData.labels[seriesIndex]
		},
		responsive: [{
			breakpoint: 640,
			options: {
				chart: {
					height: 300
				},
				legend: {
					position: 'bottom',
					horizontalAlign: 'center',
					itemMargin: {
						horizontal: 8,
						vertical: 4
					}
				},
				plotOptions: {
					pie: {
						donut: {
							size: '85%' // Larger hole on small screens
						}
					}
				}
			}
		}]
	});

	async function loadTokenData() {
		try {
			console.log('Loading data for organization:', organizationId);
			const orgData = (await must(read('organization_get', { organization: organizationId }))).organization;
			console.log('Organization data:', orgData);

			organizationData = orgData;

			const walletTokens = orgData.wallet?.tokens || orgData.tokens || {};
			console.log('Wallet tokens:', walletTokens);

			if (Object.keys(walletTokens).length === 0) {
				console.log('No tokens found in wallet');
				return;
			}

			// Process token data from wallet
			const tokenValues = Object.entries(walletTokens)
				.map(([symbol, tokenInfo]: [string, any]) => {
					console.log(`Processing token ${symbol}:`, tokenInfo);

					// Sum up balances across all addresses
					const totalBalance = tokenInfo.addresses?.reduce(
						(sum: number, addr: any) => sum + (addr.balance || 0),
						0
					) || tokenInfo.balance || 0;

					console.log(`Total balance for ${symbol}:`, totalBalance);

					// Use price from mapping, or default price if not found
					const price = tokenPrices[symbol] || DEFAULT_PRICE;
					const value = totalBalance * price;

					console.log(`Calculated value for ${symbol}: ${value} (price: ${price})`);

					return {
						symbol,
						balance: totalBalance,
						value: value
					};
				})
				.filter(token => token.value > 0)
				.sort((a, b) => b.value - a.value);

			console.log('Processed token values:', tokenValues);

			if (tokenValues.length === 0) {
				console.log('No tokens with value > 0 found');
				return;
			}

			// Update chart data
			tokenData.labels = tokenValues.map(t => t.symbol);
			tokenData.series = tokenValues.map(t => t.value);
			tokenData.colors = chartColors.slice(0, tokenValues.length);
			tokenData.balances = tokenValues.map(t => t.balance);
			
			// Force reactivity
			tokenData = tokenData;
			console.log('Updated chart data:', tokenData);
		} catch (error) {
			console.error('Failed to load token data:', error);
		}
	}

	onMount(() => {
		if (organizationId) {
			loadTokenData();
		}
	});

	// Watch for changes in organizationId
	$: if (organizationId) {
		loadTokenData();
	}
</script>

<Card size="xl" class="h-fit">
	<div class="mb-4 items-center justify-between border-b border-gray-200 pb-4 sm:flex dark:border-gray-700">
		<div class="mb-4 w-full sm:mb-0">
			<h3 class="text-base font-normal text-gray-500 dark:text-gray-400">Token Holdings</h3>
			<span class="text-2xl font-bold leading-none text-gray-900 sm:text-3xl dark:text-white">
				Portfolio Distribution
			</span>
		</div>
		<Button size="sm" class="px-3" color="alternative" on:click={loadTokenData}>
			Refresh
		</Button>
	</div>
	
	{#if tokenData.series.length > 0}
		<div class="space-y-6">
			<Chart options={chartOptions(dark)} />
			
			<div class="border-t pt-6">
				<h4 class="text-base font-medium text-gray-900 dark:text-white mb-4">Wallet Addresses</h4>
				<div class="space-y-6">
					{#each tokenData.labels as symbol, i}
						{#if organizationData.wallet?.tokens[symbol]?.addresses?.length > 0}
							<div class="space-y-3">
								<div class="flex items-center gap-2">
									<div class="h-2 w-2 rounded-full" style="background-color: {tokenData.colors[i]}"></div>
									<h5 class="text-sm font-medium text-gray-900 dark:text-white">{symbol}</h5>
									<span class="text-xs px-2 py-1 rounded-full bg-blue-100 text-blue-800">
										{organizationData.wallet.tokens[symbol].type}
									</span>
								</div>
								{#each organizationData.wallet.tokens[symbol].addresses as address}
									<div class="pl-4 space-y-2">
										<div class="flex items-center justify-between">
											<code class="text-sm text-gray-600 dark:text-gray-400 break-all">
												{address.address}
											</code>
											<div class="flex items-center gap-2">
												<span class="text-sm text-gray-600">
													Balance: {formatNumber(address.balance)}
												</span>
												<button 
													class="text-gray-500 hover:text-gray-700" 
													title="Copy address"
													on:click={() => {
														navigator.clipboard.writeText(address.address);
													}}
												>
													<ArrowUpRightFromSquareOutline size="sm" />
												</button>
											</div>
										</div>
										{#if address.land}
											<div class="bg-gray-50 dark:bg-gray-800 rounded p-2 text-sm">
												<p class="text-gray-700 dark:text-gray-300">
													Surface Area: {address.land.surface_area} units
												</p>
												{#if address.land.description}
													<p class="text-gray-600 dark:text-gray-400 mt-1">
														{address.land.description}
													</p>
												{/if}
											</div>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					{/each}
				</div>
			</div>
		</div>
	{:else}
		<div class="flex justify-center items-center h-[400px] text-gray-500">
			No tokens found in wallet
		</div>
	{/if}

	<a
		href="/wallet"
		class="text-primary-700 dark:text-primary-500 inline-flex items-center rounded-lg p-1 text-xs font-medium uppercase hover:bg-gray-100 sm:text-sm dark:hover:bg-gray-700"
	>
		View all tokens <ChevronRightOutline size="lg" />
	</a>
</Card>
