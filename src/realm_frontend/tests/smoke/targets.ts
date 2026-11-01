import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// "portal" and "marketplace" are whole sites; a "realm" is one realm page of a portal.
export type Target = {
	name: string;
	kind: 'portal' | 'marketplace' | 'realm';
	url: string;
};

const DEFAULT_TARGETS_FILE = join(dirname(fileURLToPath(import.meta.url)), 'targets.json');

// SMOKE_TARGETS_FILE points the same checks at another set of sites (test.*, demo.*).
export function loadTargets(): Target[] {
	const file = process.env.SMOKE_TARGETS_FILE || DEFAULT_TARGETS_FILE;
	return JSON.parse(readFileSync(file, 'utf-8'));
}
