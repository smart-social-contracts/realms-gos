import { describe, expect, it } from 'vitest';
import {
	choiceOf,
	choicesFor,
	decide,
	expiryError,
	scopeLabels,
	type Group
} from './authorizeChoices';

const group = (id: string, confirms = false): Group => ({
	id,
	label: `Label of ${id}`,
	summary: `What ${id} allows`,
	risk: `Risk of ${id}`,
	longest: 86400 * 365,
	confirms
});
const vocabulary = [group('vote'), group('propose', true), group('money')];
const holdsAll = () => true;

describe('the choices a request leaves the member', () => {
	it('are the groups it names, in the realm\'s own words, with the badge of those that ask each time', () => {
		const choices = choicesFor({ groups: ['vote', 'propose'] }, vocabulary, holdsAll);
		expect(choices.map((c) => [c.id, c.label, c.summary, c.risk, c.confirms, c.held])).toEqual([
			['vote', 'Label of vote', 'What vote allows', 'Risk of vote', false, true],
			['propose', 'Label of propose', 'What propose allows', 'Risk of propose', true, true]
		]);
	});

	it('mark a group the member holds nothing in as one they cannot grant', () => {
		const [vote, unknown] = choicesFor({ groups: ['vote', 'office'] }, vocabulary, holdsAll);
		expect([vote.held, unknown.held, unknown.label]).toEqual([true, false, 'office']);
	});

	it('are every group the member holds when the request asks for everything', () => {
		expect(choicesFor({ groups: ['*'] }, vocabulary, holdsAll).map((c) => c.id)).toEqual(['vote', 'propose', 'money']);
	});

	it('are the permission names of a request from a client that predates groups', () => {
		const choices = choicesFor({ operations: ['proposal.vote', 'self.data_view'] }, vocabulary, (p) => p === 'proposal.vote');
		expect(choices.map((c) => [c.id, c.label, c.held])).toEqual([
			['proposal.vote', 'proposal.vote', true],
			['self.data_view', 'self.data_view', false]
		]);
	});
});

describe('the scope the member grants', () => {
	const asked = { groups: ['vote', 'propose'] };
	const choices = choicesFor(asked, vocabulary, holdsAll);

	it('is what they ticked of what was asked', () => {
		expect(decide(asked, choices, ['vote'], false)).toEqual({ ok: true, scope: { groups: ['vote'] } });
	});

	it('is never a group the request did not ask for', () => {
		expect(decide(asked, choices, ['vote', 'money'], false)).toEqual({ ok: false, error: 'not_asked' });
	});

	it('is never a group the member holds nothing in', () => {
		const withUnheld = choicesFor({ groups: ['vote', 'office'] }, vocabulary, holdsAll);
		expect(decide({ groups: ['vote', 'office'] }, withUnheld, ['office'], false)).toEqual({ ok: false, error: 'not_asked' });
	});

	it('is never nothing', () => {
		expect(decide(asked, choices, [], false)).toEqual({ ok: false, error: 'none_chosen' });
	});

	it('is never everything when the request did not ask for it', () => {
		expect(decide(asked, choices, ['vote'], true)).toEqual({ ok: false, error: 'everything_not_asked' });
	});

	it('is everything only when the request asked for it and the member ticked it', () => {
		const everything = { groups: ['*'] };
		expect(decide(everything, choicesFor(everything, vocabulary, holdsAll), [], true)).toEqual({
			ok: true,
			scope: { groups: ['*'] }
		});
	});

	it('may be fewer groups than a request for everything', () => {
		const everything = { groups: ['*'] };
		const all = choicesFor(everything, vocabulary, holdsAll);
		expect(decide(everything, all, ['vote'], false)).toEqual({ ok: true, scope: { groups: ['vote'] } });
	});

	it('stays permission names for a request that named them', () => {
		const old = { operations: ['proposal.vote'] };
		expect(decide(old, choicesFor(old, vocabulary, holdsAll), ['proposal.vote'], false)).toEqual({
			ok: true,
			scope: { operations: ['proposal.vote'] }
		});
	});
});

describe('the expiry the member grants', () => {
	const asked = { starts_at: 0, expires_at: 2_000 };

	it('may be earlier than asked', () => {
		expect(expiryError(asked, 1_500, 1_000)).toBe('');
		expect(expiryError(asked, 2_000, 1_000)).toBe('');
	});

	it('is never later than asked, nor missing', () => {
		expect(expiryError(asked, 2_001, 1_000)).toBe('too_late');
		expect(expiryError(asked, 0, 1_000)).toBe('too_late');
	});

	it('is in the future and after the start', () => {
		expect(expiryError(asked, 900, 1_000)).toBe('not_future');
		expect(expiryError({ starts_at: 1_800, expires_at: 2_000 }, 1_700, 1_000)).toBe('not_future');
	});
});

describe('what a grant covers, in words', () => {
	it('is the labels of its groups, and "Everything" for all of them', () => {
		expect(scopeLabels({ groups: ['vote', 'office', '*'] }, vocabulary, 'Everything')).toEqual([
			'Label of vote',
			'office',
			'Everything'
		]);
	});

	it('is the permission names of a grant made before groups', () => {
		expect(scopeLabels({ operations: ['proposal.vote'] }, vocabulary, 'Everything')).toEqual(['proposal.vote']);
	});

	it('is a choice to tick for each group the member holds', () => {
		expect(choiceOf(vocabulary[1])).toMatchObject({ id: 'propose', confirms: true, held: true });
	});
});
