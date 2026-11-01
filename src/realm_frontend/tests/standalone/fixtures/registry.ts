// Something newer in the realm's file registry, for the proposals that upgrade what is installed.
import { execFileSync } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { StandaloneRealm } from './realm';

/** The repo's own checkouts of the extensions and the codices, which the local registry was filled from. */
const PACKAGES_DIR = join(process.cwd(), '..', '..');

/** Publishes the repo package at `dir` (e.g. `extensions/extensions/hello_world`) to the realm's file registry one
 * patch version above `installed`, the version the realm runs; returns that version. */
export function publishNextVersion(realm: StandaloneRealm, dir: string, installed: string): string {
	if (!realm.identity) throw new Error('the realm file names no identity to publish with');
	const manifestFile = (root: string) => join(root, 'manifest.json');
	const manifest = JSON.parse(readFileSync(manifestFile(join(PACKAGES_DIR, dir)), 'utf8'));
	const [major, minor, patch] = installed.split('.');
	const version = `${major}.${minor}.${Number(patch) + 1}`;
	// The manifest inside the package carries the version the realm installs, so the copy's must change too.
	const copy = join(mkdtempSync(join(tmpdir(), 'e2e-registry-')), 'package');
	cpSync(join(PACKAGES_DIR, dir), copy, { recursive: true });
	writeFileSync(manifestFile(copy), JSON.stringify({ ...manifest, version }, null, 2));
	execFileSync(
		'realms',
		[
			'extension',
			'publish',
			'--source-dir',
			copy,
			'--registry',
			realm.file_registry,
			'--version',
			version,
			'--network',
			realm.network_url,
			'--identity',
			realm.identity
		],
		{ stdio: 'inherit' }
	);
	return version;
}
