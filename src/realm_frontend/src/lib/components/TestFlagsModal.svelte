<script>
	import { Button, Modal, Toggle, Alert } from 'flowbite-svelte';
	import { backend } from '$lib/canisters';
	import { realmInfo, testMode } from '$lib/stores/realmInfo';
	import {
		NOTICE_LOCALE_SLOTS,
		filledNoticeTranslationCount,
		noticeTranslationSlots
	} from '$lib/config/hostTestFlags';
	import { localeLabel } from '$lib/i18n/realmLocales';

	export let open = false;

	const FLAGS = [
		{ key: 'test_mode', store: 'testMode', label: 'Test mode', hint: 'Master switch — turning this off hides this editor and locks flags to admins' },
		{ key: 'ii_bypass', store: 'testModeIIBypass', label: 'II bypass', hint: 'Skip Internet Identity and show the deterministic test identity picker' },
		{ key: 'user_self_registration', store: 'testModeUserSelfRegistration', label: 'User self-registration', hint: 'Allow users to join without an invitation code' },
		{ key: 'demo_data', store: 'testModeDemoData', label: 'Demo data', hint: 'Auto-activate the demo data simulator' },
		{ key: 'skip_terms', store: 'testModeSkipTerms', label: 'Skip terms', hint: 'Skip the demo notice step on join' },
		{ key: 'skip_passport_zkproof', store: 'testModeSkipPassportZkproof', label: 'Skip passport ZK-proof', hint: 'Bypass passport zero-knowledge verification' },
		{ key: 'disable_monetary_tokens', store: 'testModeDisableMonetaryTokens', label: 'Disable monetary tokens', hint: 'Gray out ckBTC, ckUSDC, ckEURC, and Custom. REALMS stays selectable. Does not strip an already-configured ledger.' },
		{ key: 'demo_notice', store: 'testModeDemoNotice', label: 'Demo notice', hint: 'Show the configurable demo notice on founder setup and join' }
	];

	// set_test_flags_json rejects product keys; persist only test-only flags.
	const TEST_ONLY_KEYS = [
		'test_mode',
		'ii_bypass',
		'demo_data',
		'skip_terms',
		'skip_passport_zkproof'
	];

	const TRANSLATION_SLOTS = noticeTranslationSlots();

	let values = {};
	let noticeBodies = {};
	let saving = false;
	let error = '';
	let message = '';
	let noticeOpen = false;
	let translationsOpen = false;

	// Snapshot current flags each time the modal opens
	$: if (open) syncFromStore();

	$: filledTranslations = filledNoticeTranslationCount(noticeBodies);

	function syncFromStore() {
		const info = $realmInfo;
		const next = {};
		for (const f of FLAGS) next[f.key] = !!info[f.store];
		values = next;
		const stored = info.demoNoticeBody || {};
		const bodies = {};
		for (const loc of NOTICE_LOCALE_SLOTS) bodies[loc] = stored[loc] || '';
		noticeBodies = bodies;
		error = '';
		message = '';
		noticeOpen = false;
		translationsOpen = false;
	}

	function testOnlyFlags() {
		const flags = {};
		for (const key of TEST_ONLY_KEYS) flags[key] = !!values[key];
		return flags;
	}

	async function save() {
		saving = true;
		error = '';
		message = '';
		try {
			const raw = await backend.set_test_flags_json(
				JSON.stringify({
					test_flags: testOnlyFlags()
				})
			);
			const result = typeof raw === 'string' ? JSON.parse(raw) : raw;
			if (!result?.success) {
				throw new Error(result?.error || 'Failed to update test flags');
			}
			await realmInfo.fetch({ force: true });
			message = 'Test flags updated';
			if (!values.test_mode) open = false;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

{#if $testMode}
	<Modal
		bind:open
		title="Test flags"
		size="md"
		autoclose={false}
		outsideclose
		class="max-h-[min(90dvh,40rem)] overflow-hidden flex flex-col"
		classBody="min-h-0 overflow-y-auto"
		classHeader="shrink-0"
		classFooter="shrink-0"
	>
		<p class="text-sm text-gray-500 dark:text-gray-400">
			Anyone can change these flags while test mode is on.
		</p>
		<Alert color="red" class="border border-red-300 py-2 text-sm">
			<span class="font-semibold">WARNING:</span> turning off "Test mode" hides this editor and
			locks further flag changes to canister admins.
		</Alert>
		<div class="space-y-2">
			{#each FLAGS as flag (flag.key)}
				<div
					class="flex items-center justify-between gap-3"
					title={flag.hint}
					aria-label="{flag.label}. {flag.hint}"
				>
					<p class="text-sm font-medium text-gray-900 dark:text-white">{flag.label}</p>
					<Toggle color="blue" bind:checked={values[flag.key]} disabled={saving} />
				</div>
			{/each}
		</div>
		<div class="border-t border-gray-200 pt-2 dark:border-gray-700">
			<button
				type="button"
				class="flex w-full items-center justify-between gap-3 text-left"
				aria-expanded={noticeOpen}
				on:click={() => (noticeOpen = !noticeOpen)}
			>
				<span>
					<span class="text-sm font-medium text-gray-900 dark:text-white">Demo notice body</span>
					{#if filledTranslations}
						<span class="text-xs text-gray-500 dark:text-gray-400">
							· {filledTranslations} translation{filledTranslations === 1 ? '' : 's'}
						</span>
					{/if}
				</span>
				<span class="text-xs text-gray-500 dark:text-gray-400">{noticeOpen ? 'Hide' : 'Edit'}</span>
			</button>
			{#if noticeOpen}
				<p class="mt-2 text-xs text-gray-500 dark:text-gray-400">
					English is seeded from Legal. Other locale slots stay empty until Legal/Story writes them.
				</p>
				<label class="mt-2 block">
					<span class="text-xs font-medium text-gray-600 dark:text-gray-300">
						{localeLabel('en')} (seeded default)
					</span>
					<textarea
						class="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs text-gray-900"
						rows="5"
						bind:value={noticeBodies.en}
						disabled={saving}
					></textarea>
				</label>
				<button
					type="button"
					class="mt-2 flex w-full items-center justify-between gap-3 text-left"
					aria-expanded={translationsOpen}
					on:click={() => (translationsOpen = !translationsOpen)}
				>
					<span class="text-xs font-medium text-gray-600 dark:text-gray-300">
						Translations ({filledTranslations} of {TRANSLATION_SLOTS.length} filled)
					</span>
					<span class="text-xs text-gray-500 dark:text-gray-400">
						{translationsOpen ? 'Hide' : 'Show'}
					</span>
				</button>
				{#if translationsOpen}
					<div class="mt-2 space-y-2">
						{#each TRANSLATION_SLOTS as loc (loc)}
							<label class="block">
								<span class="text-xs font-medium text-gray-600 dark:text-gray-300">
									{localeLabel(loc)} (slot)
								</span>
								<textarea
									class="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs text-gray-900"
									rows="3"
									bind:value={noticeBodies[loc]}
									disabled={saving}
									placeholder="Leave empty until Legal writes this locale"
								></textarea>
							</label>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
		{#if error}
			<Alert color="red" class="mt-2">{error}</Alert>
		{/if}
		{#if message}
			<Alert color="green" class="mt-2">{message}</Alert>
		{/if}
		<svelte:fragment slot="footer">
			<!-- Explicit colors: the flowbite primary palette maps to --color-primary-*
			     CSS vars that are unset on realms without branding. -->
			<Button
				color="none"
				class="bg-gray-900 text-white hover:bg-gray-700 dark:bg-gray-100 dark:text-gray-900"
				on:click={save}
				disabled={saving}
			>
				{saving ? 'Saving…' : 'Save'}
			</Button>
			<Button color="alternative" on:click={() => (open = false)} disabled={saving}>Close</Button>
		</svelte:fragment>
	</Modal>
{/if}
