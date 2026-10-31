import { describe, expect, it } from 'vitest';
import {
	canAdvanceFromCodexStep,
	canAdvanceFromWelcomeStep,
	canNavigateToWizardStep,
	getCodexStepPrimaryLabel,
	getNextWizardStep,
	getPreviousWizardStep,
	getWelcomeAdvanceStep,
	isCodexChosen,
	isCodexInstalled,
	isCodexPrimaryActionDisabled,
	applyDraftTokenDidPersist,
	firstFailedLaunchStepError,
	founderConfigureTokenFromSetupState,
	isFailedOrRunningLaunch,
	reconcileCodexVersion,
	resolveInitialWizardStep,
	resolveReviewTokenSymbol,
	resolveSelectedCodexVersion,
	shouldClearCodexAdvanceError,
	isSetupCatalogCodex,
	stepToUrlToken,
	urlTokenToStep
} from './wizardLogic';
import type { SetupState } from './types';
import { catalogTokenOptions, tokenDraftFromChoice } from './sharedTokens';

const installedState: SetupState = {
	status: 'setup',
	creator: 'abc',
	is_caller_authorized: true,
	codex: { package: 'agora', version: '0.9.5' },
	token: null,
	branding: null
};

const draftChosenState: SetupState = {
	status: 'setup',
	creator: 'abc',
	is_caller_authorized: true,
	codex: null,
	token: null,
	branding: null,
	draft: {
		step: 'codex',
		codex: { package: 'agora', version: '0.9.5' }
	}
};

const freshState: SetupState = {
	status: 'setup',
	creator: 'abc',
	is_caller_authorized: true,
	codex: null,
	token: null,
	branding: null
};

