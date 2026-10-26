import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, 'TestFlagsModal.svelte'), 'utf8');

describe('TestFlagsModal layout', () => {
	it('caps the dialog to the iframe viewport and pins chrome', () => {
		expect(source).toContain('max-h-[min(90dvh,40rem)]');
		expect(source).toContain('flex flex-col');
		expect(source).toContain('classBody="min-h-0 overflow-y-auto"');
		expect(source).toContain('classHeader="shrink-0"');
		expect(source).toContain('classFooter="shrink-0"');
	});

	it('keeps notice editors behind a disclosure', () => {
		expect(source).toContain('aria-expanded={noticeOpen}');
		expect(source).toContain('aria-expanded={translationsOpen}');
		expect(source).toContain('noticeTranslationSlots');
	});

	it('uses compact flag rows with the hint on the row', () => {
		expect(source).toContain('title={flag.hint}');
		expect(source).toContain('flex items-center justify-between');
		expect(source).not.toMatch(/items-start justify-between gap-4/);
	});

	it('saves only test-only keys through realm_configure', () => {
		expect(source).toContain("run('realm_configure'");
		expect(source).toContain("test_flags: testOnlyFlags()");
		expect(source).toContain('test_mode: !!values.test_mode');
		expect(source).toContain("'ii_bypass'");
		expect(source).toContain("'demo_data'");
		expect(source).toContain("'skip_terms'");
		expect(source).toContain("'skip_passport_zkproof'");
		expect(source).not.toMatch(/test_flags:\s*\{\s*\.\.\.values/);
		expect(source).not.toContain('demo_notice_body: noticeBodies');
	});
});
