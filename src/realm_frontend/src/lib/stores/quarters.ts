import { writable, derived } from 'svelte/store';
import { backendActorReady } from '$lib/canisters';
import { read } from '$lib/realm';

const STATUS_QUERY_TIMEOUT_MS = 12_000;

function withQueryTimeout<T>(
	promise: Promise<T>,
	ms: number,
	label: string
): Promise<T | null> {
	return new Promise((resolve) => {
		let settled = false;
		const timer = setTimeout(() => {
			if (!settled) {
				console.warn(`[quarters] ${label} timed out after ${ms}ms`);
				settled = true;
				resolve(null);
			}
		}, ms);
		promise
			.then((value) => {
				if (!settled) {
					settled = true;
					clearTimeout(timer);
					resolve(value);
				}
			})
			.catch((err) => {
				if (!settled) {
					settled = true;
					clearTimeout(timer);
					console.warn(`[quarters] ${label} failed:`, err);
					resolve(null);
				}
			});
	});
}

export interface QuarterInfo {
	name: string;
	canister_id: string;
	population: number;
	status: string;
}

/**
 * The federation as ``read('quarter_directory')`` reports it. Its first row is this canister; the
 * list is empty for a realm on its own.
 */
export function quarterFederation(reply: Record<string, any>) {
	const rows: Record<string, any>[] = reply.quarters;
	const isQuarter = rows[0].id !== reply.capital;
	return {
		quarters: (rows.length > 1 ? rows : []).map((q) => ({
			name: q.name,
			canister_id: q.id,
			population: q.population,
			status: q.status,
			index: q.index,
			is_capital: q.capital
		})),
		isQuarter,
		parentRealmCanisterId: isQuarter ? reply.capital : ''
	};
}

export interface QuartersState {
	quarters: QuarterInfo[];
	isQuarter: boolean;
	isCapital: boolean;
	parentRealmCanisterId: string;
	loading: boolean;
	error: string | null;
}

const createQuartersStore = () => {
	const { subscribe, set, update } = writable<QuartersState>({
		quarters: [],
		isQuarter: false,
		isCapital: false,
		parentRealmCanisterId: '',
		loading: true,
		error: null
	});

	return {
		subscribe,
		fetch: async () => {
			update((state: QuartersState) => ({ ...state, loading: true, error: null }));

		try {
			// Await the provisional actor, not backendReady — see realmInfo.ts
			// (backendReady includes auth init, which awaits these fetches).
			await backendActorReady;
				const reply = await withQueryTimeout(
					read('quarter_directory'),
					STATUS_QUERY_TIMEOUT_MS,
					'quarter_directory'
				);
				if (!reply?.ok) {
					throw new Error('Failed to fetch quarter info');
				}
				const federation = quarterFederation(reply);
				update((state: QuartersState) => ({
					...state,
					...federation,
					isCapital: !federation.isQuarter && federation.quarters.length > 0,
					loading: false
				}));
			} catch (error) {
				console.error('Error fetching quarters:', error);
				update((state: QuartersState) => ({
					...state,
					loading: false,
					error: error instanceof Error ? error.message : 'Unknown error'
				}));
			}
		}
	};
};

export const quartersStore = createQuartersStore();

// Active quarter canister ID (null = use parent backend directly)
export const activeQuarterId = writable<string | null>(null);

// Derived: list of quarters only
export const quartersList = derived(quartersStore, ($s: QuartersState) => $s.quarters);

// Derived: whether this realm has quarters
export const hasQuarters = derived(quartersStore, ($s: QuartersState) => $s.quarters.length > 0);

// Derived: whether this backend is itself a quarter
export const isQuarter = derived(quartersStore, ($s: QuartersState) => $s.isQuarter);

// Derived: whether this backend is the capital quarter of a federation
export const isCapital = derived(quartersStore, ($s: QuartersState) => $s.isCapital);
