import { describe, expect, it } from 'vitest';
import { describeEvent, type ActivityEvent } from './activity';

const event = (over: Partial<ActivityEvent>): ActivityEvent => ({
	id: '1',
	op: 'proposal_vote',
	origin: 'user',
	args: {},
	result: {},
	at: 100,
	...over
});

describe('what an event of a delegate says', () => {
	it('is the act and that it was done', () => {
		expect(describeEvent(event({}))).toEqual({ act: 'proposal_vote', outcome: 'done', reference: '', href: '' });
	});

	it('links the proposal an act concerns', () => {
		expect(describeEvent(event({ args: { proposal: 'P-7' } }))).toMatchObject({ reference: 'P-7', href: '/proposal/P-7' });
		expect(describeEvent(event({ result: { proposal: 'P-9' } }))).toMatchObject({ href: '/proposal/P-9' });
	});

	it('links the action that waits for the member', () => {
		expect(describeEvent(event({ op: 'payment_account_add', result: { pending: 'AC-3' } }))).toEqual({
			act: 'payment_account_add',
			outcome: 'waiting',
			reference: 'AC-3',
			href: '/delegations/confirm/AC-3'
		});
	});

	it('says an act the member approved was approved, and a read was collected', () => {
		expect(describeEvent(event({ origin: 'confirmed' })).outcome).toBe('approved');
		expect(describeEvent(event({ result: { collected: 'AC-4' } }))).toMatchObject({ outcome: 'collected', href: '' });
	});
});