describe('wizardLogic', () => {
	it('detects installed codex from setup state', () => {
		expect(isCodexInstalled(installedState)).toBe(true);
		expect(isCodexInstalled({ ...installedState, codex: null })).toBe(false);
	});

	it('detects chosen codex from draft or installed backend state', () => {
		expect(isCodexChosen(draftChosenState)).toBe(true);
		expect(isCodexChosen(installedState)).toBe(true);
		expect(isCodexChosen(freshState)).toBe(false);
		expect(isCodexChosen(null)).toBe(false);
	});

	it('allows advancing when draft or backend has a codex', () => {
		expect(canAdvanceFromCodexStep(draftChosenState)).toBe(true);
		expect(canAdvanceFromCodexStep(installedState)).toBe(true);
		expect(canAdvanceFromCodexStep(freshState)).toBe(false);
	});

	it('falls back to draft then installed version when UI selection is empty', () => {
		expect(resolveSelectedCodexVersion('', draftChosenState)).toBe('0.9.5');
		expect(resolveSelectedCodexVersion('', installedState)).toBe('0.9.5');
		expect(resolveSelectedCodexVersion('0.9.4', installedState)).toBe('0.9.4');
	});

	it('keeps saved version when catalog list omits it', () => {
		const versions = ['0.9.6', '1.0.0'];
		expect(
			reconcileCodexVersion(versions, '', '0.9.5', (v) => v[v.length - 1] ?? '')
		).toBe('0.9.5');
	});

	it('uses latest catalog version when nothing is selected or saved', () => {
		const versions = ['0.9.4', '0.9.5'];
		expect(reconcileCodexVersion(versions, '', undefined, (v) => v[v.length - 1] ?? '')).toBe(
			'0.9.5'
		);
	});

	describe('codex primary action', () => {
		it('always labels continue when not busy', () => {
			expect(getCodexStepPrimaryLabel(freshState, false)).toBe('Continue');
			expect(getCodexStepPrimaryLabel(installedState, false)).toBe('Continue');
			expect(getCodexStepPrimaryLabel(freshState, true)).toBe('Continuing…');
		});

		it('requires codex and version selection on a fresh realm', () => {
			expect(isCodexPrimaryActionDisabled(false, 'agora', '0.9.5', freshState)).toBe(false);
			expect(isCodexPrimaryActionDisabled(false, '', '0.9.5', freshState)).toBe(true);
			expect(isCodexPrimaryActionDisabled(false, 'agora', '', freshState)).toBe(true);
		});

		it('enables continue when codex is already chosen', () => {
			expect(isCodexPrimaryActionDisabled(false, '', '', draftChosenState)).toBe(false);
		});
	});

	describe('welcome step', () => {
		it('always allows advancing to codex without backend state', () => {
			expect(canAdvanceFromWelcomeStep()).toBe(true);
			expect(getWelcomeAdvanceStep()).toBe('codex');
		});

		it('places welcome first in the step order', () => {
			expect(getPreviousWizardStep('welcome')).toBeNull();
			expect(getPreviousWizardStep('codex')).toBe('welcome');
			expect(getNextWizardStep('welcome')).toBe('codex');
			expect(getNextWizardStep('review')).toBeNull();
		});

		it('allows navigation from welcome to codex without a chosen codex', () => {
			expect(canNavigateToWizardStep('welcome', 'codex', freshState)).toEqual({ allowed: true });
		});

		it('blocks skipping ahead from welcome past codex', () => {
			expect(canNavigateToWizardStep('welcome', 'token', freshState)).toEqual({
				allowed: false,
				showError: false,
				errorMessage: 'Choose a codex before continuing to later steps'
			});
		});
	});

	describe('back navigation', () => {
		it('returns the previous step for steps after welcome', () => {
			expect(getPreviousWizardStep('welcome')).toBeNull();
			expect(getPreviousWizardStep('codex')).toBe('welcome');
			expect(getPreviousWizardStep('token')).toBe('codex');
			expect(getPreviousWizardStep('branding')).toBe('token');
			expect(getPreviousWizardStep('languages')).toBe('branding');
			expect(getPreviousWizardStep('review')).toBe('languages');
			expect(getNextWizardStep('branding')).toBe('languages');
			expect(getNextWizardStep('languages')).toBe('review');
		});
	});

	describe('step navigation and banner scoping', () => {
		it('blocks skipping ahead from codex without a choice and surfaces the banner', () => {
			expect(canNavigateToWizardStep('codex', 'token', freshState)).toEqual({
				allowed: false,
				showError: true,
				errorMessage: 'Choose a codex before continuing to later steps'
			});
		});

		it('allows back navigation without the banner', () => {
			expect(canNavigateToWizardStep('token', 'codex', freshState)).toEqual({ allowed: true });
			expect(canNavigateToWizardStep('review', 'languages', freshState)).toEqual({ allowed: true });
			expect(canNavigateToWizardStep('languages', 'branding', freshState)).toEqual({ allowed: true });
		});

		it('allows forward navigation once codex is chosen', () => {
			expect(canNavigateToWizardStep('codex', 'token', draftChosenState)).toEqual({ allowed: true });
		});

		it('clears the codex banner when returning to the codex step', () => {
			expect(
				shouldClearCodexAdvanceError(
					'codex',
					'Choose a codex before continuing to later steps'
				)
			).toBe(true);
			expect(
				shouldClearCodexAdvanceError('token', 'Choose a codex before continuing to later steps')
			).toBe(false);
		});
	});

	describe('initial step resolution', () => {
		const failedLaunchState: SetupState = {
			...freshState,
			launch: {
				status: 'failed',
				phase: 'configure_token',
				steps: [
					{ name: 'install_codex', status: 'completed', error: null },
					{
						name: 'configure_token',
						status: 'failed',
						error: 'No treasury currency — set the treasury ledger canister in Realm Settings so the token symbol can be resolved'
					}
				],
				updated_at: '1'
			}
		};
		const runningLaunchState: SetupState = {
			...freshState,
			launch: { status: 'running', phase: 'install_codex', steps: [], updated_at: null }
		};

		it('prefers a reachable URL step', () => {
			expect(resolveInitialWizardStep(draftChosenState, 'token')).toBe('token');
		});

		it('resumes a running or failed launch on review when no step is requested', () => {
			expect(resolveInitialWizardStep(runningLaunchState, null)).toBe('review');
			expect(resolveInitialWizardStep(failedLaunchState, null)).toBe('review');
			expect(isFailedOrRunningLaunch(failedLaunchState)).toBe(true);
		});

		it('surfaces the fossil configure_token error for the top banner', () => {
			expect(firstFailedLaunchStepError(failedLaunchState.launch)).toContain('Realm Settings');
			expect(firstFailedLaunchStepError(runningLaunchState.launch)).toBe('');
			expect(firstFailedLaunchStepError(null)).toBe('');
		});

		it('does not trap a failed launch on review when the URL asks for token', () => {
			expect(resolveInitialWizardStep(failedLaunchState, 'token')).toBe('token');
			expect(resolveInitialWizardStep(failedLaunchState, 'launch')).toBe('review');
		});

		it('does not trap a running launch on review when the URL asks for token', () => {
			expect(resolveInitialWizardStep(runningLaunchState, 'token')).toBe('token');
		});

		it('allows the Token stepper after a failed launch even without a draft codex', () => {
			expect(canNavigateToWizardStep('review', 'token', failedLaunchState)).toEqual({
				allowed: true
			});
			expect(canNavigateToWizardStep('welcome', 'token', failedLaunchState)).toEqual({
				allowed: true
			});
			expect(canNavigateToWizardStep('welcome', 'token', freshState)).toEqual({
				allowed: false,
				showError: false,
				errorMessage: 'Choose a codex before continuing to later steps'
			});
		});

		it('falls back to the saved draft step', () => {
			expect(
				resolveInitialWizardStep(
					{ ...draftChosenState, draft: { ...draftChosenState.draft!, step: 'branding' } },
					null
				)
			).toBe('branding');
			expect(
				resolveInitialWizardStep(
					{ ...draftChosenState, draft: { ...draftChosenState.draft!, step: 'languages' } },
					null
				)
			).toBe('languages');
		});
	});

	describe('review token symbol and persist contract', () => {
		it('shows the persisted draft symbol, not the REALMS UI default', () => {
			expect(
				resolveReviewTokenSymbol({
					...freshState,
					draft: {
						token: {
							symbol: 'ckEURC',
							token_canister_id: 'pe5t5-diaaa-aaaar-qahwa-cai'
						}
					}
				})
			).toBe('ckEURC');
		});

		it('can show ckEURC from a symbol-only draft (ledger filled at save/launch)', () => {
			expect(
				resolveReviewTokenSymbol({
					...freshState,
					draft: { token: { symbol: 'ckEURC' } }
				})
			).toBe('ckEURC');
		});

		it('can show ckEURC from a string, {id}, or {existing} draft', () => {
			expect(
				resolveReviewTokenSymbol({
					...freshState,
					draft: { token: 'ckEURC' }
				})
			).toBe('ckEURC');
			expect(
				resolveReviewTokenSymbol({
					...freshState,
					draft: { token: { id: 'ckEURC' } }
				})
			).toBe('ckEURC');
			expect(
				resolveReviewTokenSymbol({
					...freshState,
					draft: { token: { existing: 'ckEURC' } }
				})
			).toBe('ckEURC');
		});

		it('treats an explicit skipped token as empty so review can say Skipped', () => {
			expect(resolveReviewTokenSymbol({ ...freshState, draft: { token: null } })).toBe('');
			expect(resolveReviewTokenSymbol(freshState)).toBe('');
		});

		it('Retry persist check rejects leftover success:true without a realm ledger', () => {
			const fossilState: SetupState = {
				...freshState,
				token: null,
				realm_token_canister_id: null
			};
			expect(
				applyDraftTokenDidPersist(
					'pe5t5-diaaa-aaaar-qahwa-cai',
					{ success: true },
					fossilState
				)
			).toBe(false);
			expect(
				applyDraftTokenDidPersist(
					'pe5t5-diaaa-aaaar-qahwa-cai',
					{ success: true, token: { token_canister_id: 'pe5t5-diaaa-aaaar-qahwa-cai' } },
					{ ...fossilState, token: null, realm_token_canister_id: '' }
				)
			).toBe(false);
			expect(
				applyDraftTokenDidPersist(
					'pe5t5-diaaa-aaaar-qahwa-cai',
					{ success: true, token: { token_canister_id: 'pe5t5-diaaa-aaaar-qahwa-cai' } },
					{
						...fossilState,
						token: { token_canister_id: 'pe5t5-diaaa-aaaar-qahwa-cai' },
						realm_token_canister_id: 'pe5t5-diaaa-aaaar-qahwa-cai'
					}
				)
			).toBe(true);
		});

		const catalogState: SetupState = {
			...freshState,
			token_catalog: catalogTokenOptions([{ symbol: 'ckEURC', ledger: 'eeeee-ee', decimals: 6 }])
		};

		it('Launch / Token Continue founder payload writes the draft ledger', () => {
			expect(
				founderConfigureTokenFromSetupState({
					...catalogState,
					draft: { token: { symbol: 'ckEURC', token_canister_id: 'eeeee-ee', decimals: 6 } }
				})
			).toEqual({ symbol: 'ckEURC', token_canister_id: 'eeeee-ee', decimals: 6 });
			expect(
				founderConfigureTokenFromSetupState({ ...catalogState, draft: { token: null } })
			).toBeNull();
		});

		it('fills a symbol-only draft from the catalog the backend reports', () => {
			expect(
				founderConfigureTokenFromSetupState({ ...catalogState, draft: { token: { symbol: 'ckEURC' } } })
			).toEqual({ symbol: 'ckEURC', token_canister_id: 'eeeee-ee', decimals: 6 });
			// No catalog from the backend: nothing to fill from, fail closed.
			expect(
				founderConfigureTokenFromSetupState({ ...freshState, draft: { token: { symbol: 'ckEURC' } } })
			).toBeNull();
		});

		it('maps a catalog ckEURC choice to the ledger configure_token reads', () => {
			const options = catalogState.token_catalog ?? [];
			const token = tokenDraftFromChoice('ckEURC', { symbol: '', token_canister_id: '' }, options);
			expect(token).toEqual({ symbol: 'ckEURC', token_canister_id: 'eeeee-ee', decimals: 6 });
			expect(
				resolveReviewTokenSymbol({
					...freshState,
					draft: { token: { symbol: String(token!.symbol), token_canister_id: String(token!.token_canister_id) } }
				})
			).toBe('ckEURC');
		});
	});

	describe('url token mapping', () => {
		it('maps review to launch in the URL', () => {
			expect(stepToUrlToken('review')).toBe('launch');
			expect(stepToUrlToken('welcome')).toBe('welcome');
			expect(stepToUrlToken('codex')).toBe('codex');
			expect(stepToUrlToken('token')).toBe('token');
			expect(stepToUrlToken('branding')).toBe('branding');
			expect(stepToUrlToken('languages')).toBe('languages');
		});

		it('maps launch back to review and rejects unknown tokens', () => {
			expect(urlTokenToStep('launch')).toBe('review');
			expect(urlTokenToStep('welcome')).toBe('welcome');
			expect(urlTokenToStep('codex')).toBe('codex');
			expect(urlTokenToStep('token')).toBe('token');
			expect(urlTokenToStep('branding')).toBe('branding');
			expect(urlTokenToStep('languages')).toBe('languages');
			expect(urlTokenToStep('review')).toBeNull();
			expect(urlTokenToStep('')).toBeNull();
			expect(urlTokenToStep('unknown')).toBeNull();
		});
	});

	describe('setup catalog filter', () => {
		it('hides shared and unfinished packages', () => {
			expect(isSetupCatalogCodex('agora')).toBe(true);
			expect(isSetupCatalogCodex('syntropia')).toBe(true);
			expect(isSetupCatalogCodex('dominion')).toBe(true);
			expect(isSetupCatalogCodex('common')).toBe(false);
			expect(isSetupCatalogCodex('westminster')).toBe(false);
			expect(isSetupCatalogCodex('_common')).toBe(false);
		});
	});
});
