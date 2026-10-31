import { describe, expect, it } from 'vitest';
import {
	CUSTOM_TOKEN_ID,
	catalogTokenOptions,
	completeCatalogTokenDraft,
	configureTokenPayload,
	matchSharedToken,
	tokenDraftFromChoice
} from './sharedTokens';

// A catalog as the backend's token_catalog reports it. Fixture ids only —
// the frontend holds no ledger ids of its own.
const CATALOG = [
	{ symbol: 'ckBTC', ledger: 'bbbbb-bb', indexer: 'bbbbb-ix', decimals: 8, monetary: true },
	{ symbol: 'ckUSDC', ledger: 'ccccc-cc', decimals: 6, monetary: true },
	{ symbol: 'ckEURC', name: 'ckEURC', ledger: 'eeeee-ee', decimals: 6, monetary: true }
];
const OPTIONS = catalogTokenOptions(CATALOG);
const EURC = { symbol: 'ckEURC', token_canister_id: 'eeeee-ee', decimals: 6 };

describe('sharedTokens', () => {
	it('derives the wizard cards from the backend catalog, in its order', () => {
		expect(OPTIONS.map((token) => token.id)).toEqual(['ckBTC', 'ckUSDC', 'ckEURC']);
		expect(OPTIONS.find((t) => t.id === 'ckEURC')).toMatchObject({
			name: 'ckEURC',
			ledger: 'eeeee-ee',
			decimals: 6,
			monetary: true
		});
		expect(OPTIONS.find((t) => t.id === 'ckBTC')?.indexer).toBe('bbbbb-ix');
	});

	it('has no cards without a catalog, and skips entries without a ledger', () => {
		expect(catalogTokenOptions(undefined)).toEqual([]);
		expect(catalogTokenOptions([])).toEqual([]);
		expect(
			catalogTokenOptions([
				{ symbol: 'X', ledger: '' },
				{ symbol: 'Y', ledger: 'yyyyy-yy' }
			]).map((t) => t.id)
		).toEqual(['Y']);
	});

	it('names a token after its symbol and treats it as money unless the catalog says otherwise', () => {
		expect(catalogTokenOptions([{ symbol: 'FOO', ledger: 'fffff-ff', decimals: 2 }])[0]).toMatchObject({
			id: 'FOO',
			name: 'FOO',
			decimals: 2,
			monetary: true
		});
		expect(catalogTokenOptions([{ symbol: 'FOO', ledger: 'fffff-ff', monetary: false }])[0].monetary).toBe(false);
	});

	it('matches a saved symbol or ledger to a catalog token', () => {
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

	it('does not invent a token for an empty or null token', () => {
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
