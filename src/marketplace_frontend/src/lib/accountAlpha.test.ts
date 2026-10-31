import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { accountPagesAlpha } from './accountAlpha.ts';

describe('accountPagesAlpha', () => {
	it('is on for staging, demo, and test when the flag is unset', () => {
		assert.equal(accountPagesAlpha({ envName: 'staging' }), true);
		assert.equal(accountPagesAlpha({ envName: 'demo' }), true);
		assert.equal(accountPagesAlpha({ envName: 'test' }), true);
	});

	it('is off for production and when no environment is set', () => {
		assert.equal(accountPagesAlpha({ envName: 'production' }), false);
		assert.equal(accountPagesAlpha({}), false);
	});

	it('lets an explicit flag override the environment', () => {
		assert.equal(accountPagesAlpha({ envName: 'production', accountAlpha: 'true' }), true);
		assert.equal(accountPagesAlpha({ envName: 'staging', accountAlpha: 'false' }), false);
		assert.equal(accountPagesAlpha({ accountAlpha: '1' }), true);
		assert.equal(accountPagesAlpha({ accountAlpha: '0' }), false);
	});

	it('is forced on in the released bundle, which has no environment name', () => {
		const release = readFileSync(
			new URL('../../../../.github/workflows/release.yml', import.meta.url),
			'utf8'
		);
		assert.match(release, /VITE_ACCOUNT_ALPHA=true npm run build/);
	});
});
