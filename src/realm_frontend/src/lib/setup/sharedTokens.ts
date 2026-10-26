/**
 * Treasury tokens a founder may adopt in the setup wizard: the realm backend's
 * `token_catalog`, plus a custom ICRC-1 ledger the founder enters by hand.
 */

/** One entry of the backend's `token_catalog`. */
export interface CatalogToken {
	symbol: string;
	name?: string;
	description?: string;
	ledger: string;
	indexer?: string;
	decimals?: number;
	monetary?: boolean;
}

export interface SharedTokenOption {
	id: string;
	name: string;
	symbol: string;
	description: string;
	decimals: number;
	ledger: string;
	indexer?: string;
	/** Real money: unavailable where the realm disables monetary tokens. */
	monetary: boolean;
}

export const CUSTOM_TOKEN_ID = 'custom';

/** The wizard's catalog cards, in the backend's order. */
export function catalogTokenOptions(tokens: CatalogToken[] | null | undefined): SharedTokenOption[] {
	const out: SharedTokenOption[] = [];
	for (const token of tokens || []) {
		const symbol = String(token?.symbol || '').trim();
		const ledger = String(token?.ledger || '').trim();
		if (!symbol || !ledger) continue;
		out.push({
			id: symbol,
			symbol,
			name: token.name || symbol,
			description: token.description || '',
			decimals: token.decimals ?? 8,
			ledger,
			indexer: String(token.indexer || '').trim() || undefined,
			monetary: token.monetary !== false
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
