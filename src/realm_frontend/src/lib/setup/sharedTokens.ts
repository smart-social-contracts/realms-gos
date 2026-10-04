/**
 * Treasury tokens a founder may adopt in the setup wizard.
 *
 * The Realms token ledger depends on the environment and is read from
 * `fleet-tokens.json` in the Realms GitHub repo. ckBTC and ckUSDC are the
 * chain-key ledgers: the same principals on every environment.
 */

export interface SharedTokenCatalogEntry {
	ledger: string;
	indexer?: string;
	decimals?: number;
	name?: string;
}

/** `{symbol: {ledger, indexer, decimals}}` as the backend reports it. */
export type SharedTokenCatalog = Record<string, SharedTokenCatalogEntry>;

export interface SharedTokenOption {
	id: string;
	name: string;
	symbol: string;
	description: string;
	decimals: number;
	ledger: string;
	indexer?: string;
}

const TOKEN_INFO: Record<string, { name: string; description: string }> = {
	RLM: { name: 'Realms Token', description: 'The shared mundus-wide token, common to all realms' },
	REALMS: { name: 'REALMS Token', description: 'The shared mundus-wide token, common to all realms' },
	CKBTC: { name: 'ckBTC', description: 'Chain-Key Bitcoin — IC-native Bitcoin twin' },
	CKUSDC: { name: 'ckUSDC', description: 'Chain-Key USDC — IC-native USD stablecoin' },
	CKEURC: {
		name: 'ckEURC',
		description: 'Circle EURC on Ethereum, chain-key — IC-native euro stablecoin'
	}
};

export const CUSTOM_TOKEN_ID = 'custom';

/** Published map: environment name → Realms token ledger. */
export const FLEET_TOKENS_URL =
	'https://raw.githubusercontent.com/smart-social-contracts/realms-gos/main/fleet-tokens.json';

/** Chain-key ledgers. They do not vary by GaaS environment. */
export const CHAIN_KEY_TOKENS: SharedTokenOption[] = [
	{
		id: 'ckBTC',
		symbol: 'ckBTC',
		name: 'ckBTC',
		description: TOKEN_INFO.CKBTC.description,
		decimals: 8,
		ledger: 'mxzaz-hqaaa-aaaar-qaada-cai',
		indexer: 'n5wcd-faaaa-aaaar-qaaea-cai'
	},
	{
		id: 'ckUSDC',
		symbol: 'ckUSDC',
		name: 'ckUSDC',
		description: TOKEN_INFO.CKUSDC.description,
		decimals: 6,
		ledger: 'xevnm-gaaaa-aaaar-qafnq-cai',
		indexer: 'xrs4b-hiaaa-aaaar-qafoa-cai'
	}
];

/** Realms token for this environment, then the two chain-key ledgers. */
export function wizardTokenOptions(
	defaultToken: SharedTokenOption | null | undefined
): SharedTokenOption[] {
	const out: SharedTokenOption[] = [];
	if (defaultToken?.ledger) out.push(defaultToken);
	for (const token of CHAIN_KEY_TOKENS) {
		if (!out.some((item) => item.ledger === token.ledger)) out.push(token);
	}
	return out;
}

export function defaultTokenFromMap(
	table: unknown,
	environment: string
): SharedTokenOption | null {
	if (!table || typeof table !== 'object') return null;
	const name = environment.trim().toLowerCase();
	if (!name || name.startsWith('_')) return null;
	const entry = (table as Record<string, unknown>)[name];
	if (!entry || typeof entry !== 'object') return null;
	const row = entry as Record<string, unknown>;
	const ledger = String(row.ledger || '').trim();
	if (!ledger) return null;
	const symbol = String(row.symbol || 'RLM').trim() || 'RLM';
	const info = TOKEN_INFO[symbol.toUpperCase()];
	const indexer = String(row.indexer || '').trim();
	return {
		id: symbol,
		symbol,
		name: String(row.name || info?.name || symbol),
		description: info?.description || 'The Realms token for this environment',
		decimals: typeof row.decimals === 'number' ? row.decimals : 8,
		ledger,
		...(indexer ? { indexer } : {})
	};
}

export async function fetchDefaultTreasuryToken(environment: string): Promise<SharedTokenOption> {
	const name = environment.trim().toLowerCase();
	if (!name) throw new Error('This realm has no environment name');
	const response = await fetch(FLEET_TOKENS_URL);
	if (!response.ok) {
		throw new Error(`Could not load the Realms token map (${response.status})`);
	}
	const token = defaultTokenFromMap(await response.json(), name);
	if (!token) {
		throw new Error(`No Realms token is published for environment ${name}`);
	}
	return token;
}

