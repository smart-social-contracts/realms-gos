import { describe, expect, it } from 'vitest';
import {
	CHAIN_KEY_TOKENS,
	CUSTOM_TOKEN_ID,
	completeCatalogTokenDraft,
	configureTokenPayload,
	defaultTokenFromLedger,
	matchSharedToken,
	sharedTokenOptions,
	tokenDraftFromChoice,
	wizardTokenOptions
} from './sharedTokens';

// A catalog as get_setup_state().shared_tokens reports it. Fixture ids only —
// the frontend holds no ledger ids of its own.
const CATALOG = {
	REALMS: { ledger: 'aaaaa-aa', decimals: 8 },
	ckBTC: { ledger: 'bbbbb-bb', indexer: 'bbbbb-ix', decimals: 8 },
	ckUSDC: { ledger: 'ccccc-cc', decimals: 6 },
	ckEURC: { ledger: 'eeeee-ee', decimals: 6 }
};
const OPTIONS = sharedTokenOptions(CATALOG);
const EURC = { symbol: 'ckEURC', token_canister_id: 'eeeee-ee', decimals: 6 };

describe('sharedTokens', () => {
	it('derives the wizard cards from the backend catalog, in its order', () => {
		expect(OPTIONS.map((token) => token.id)).toEqual(['REALMS', 'ckBTC', 'ckUSDC', 'ckEURC']);
		expect(OPTIONS.find((t) => t.id === 'ckEURC')).toMatchObject({
			name: 'ckEURC',
			ledger: 'eeeee-ee',
			decimals: 6
		});
		expect(OPTIONS.find((t) => t.id === 'ckBTC')?.indexer).toBe('bbbbb-ix');
		expect(OPTIONS.map((token) => token.symbol)).not.toContain('ckEUR');
	});

	it('has no cards without a catalog, and skips entries without a ledger', () => {
		expect(sharedTokenOptions(undefined)).toEqual([]);
		expect(sharedTokenOptions({})).toEqual([]);
		expect(sharedTokenOptions({ X: { ledger: '' }, Y: { ledger: 'yyyyy-yy' } }).map((t) => t.id)).toEqual(
			['Y']
		);
	});

	it('names unknown symbols after themselves', () => {
		expect(sharedTokenOptions({ FOO: { ledger: 'fffff-ff', decimals: 2 } })[0]).toMatchObject({
			id: 'FOO',
			name: 'FOO',
			decimals: 2
		});
	});

	it('matches a saved symbol or ledger to a catalog token', () => {
		expect(matchSharedToken(OPTIONS, { symbol: 'realms' })?.id).toBe('REALMS');
		expect(matchSharedToken(OPTIONS, { token_canister_id: 'bbbbb-bb' })?.id).toBe('ckBTC');
		expect(matchSharedToken(OPTIONS, { symbol: 'CKEURC' })?.id).toBe('ckEURC');
		expect(matchSharedToken(OPTIONS, { token_canister_id: 'eeeee-ee' })?.id).toBe('ckEURC');
		expect(matchSharedToken(OPTIONS, { symbol: 'MINE', token_canister_id: 'zzzzz-zz' })).toBeUndefined();
	});

	it('fills ledger, decimals and indexer for a catalog choice', () => {
		expect(tokenDraftFromChoice('ckUSDC', { symbol: '', token_canister_id: '' }, OPTIONS)).toEqual({
			symbol: 'ckUSDC',
			token_canister_id: 'ccccc-cc',
			decimals: 6
		});
		expect(tokenDraftFromChoice('ckBTC', { symbol: '', token_canister_id: '' }, OPTIONS)).toEqual({
			symbol: 'ckBTC',
			token_canister_id: 'bbbbb-bb',
			decimals: 8,
			indexer_canister_id: 'bbbbb-ix'
		});
		expect(tokenDraftFromChoice('ckEURC', { symbol: '', token_canister_id: '' }, [])).toBeNull();
	});

	it('requires a looked-up symbol, ledger, and index for a custom token', () => {
		expect(
			tokenDraftFromChoice(CUSTOM_TOKEN_ID, { symbol: 'MINE', token_canister_id: '' }, OPTIONS)
		).toBeNull();
		expect(
			tokenDraftFromChoice(CUSTOM_TOKEN_ID, { symbol: 'MINE', token_canister_id: 'zzzzz-zz' }, OPTIONS)
		).toBeNull();
		expect(
			tokenDraftFromChoice(
				CUSTOM_TOKEN_ID,
				{ symbol: '', token_canister_id: 'zzzzz-zz', indexer_canister_id: 'yyyyy-yy' },
				OPTIONS
			)
		).toBeNull();
		expect(
			tokenDraftFromChoice(
				CUSTOM_TOKEN_ID,
				{ symbol: 'MINE', token_canister_id: 'zzzzz-zz', indexer_canister_id: 'yyyyy-yy' },
				OPTIONS
			)
		).toEqual({
			symbol: 'MINE',
			token_canister_id: 'zzzzz-zz',
			indexer_canister_id: 'yyyyy-yy'
		});
	});

	it('offers the environment Realms token plus ckBTC and ckUSDC', () => {
		const realms = defaultTokenFromLedger('r6dmi-3yaaa-aaaak-qzgka-cai', 'RLM');
		expect(realms).toMatchObject({
			id: 'RLM',
			symbol: 'RLM',
			name: 'Realms Token',
			ledger: 'r6dmi-3yaaa-aaaak-qzgka-cai'
		});
		expect(defaultTokenFromLedger('', 'RLM')).toBeNull();
		expect(defaultTokenFromLedger('aaaaa-aa', '')?.symbol).toBe('RLM');
		expect(wizardTokenOptions(realms).map((token) => token.id)).toEqual(['RLM', 'ckBTC', 'ckUSDC']);
		expect(wizardTokenOptions(null).map((token) => token.ledger)).toEqual(
			CHAIN_KEY_TOKENS.map((token) => token.ledger)
		);
	});

	it('Token Continue persists the catalog ledger for a ckEURC pick', () => {
		expect(
			completeCatalogTokenDraft(
				tokenDraftFromChoice('ckEURC', { symbol: '', token_canister_id: '' }, OPTIONS),
				OPTIONS
			)
		).toEqual(EURC);
	});

	it('fills the ledger when a catalog pick was stored as symbol-only', () => {
		expect(completeCatalogTokenDraft({ symbol: 'ckEURC' }, OPTIONS)).toEqual(EURC);
	});

	it('fills the ledger from a bare string, {id}, or {existing} catalog pick', () => {
		expect(completeCatalogTokenDraft('ckEURC', OPTIONS)).toEqual(EURC);
		expect(completeCatalogTokenDraft('ckEURC ', OPTIONS)).toEqual(EURC);
		expect(completeCatalogTokenDraft({ id: 'ckEURC' }, OPTIONS)).toEqual(EURC);
		expect(completeCatalogTokenDraft({ existing: 'ckEURC' }, OPTIONS)).toEqual(EURC);
	});

	it('does not invent REALMS for an empty or null token', () => {
		expect(completeCatalogTokenDraft(null, OPTIONS)).toBeNull();
		expect(completeCatalogTokenDraft('', OPTIONS)).toBeNull();
		expect(completeCatalogTokenDraft('   ', OPTIONS)).toBeNull();
	});

	it('does not invent a ledger for a custom token without a canister id', () => {
		expect(
			completeCatalogTokenDraft(
				tokenDraftFromChoice(CUSTOM_TOKEN_ID, { symbol: 'MINE', token_canister_id: '' }, OPTIONS),
				OPTIONS
			)
		).toBeNull();
	});

	it('Token Continue payload is setup_configure_token with the catalog ledger', () => {
		expect(
			configureTokenPayload(
				tokenDraftFromChoice('ckEURC', { symbol: '', token_canister_id: '' }, OPTIONS),
				OPTIONS
			)
		).toEqual(EURC);
		expect(configureTokenPayload(null, OPTIONS)).toBeNull();
		expect(configureTokenPayload({ symbol: 'ckEURC' }, OPTIONS)).toEqual(EURC);
		// Symbol-only with no catalog: fail closed rather than guess a ledger.
		expect(configureTokenPayload({ symbol: 'ckEURC' }, [])).toBeNull();
	});
});
