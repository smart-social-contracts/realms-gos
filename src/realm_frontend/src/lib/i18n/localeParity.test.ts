import { describe, expect, it } from 'vitest';
import en from './locales/en.json';
import es from './locales/es.json';
import de from './locales/de.json';
import fr from './locales/fr.json';
import itLocale from './locales/it.json';
import zhCN from './locales/zh-CN.json';
import loc_ca from './locales/ca.json';
import loc_eu from './locales/eu.json';
import loc_gl from './locales/gl.json';
import loc_rm from './locales/rm.json';
import loc_zh_HK from './locales/zh-HK.json';
import caValencia from './locales/ca-valencia.json';

type JsonValue = string | number | boolean | null | JsonObject | JsonValue[];
interface JsonObject {
	[key: string]: JsonValue;
}

function flattenLeafKeys(obj: JsonObject, prefix = ''): string[] {
	const keys: string[] = [];
	for (const [key, value] of Object.entries(obj)) {
		const path = prefix ? `${prefix}.${key}` : key;
		if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
			keys.push(...flattenLeafKeys(value as JsonObject, path));
		} else {
			keys.push(path);
		}
	}
	return keys.sort();
}

const enKeys = flattenLeafKeys(en as JsonObject);

const locales: Record<string, JsonObject> = {
	es: es as JsonObject,
	de: de as JsonObject,
	fr: fr as JsonObject,
	it: itLocale as JsonObject,
	'zh-CN': zhCN as JsonObject,
	'ca-valencia': caValencia as JsonObject,
	'zh-HK': loc_zh_HK as JsonObject,
	'rm': loc_rm as JsonObject,
	'gl': loc_gl as JsonObject,
	'eu': loc_eu as JsonObject,
	'ca': loc_ca as JsonObject,
};

describe('locale parity', () => {
	for (const [localeId, messages] of Object.entries(locales)) {
		it(`includes every en.json leaf key in ${localeId}.json`, () => {
			const localeKeys = new Set(flattenLeafKeys(messages));
			const missing = enKeys.filter((key) => !localeKeys.has(key));
			expect(missing, `missing keys in ${localeId}`).toEqual([]);
		});
	}
});
