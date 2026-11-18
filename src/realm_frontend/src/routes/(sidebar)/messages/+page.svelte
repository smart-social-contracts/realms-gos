<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { Heading, Spinner } from 'flowbite-svelte';
	import { _ } from 'svelte-i18n';
	import { realmName } from '$lib/stores/realmInfo';
	import { pageTitle } from '$lib/utils/page-title';
	import {
		notifications,
		unreadCount,
		loadNotifications,
		markAsRead,
		markAllAsRead,
	} from '$lib/stores/notifications';
	import { IconMail, IconMailOpened, IconInbox } from '@tabler/icons-svelte';
	import {
		actionLabel,
		formatFullDate,
		formatNoticeMeta,
		formatRelativeTime,
		isActionHref,
	} from '$lib/utils/messages';

	let loading = $state(true);
	let activeTab = $state<'all' | 'unread'>('all');
	let expandedId = $state<string | null>(null);
	let markingAll = $state(false);

	onMount(async () => {
		await loadNotifications();
		loading = false;
	});

	const displayedMessages = $derived(
		activeTab === 'unread' ? $notifications.filter((n) => !n.read) : $notifications,
	);

	async function handleOpen(id: string, read: boolean) {
		const next = expandedId === id ? null : id;
		expandedId = next;
		if (next && !read) {
			await markAsRead(id, true);
		}
	}

	async function handleToggleRead(event: MouseEvent, id: string, read: boolean) {
		event.stopPropagation();
		await markAsRead(id, !read);
	}

	async function handleOpenAction(event: MouseEvent, href: string) {
		event.stopPropagation();
		await goto(href);
	}

	async function handleMarkAll() {
		markingAll = true;
		try {
			await markAllAsRead();
		} finally {
			markingAll = false;
		}
	}
</script>

<svelte:head>
	<title>{pageTitle($_('messages.page_title'), $realmName)}</title>
</svelte:head>

<div class="mt-4 px-4 md:px-6">
	<div class="mb-6 flex flex-wrap items-start justify-between gap-3">
		<div>
			<Heading tag="h1" class="text-2xl font-bold text-gray-900">{$_('messages.page_title')}</Heading>
			<p class="mt-1 text-sm text-gray-500">
				{$_('messages.subtitle')}
			</p>
		</div>
		{#if $unreadCount > 0}
			<button
				type="button"
				class="inline-flex items-center rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
				disabled={markingAll}
				onclick={handleMarkAll}
			>
				{$_('messages.mark_all_read')}
			</button>
		{/if}
	</div>

	<div class="mb-4 flex gap-4 border-b border-gray-200">
		<button
			type="button"
			class="pb-2 text-sm font-medium transition-colors {activeTab === 'all'
				? 'border-b-2 border-gray-900 text-gray-900'
				: 'text-gray-500 hover:text-gray-700'}"
			onclick={() => (activeTab = 'all')}
		>
			{$_('messages.tab_all', { values: { count: $notifications.length } })}
		</button>
		<button
			type="button"
			class="pb-2 text-sm font-medium transition-colors {activeTab === 'unread'
				? 'border-b-2 border-gray-900 text-gray-900'
				: 'text-gray-500 hover:text-gray-700'}"
			onclick={() => (activeTab = 'unread')}
		>
			{$_('messages.tab_unread', { values: { count: $unreadCount } })}
		</button>
	</div>

	{#if loading}
		<div class="flex items-center justify-center py-16">
			<Spinner size="6" />
		</div>
	{:else if displayedMessages.length === 0}
		<div class="flex flex-col items-center justify-center py-16 text-gray-500">
			<IconInbox size={48} class="mb-4" />
			<p class="text-lg font-medium text-gray-500">
				{activeTab === 'unread'
					? $_('messages.empty_unread_title')
					: $_('messages.empty_all_title')}
			</p>
			<p class="mt-1 text-sm text-gray-500">
				{activeTab === 'unread'
					? $_('messages.empty_unread_body')
					: $_('messages.empty_all_body')}
			</p>
		</div>
	{:else}
		<div class="mx-auto max-w-3xl space-y-2">
			{#each displayedMessages as message (message.id)}
				{@const open = expandedId === message.id}
				<article
					class="rounded-lg border transition-colors {message.read
						? 'border-gray-100 bg-white'
						: 'border-gray-200 border-l-4 border-l-gray-900 bg-gray-50'}"
				>
					<button
						type="button"
						class="w-full p-4 text-left hover:bg-gray-50"
						aria-expanded={open}
						onclick={() => handleOpen(message.id, message.read)}
					>
						<div class="flex items-start gap-3">
							<div class="mt-0.5 flex-shrink-0">
								{#if message.read}
									<IconMailOpened size={20} class="text-gray-500" />
								{:else}
									<IconMail size={20} class="text-gray-700" />
								{/if}
							</div>
							<div class="min-w-0 flex-1">
								<div class="flex items-center justify-between gap-2">
									<h4
										class="truncate text-sm {message.read
											? 'font-normal text-gray-600'
											: 'font-semibold text-gray-900'}"
									>
										{message.title}
									</h4>
									{#if message.timestamp_ms}
										<span
											class="flex-shrink-0 text-xs text-gray-500"
											title={formatFullDate(message.timestamp_ms)}
										>
											{formatRelativeTime(message.timestamp_ms)}
										</span>
									{/if}
								</div>
								<p class="mt-0.5 text-xs text-gray-500">{formatNoticeMeta(message)}</p>
								{#if !open}
									<p class="mt-1 line-clamp-2 text-sm text-gray-500">{message.message}</p>
								{/if}
							</div>
							<svg
								class="h-5 w-5 flex-shrink-0 text-gray-500 transition-transform {open
									? 'rotate-180'
									: ''}"
								fill="none"
								stroke="currentColor"
								viewBox="0 0 24 24"
								aria-hidden="true"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									stroke-width="2"
									d="M19 9l-7 7-7-7"
								/>
							</svg>
						</div>
					</button>

					{#if open}
						<div class="px-4 pb-4 pl-[3.25rem]">
							{#if message.message}
								<p class="whitespace-pre-wrap text-sm text-gray-700">{message.message}</p>
							{/if}
							{#if message.timestamp_ms}
								<p class="mt-2 text-xs text-gray-500">{formatFullDate(message.timestamp_ms)}</p>
							{/if}
							<div class="mt-3 flex flex-wrap items-center gap-3 border-t border-gray-100 pt-3 {isActionHref(message.href) ? 'justify-between' : 'justify-end'}">
								{#if isActionHref(message.href)}
									<button
										type="button"
										class="text-sm font-medium text-gray-900 underline-offset-2 hover:underline"
										onclick={(e) => handleOpenAction(e, message.href || '')}
									>
										{actionLabel(message.href || '')}
									</button>
								{/if}
								<button
									type="button"
									class="text-xs text-gray-500 hover:text-gray-800"
									onclick={(e) => handleToggleRead(e, message.id, message.read)}
								>
									{message.read ? $_('messages.mark_unread') : $_('messages.mark_read')}
								</button>
							</div>
						</div>
					{/if}
				</article>
			{/each}
		</div>
	{/if}
</div>
