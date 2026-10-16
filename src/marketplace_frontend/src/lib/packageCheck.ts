/**
 * Whether a picked folder is a package a realm can install. Realms find
 * extensions and codices through the registry's catalog, which skips any
 * namespace without a manifest.json at its root.
 */

export type PackageProblem =
	| { key: 'no_manifest'; values?: undefined }
	| { key: 'nested_manifest'; values: { folder: string } }
	| { key: 'bad_manifest'; values?: undefined }
	| { key: 'id_mismatch'; values: { found: string; expected: string } }
	| { key: 'version_mismatch'; values: { found: string; expected: string } };

export function checkPackage(
	kind: 'ext' | 'codex' | 'assistant',
	id: string,
	version: string,
	paths: string[],
	manifestText: string | null,
): PackageProblem | null {
	if (kind === 'assistant') return null;
	if (manifestText === null) {
		const nested = paths.find((p) => p.split('/').length === 2 && p.endsWith('/manifest.json'));
		if (nested) return { key: 'nested_manifest', values: { folder: nested.split('/')[0] } };
		return { key: 'no_manifest' };
	}
	let manifest: unknown;
	try {
		manifest = JSON.parse(manifestText);
	} catch {
		return { key: 'bad_manifest' };
	}
	if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) return { key: 'bad_manifest' };
	const m = manifest as Record<string, unknown>;
	const found = String((kind === 'codex' ? m.id ?? m.name : m.name) ?? '').trim();
	if (found !== id.trim()) return { key: 'id_mismatch', values: { found: found || '—', expected: id.trim() } };
	const foundVersion = String(m.version ?? '').trim();
	if (foundVersion !== version.trim()) {
		return { key: 'version_mismatch', values: { found: foundVersion || '—', expected: version.trim() } };
	}
	return null;
}
