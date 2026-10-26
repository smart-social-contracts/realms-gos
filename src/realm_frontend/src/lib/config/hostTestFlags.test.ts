import { describe, expect, it } from 'vitest';
import {
	filledNoticeTranslationCount,
	isTokenChoiceSelectable,
	monetaryUnavailableLabel,
	NOTICE_LOCALE_SLOTS,
	noticeTranslationSlots,
	resolveDemoNoticeEnabled,
	resolveDemoNoticeView,
	resolveDisableMonetaryTokens,
	shouldShowJoinNotice
} from './hostTestFlags';

describe('hostTestFlags', () => {
	it('defaults monetary tokens off on gos.earth networks', () => {
		expect(resolveDisableMonetaryTokens(undefined, 'staging')).toBe(true);
		expect(resolveDisableMonetaryTokens(undefined, 'demo')).toBe(true);
		expect(resolveDisableMonetaryTokens(undefined, 'test')).toBe(true);
		expect(resolveDisableMonetaryTokens(undefined, 'ic')).toBe(false);
		expect(resolveDisableMonetaryTokens(false, 'staging')).toBe(false);
		expect(resolveDisableMonetaryTokens(true, 'ic')).toBe(true);
	});

	it('defaults the demo notice on for staging and demo only', () => {
		expect(resolveDemoNoticeEnabled(undefined, 'staging')).toBe(true);
		expect(resolveDemoNoticeEnabled(undefined, 'demo')).toBe(true);
		expect(resolveDemoNoticeEnabled(undefined, 'test')).toBe(false);
		expect(resolveDemoNoticeEnabled(false, 'staging')).toBe(false);
	});

	it('shows the join notice only when the flag is on and terms are not skipped', () => {
		expect(shouldShowJoinNotice(true, false)).toBe(true);
		expect(shouldShowJoinNotice(true, true)).toBe(false);
		expect(shouldShowJoinNotice(false, false)).toBe(false);
	});

	it('grays out monetary catalog tokens only when they are disabled', () => {
		expect(isTokenChoiceSelectable(true, true)).toBe(false);
		expect(isTokenChoiceSelectable(true, false)).toBe(true);
		expect(isTokenChoiceSelectable(false, true)).toBe(true);
	});

	it('uses the specified unavailable labels', () => {
		expect(monetaryUnavailableLabel('en')).toBe('Not available in this demo');
		expect(monetaryUnavailableLabel('es')).toBe('No disponible en esta demo');
		expect(monetaryUnavailableLabel('ca-valencia')).toBe('Not available in this demo');
	});

	it('shows primary locale + English when a translation slot is filled', () => {
		const view = resolveDemoNoticeView(
			{ en: 'English notice.', es: 'Aviso en español.', 'ca-valencia': '' },
			'es'
		);
		expect(view.showPrimary).toBe(true);
		expect(view.primary).toBe('Aviso en español.');
		expect(view.english).toBe('English notice.');
	});

	it('does not invent a primary-locale translation', () => {
		const view = resolveDemoNoticeView({ en: 'English notice.', es: '' }, 'es');
		expect(view.showPrimary).toBe(false);
		expect(view.primary).toBe('');
		expect(view.english).toBe('English notice.');
	});

	it('treats every notice locale except English as a translation slot', () => {
		expect(noticeTranslationSlots()).toEqual(['es', 'de', 'fr', 'it', 'zh-CN', 'ca-valencia']);
		expect(noticeTranslationSlots(NOTICE_LOCALE_SLOTS)).not.toContain('en');
	});

	it('counts only non-empty translation slots', () => {
		expect(filledNoticeTranslationCount(undefined)).toBe(0);
		expect(filledNoticeTranslationCount({ en: 'English notice.', es: '  ', de: 'Hinweis' })).toBe(1);
		expect(
			filledNoticeTranslationCount({
				en: 'English notice.',
				es: 'Aviso',
				de: 'Hinweis',
				fr: '',
				it: '',
				'zh-CN': '',
				'ca-valencia': ''
			})
		).toBe(2);
	});
});