/** The wizard's catalog cards, in the backend's order. Empty when the realm has none. */
export function sharedTokenOptions(
	catalog: SharedTokenCatalog | null | undefined
): SharedTokenOption[] {
	const out: SharedTokenOption[] = [];
	for (const [symbol, entry] of Object.entries(catalog || {})) {
		const ledger = String(entry?.ledger || '').trim();
		if (!symbol.trim() || !ledger) continue;
		const info = TOKEN_INFO[symbol.toUpperCase()];
		out.push({
			id: symbol,
			symbol,
			name: entry.name || info?.name || symbol,
			description: info?.description || `Shared ${symbol} ledger`,
			decimals: entry.decimals ?? 8,
			ledger,
			indexer: String(entry.indexer || '').trim() || undefined
		});
	}
	return out;
}

export function sharedTokenById(
	options: SharedTokenOption[],
	id: string
): SharedTokenOption | undefined {
	const wanted = (id || '').trim().toUpperCase();
	return options.find((token) => token.id.toUpperCase() === wanted);
}

export function matchSharedToken(
	options: SharedTokenOption[],
	input: { symbol?: string; token_canister_id?: string }
): SharedTokenOption | undefined {
	const canister = (input.token_canister_id || '').trim();
	if (canister) {
		const byLedger = options.find((token) => token.ledger === canister);
		if (byLedger) return byLedger;
	}
	const symbol = (input.symbol || '').trim().toUpperCase();
	if (!symbol) return undefined;
	return options.find(
		(token) => token.id.toUpperCase() === symbol || token.symbol.toUpperCase() === symbol
	);
}

export function tokenDraftFromChoice(
	choiceId: string,
	custom: { symbol: string; token_canister_id: string; indexer_canister_id?: string },
	options: SharedTokenOption[]
): Record<string, string | number> | null {
	if (choiceId === CUSTOM_TOKEN_ID) {
		const symbol = custom.symbol.trim();
		const token_canister_id = custom.token_canister_id.trim();
		const indexer_canister_id = (custom.indexer_canister_id || '').trim();
		if (!symbol || !token_canister_id || !indexer_canister_id) return null;
		return { symbol, token_canister_id, indexer_canister_id };
	}
	const token = sharedTokenById(options, choiceId);
	if (!token) return null;
	const draft: Record<string, string | number> = {
		symbol: token.symbol,
		token_canister_id: token.ledger,
		decimals: token.decimals
	};
	if (token.indexer) draft.indexer_canister_id = token.indexer;
	return draft;
}

export type CatalogTokenDraftInput =
	| {
			symbol?: string;
			id?: string;
			existing?: string;
			token_canister_id?: string | number;
			decimals?: number;
	  }
	| string
	| null
	| undefined;

function catalogTokenSymbol(token: CatalogTokenDraftInput): string {
	if (token == null) return '';
	if (typeof token === 'string') return token.trim();
	return String(token.symbol || token.id || token.existing || '').trim();
}

/** Fill ledger/decimals/indexer for every realistic catalog draft shape. */
export function completeCatalogTokenDraft(
	token: CatalogTokenDraftInput,
	options: SharedTokenOption[]
): Record<string, string | number> | null {
	if (token == null) return null;
	if (typeof token === 'string') {
		const symbol = token.trim();
		if (!symbol) return null;
		return completeCatalogTokenDraft({ symbol }, options);
	}
	const canister = String(token.token_canister_id || '').trim();
	const symbol = catalogTokenSymbol(token);
	if (canister) {
		return {
			...token,
			...(symbol ? { symbol } : {}),
			token_canister_id: canister
		};
	}
	const matched = matchSharedToken(options, { symbol });
	if (!matched) {
		return symbol ? { ...token, symbol } : { ...token };
	}
	return tokenDraftFromChoice(matched.id, { symbol: '', token_canister_id: '' }, options);
}

/** Payload for founder-auth ``setup_configure_token``. Empty/null stays fail-closed. */
export function configureTokenPayload(
	token: CatalogTokenDraftInput,
	options: SharedTokenOption[]
): Record<string, string | number> | null {
	const completed = completeCatalogTokenDraft(token, options);
	const ledger = String(completed?.token_canister_id || '').trim();
	if (!completed || !ledger) return null;
	const payload: Record<string, string | number> = { token_canister_id: ledger };
	const symbol = String(completed.symbol || '').trim();
	if (symbol) payload.symbol = symbol;
	if (completed.decimals != null) payload.decimals = completed.decimals;
	if (completed.indexer_canister_id) {
		payload.indexer_canister_id = String(completed.indexer_canister_id);
	}
	return payload;
}
