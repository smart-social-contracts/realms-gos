import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { CONFIG, runtimeSetting } from './config.ts';

// /canister_ids.js is what Casals writes at deploy time; the build env is empty
// under Casals, so every canister id the SPA needs must come from here.
describe('runtimeSetting', () => {
  it('reads the id the orchestrator wrote', () => {
    assert.equal(
      runtimeSetting('marketplace_backend', { marketplace_backend: 'gudgy-daaaa-aaaap-quz2a-cai' }),
      'gudgy-daaaa-aaaap-quz2a-cai'
    );
  });

  it('reads the forum URL the orchestrator wrote', () => {
    assert.equal(
      runtimeSetting('forum_url', { forum_url: 'https://forum.realmsgos.org' }),
      'https://forum.realmsgos.org'
    );
  });

  it('is empty when the key, the object or the value is missing', () => {
    assert.equal(runtimeSetting('marketplace_backend', {}), '');
    assert.equal(runtimeSetting('marketplace_backend', undefined), '');
    assert.equal(runtimeSetting('marketplace_backend', { marketplace_backend: '  ' }), '');
    assert.equal(runtimeSetting('marketplace_backend', { marketplace_backend: 42 }), '');
  });
});

// The forum is deployed on its own schedule: a dist with no forum_url in its
// canister_ids.js must not link to it.
describe('CONFIG.forum_url', () => {
  it('is empty when canister_ids.js does not name a forum', () => {
    assert.equal(CONFIG.forum_url, '');
  });
});
