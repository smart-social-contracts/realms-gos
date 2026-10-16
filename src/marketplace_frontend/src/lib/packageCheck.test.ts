import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { checkPackage } from './packageCheck.ts';

const ext = (m: object) => JSON.stringify({ name: 'my_ext_1', version: '0.1.0', ...m });

describe('checkPackage', () => {
	it('accepts an extension whose manifest names it at this version', () => {
		assert.equal(checkPackage('ext', 'my_ext_1', '0.1.0', ['manifest.json'], ext({})), null);
	});

	it('refuses a folder without a manifest', () => {
		assert.deepEqual(checkPackage('ext', 'my_ext_1', '0.1.0', ['gaas_logo.png'], null), { key: 'no_manifest' });
	});

	it('points at the subfolder when the parent folder was picked', () => {
		assert.deepEqual(checkPackage('ext', 'my_ext_1', '0.1.0', ['my_ext_1/manifest.json', 'x.png'], null), {
			key: 'nested_manifest',
			values: { folder: 'my_ext_1' },
		});
	});

	it('refuses unparsable or non-object manifests', () => {
		assert.equal(checkPackage('ext', 'a', '0.1.0', ['manifest.json'], '{nope')?.key, 'bad_manifest');
		assert.equal(checkPackage('ext', 'a', '0.1.0', ['manifest.json'], '[]')?.key, 'bad_manifest');
	});

	it('refuses a manifest for another id or version', () => {
		assert.deepEqual(checkPackage('ext', 'my_ext_1', '0.1.0', ['manifest.json'], ext({ name: 'voting' })), {
			key: 'id_mismatch',
			values: { found: 'voting', expected: 'my_ext_1' },
		});
		assert.equal(
			checkPackage('ext', 'my_ext_1', '0.1.0', ['manifest.json'], ext({ version: '0.2.0' }))?.key,
			'version_mismatch',
		);
	});

	it('reads a codex id from `id`, falling back to `name`', () => {
		const codex = JSON.stringify({ id: 'agora', name: 'Agora', version: '0.9.6', kind: 'codex' });
		assert.equal(checkPackage('codex', 'agora', '0.9.6', ['manifest.json'], codex), null);
		const legacy = JSON.stringify({ name: 'agora', version: '0.9.6' });
		assert.equal(checkPackage('codex', 'agora', '0.9.6', ['manifest.json'], legacy), null);
	});

	it('does not require a manifest for assistants', () => {
		assert.equal(checkPackage('assistant', 'helper', '0.1.0', ['prompts/system.txt'], null), null);
	});
});
