import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { PRIVACY_URL, REPORT_URL, TERMS_URL, legalLinks, readCollapsed, showsDemoNotice, writeCollapsed } from './demo-notice.ts';

function memoryStorage(): Storage {
	const items = new Map<string, string>();
	return {
		get length() {
			return items.size;
		},
		clear: () => items.clear(),
		getItem: (key) => items.get(key) ?? null,
		key: (i) => [...items.keys()][i] ?? null,
		removeItem: (key) => void items.delete(key),
		setItem: (key, value) => void items.set(key, String(value)),
	};
}

describe('showsDemoNotice', () => {
	it('is on for staging, its demo alias, test and local', () => {
		for (const host of ['staging.realmsgos.org', 'demo.realmsgos.org', 'test.realmsgos.org', 'localhost']) {
			assert.equal(showsDemoNotice(host), true, host);
		}
	});

	it('is on for a host nobody listed', () => {
		assert.equal(showsDemoNotice('new-env.realmsgos.org'), true);
	});

	it('is off on production only', () => {
		assert.equal(showsDemoNotice('realmsgos.org'), false);
		assert.equal(showsDemoNotice('www.realmsgos.org'), false);
		assert.equal(showsDemoNotice('REALMSGOS.ORG'), false);
	});
});

describe('demo notice links', () => {
	it('point at the demo portal and the public contact', () => {
		assert.equal(PRIVACY_URL, 'https://demo.gos.earth/privacy');
		assert.equal(TERMS_URL, 'https://demo.gos.earth/terms');
		assert.equal(REPORT_URL, 'mailto:contact@realmsgos.org');
	});
});

describe('footer legal links', () => {
	it("point at production's portal on production", () => {
		for (const host of ['realmsgos.org', 'www.realmsgos.org']) {
			assert.deepEqual(legalLinks(host), { privacy: 'https://gos.earth/privacy', terms: 'https://gos.earth/terms' }, host);
		}
	});

	it("point at the demo's portal everywhere else", () => {
		for (const host of ['staging.realmsgos.org', 'demo.realmsgos.org', 'localhost']) {
			assert.deepEqual(legalLinks(host), { privacy: PRIVACY_URL, terms: TERMS_URL }, host);
		}
	});
});

describe('demo notice collapse', () => {
	it('starts expanded and remembers a collapse', () => {
		const storage = memoryStorage();
		assert.equal(readCollapsed(() => storage), false);
		writeCollapsed(() => storage, true);
		assert.equal(readCollapsed(() => storage), true);
		writeCollapsed(() => storage, false);
		assert.equal(readCollapsed(() => storage), false);
	});

	it('stays expanded when storage is blocked', () => {
		const blocked = () => {
			throw new Error('SecurityError');
		};
		assert.equal(readCollapsed(blocked), false);
		assert.doesNotThrow(() => writeCollapsed(blocked, true));
	});
});

describe('demo notice copy', () => {
	const locale = (name: string) =>
		JSON.parse(readFileSync(new URL(`./i18n/locales/${name}.json`, import.meta.url), 'utf8')).demo_notice;

	// The CLFO's text (.ceo/clfo/launch/demo-environment.md, "Banner"), word for word.
	it('is the CLFO banner in English', () => {
		const en = locale('en');
		assert.equal(en.title, 'Demo with simulated data.');
		assert.equal(
			en.body,
			'València here is a simulated city, not a service of the City of València. Amounts in EUR and all tokens ' +
				"are play money with no value. Anything you enter is public and is deleted at the next reset. Please don't " +
				'enter real personal data.'
		);
		assert.deepEqual([en.privacy, en.terms, en.report], ['Privacy', 'Terms', 'Report content']);
	});

	it('is the CLFO banner in Spanish', () => {
		const es = locale('es');
		assert.equal(es.title, 'Demo con datos simulados.');
		assert.equal(
			es.body,
			'Aquí València es una ciudad simulada, no un servicio del Ayuntamiento de València. Los importes en EUR y ' +
				'todos los tokens son dinero de juego sin valor. Todo lo que introduzcas es público y se borra en el ' +
				'próximo reinicio. Por favor, no introduzcas datos personales reales.'
		);
		assert.deepEqual([es.privacy, es.terms, es.report], ['Privacidad', 'Condiciones', 'Denunciar contenido']);
	});
});
