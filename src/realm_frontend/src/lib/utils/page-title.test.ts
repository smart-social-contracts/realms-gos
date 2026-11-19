import { describe, expect, it } from 'vitest';
import { pageTitle } from './page-title';

describe('pageTitle', () => {
	it('names the page, then the realm', () => {
		expect(pageTitle('Voting', 'Vilanova')).toBe('Voting · Vilanova');
	});

	it('leaves out a part that is missing', () => {
		expect(pageTitle('', 'Vilanova')).toBe('Vilanova');
		expect(pageTitle(' Voting ', undefined)).toBe('Voting');
	});

	it('never leaves the tab untitled', () => {
		expect(pageTitle('', null)).toBe('Realm');
	});
});
