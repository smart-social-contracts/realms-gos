// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import {
	DEFAULT_PRIMARY_COLOR,
	applyBrandingPrimary,
	parsePrimaryColor,
	contrastWithWhite,
	paletteFromPrimary,
	primaryCssVariables,
	reapplyBrandingPrimary
} from './brandingPrimary';

describe('brandingPrimary', () => {
	it('parses valid hex and rejects invalid values', () => {
		expect(parsePrimaryColor('#3B82F6')).toBe('#3b82f6');
		expect(parsePrimaryColor(' #aabbcc ')).toBe('#aabbcc');
		expect(parsePrimaryColor('#abc')).toBeNull();
		expect(parsePrimaryColor('blue')).toBeNull();
		expect(parsePrimaryColor(null)).toBeNull();
	});

	it('builds a palette where 600 is the input when white text reads on it', () => {
		const palette = paletteFromPrimary('#1E6B52');
		expect(palette[600]).toBe('#1e6b52');
		expect(palette[50]).toMatch(/^#[0-9a-f]{6}$/);
		expect(palette[900]).toMatch(/^#[0-9a-f]{6}$/);
	});

	it('darkens a colour too light for white text, and keeps it as the lighter shade', () => {
		for (const light of ['#FF00AA', '#fde047', '#3b82f6', '#ffffff']) {
			const palette = paletteFromPrimary(light);
			expect(contrastWithWhite(palette[600])).toBeGreaterThanOrEqual(4.5);
			expect(contrastWithWhite(palette[700])).toBeGreaterThan(contrastWithWhite(palette[600]));
			expect(palette[500]).toBe(light.toLowerCase());
		}
		expect(contrastWithWhite(DEFAULT_PRIMARY_COLOR)).toBeGreaterThanOrEqual(4.5);
	});

	it('falls back to the default color for invalid input', () => {
		const palette = paletteFromPrimary('not-a-color');
		expect(palette[600]).toBe(DEFAULT_PRIMARY_COLOR);
	});

	it('exports css variables for every primary shade, as a colour and as "r g b"', () => {
		const vars = primaryCssVariables('#2563eb');
		expect(vars['--color-primary-600']).toBe('#2563eb');
		expect(vars['--color-primary-600-rgb']).toBe('37 99 235');
		expect(Object.keys(vars)).toHaveLength(20);
	});

	it('applyBrandingPrimary is safe without document', () => {
		const originalDocument = globalThis.document;
		// @ts-expect-error test shim
		delete globalThis.document;
		expect(applyBrandingPrimary('#2563eb')['--color-primary-600']).toBe('#2563eb');
		globalThis.document = originalDocument;
	});

	it('applyBrandingPrimary sets variables on documentElement', () => {
		const vars = applyBrandingPrimary('#dc2626');
		expect(document.documentElement.style.getPropertyValue('--color-primary-600')).toBe('#dc2626');
		expect(vars['--color-primary-700']).toBeTruthy();
	});

	it('puts the realm\'s colour back after the theme has overwritten the variables', () => {
		applyBrandingPrimary('#1e6b52');
		document.documentElement.style.setProperty('--color-primary-600', '#525252'); // what the theme's start-up writes
		reapplyBrandingPrimary();
		expect(document.documentElement.style.getPropertyValue('--color-primary-600')).toBe('#1e6b52');
	});
});

