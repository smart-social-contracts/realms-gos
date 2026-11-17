import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { runtimeCanisterId } from './config.ts';

// /canister_ids.js is what Casals writes at deploy time; the build env is empty
// under Casals, so every canister id the SPA needs must come from here.
describe('runtimeCanisterId', () => {
  it('reads the id the orchestrator wrote', () => {
    assert.equal(
      runtimeCanisterId('marketplace_backend', { marketplace_backend: 'gudgy-daaaa-aaaap-quz2a-cai' }),
      'gudgy-daaaa-aaaap-quz2a-cai'
    );
  });

  it('is empty when the key, the object or the value is missing', () => {
    assert.equal(runtimeCanisterId('marketplace_backend', {}), '');
    assert.equal(runtimeCanisterId('marketplace_backend', undefined), '');
    assert.equal(runtimeCanisterId('marketplace_backend', { marketplace_backend: '  ' }), '');
    assert.equal(runtimeCanisterId('marketplace_backend', { marketplace_backend: 42 }), '');
  });
});
