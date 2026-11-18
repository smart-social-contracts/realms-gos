/** The colour of a realm that has not chosen one. White text reads on it at 5.2:1. */
export const DEFAULT_PRIMARY_COLOR = '#2563eb';

const PRIMARY_COLOR_RE = /^#[0-9a-fA-F]{6}$/;

export type PrimaryPalette = Record<'50' | '100' | '200' | '300' | '400' | '500' | '600' | '700' | '800' | '900', string>;

const SHADE_KEYS = ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900'] as const;

const LIGHT_MIX: Record<'50' | '100' | '200' | '300' | '400' | '500', number> = {
	50: 0.95,
	100: 0.9,
	200: 0.75,
	300: 0.6,
	400: 0.4,
	500: 0.2
};

const DARK_MIX: Record<'700' | '800' | '900', number> = {
	700: 0.2,
	800: 0.4,
	900: 0.6
};

function hexToRgb(hex: string): { r: number; g: number; b: number } {
	return {
		r: parseInt(hex.slice(1, 3), 16),
		g: parseInt(hex.slice(3, 5), 16),
		b: parseInt(hex.slice(5, 7), 16)
	};
}

function rgbToHex(r: number, g: number, b: number): string {
	const clamp = (value: number) => Math.max(0, Math.min(255, Math.round(value)));
	return `#${[clamp(r), clamp(g), clamp(b)]
		.map((value) => value.toString(16).padStart(2, '0'))
		.join('')}`;
}

function mixWithWhite(hex: string, amount: number): string {
	const { r, g, b } = hexToRgb(hex);
	return rgbToHex(r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount);
}

function mixWithBlack(hex: string, amount: number): string {
	const { r, g, b } = hexToRgb(hex);
	return rgbToHex(r * (1 - amount), g * (1 - amount), b * (1 - amount));
}

export function parsePrimaryColor(value: unknown): string | null {
	if (typeof value !== 'string') return null;
	const trimmed = value.trim();
	if (!PRIMARY_COLOR_RE.test(trimmed)) return null;
	return trimmed.toLowerCase();
}

function luminance(hex: string): number {
	const { r, g, b } = hexToRgb(hex);
	const linear = (value: number) => {
		const c = value / 255;
		return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
	};
	return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

/** WCAG contrast of ``hex`` against white: 4.5 is what text needs. */
export function contrastWithWhite(hex: string): number {
	return 1.05 / (luminance(hex) + 0.05);
}

/**
 * The realm's colour as a control's fill: itself when white text reads on it (4.5:1), else darkened just enough.
 * Every screen writes white on shade 600, so a realm that picks a light colour must not get unreadable buttons.
 */
export function solidFromPrimary(hex: string): string {
	const base = parsePrimaryColor(hex) ?? DEFAULT_PRIMARY_COLOR;
	for (let amount = 0; amount <= 1; amount += 0.04) {
		const shade = mixWithBlack(base, amount);
		if (contrastWithWhite(shade) >= 4.5) return shade;
	}
	return '#000000';
}

/**
 * The shades of a realm's colour. 600 is the fill of a control (see ``solidFromPrimary``): the realm's colour itself
 * unless that is too light for white text. Lighter shades are tints of the realm's colour, darker ones of the fill.
 */
export function paletteFromPrimary(hex: string): PrimaryPalette {
	const base = parsePrimaryColor(hex) ?? DEFAULT_PRIMARY_COLOR;
	const solid = solidFromPrimary(base);
	return {
		50: mixWithWhite(base, LIGHT_MIX[50]),
		100: mixWithWhite(base, LIGHT_MIX[100]),
		200: mixWithWhite(base, LIGHT_MIX[200]),
		300: mixWithWhite(base, LIGHT_MIX[300]),
		400: mixWithWhite(base, LIGHT_MIX[400]),
		500: solid === base ? mixWithWhite(base, LIGHT_MIX[500]) : base,
		600: solid,
		700: mixWithBlack(solid, DARK_MIX[700]),
		800: mixWithBlack(solid, DARK_MIX[800]),
		900: mixWithBlack(solid, DARK_MIX[900])
	};
}

/** Each shade twice: as a colour, and as "r g b" for the stylesheet's see-through variants (``bg-primary-600/40``). */
export function primaryCssVariables(hex: string): Record<string, string> {
	const palette = paletteFromPrimary(hex);
	const vars: Record<string, string> = {};
	for (const shade of SHADE_KEYS) {
		vars[`--color-primary-${shade}`] = palette[shade];
	}
	for (const shade of SHADE_KEYS) {
		const { r, g, b } = hexToRgb(palette[shade]);
		vars[`--color-primary-${shade}-rgb`] = `${r} ${g} ${b}`;
	}
	return vars;
}

/** Where the realm's colour is remembered between visits (app.html reads it). */
export const PRIMARY_VARS_KEY = 'realm_primary_vars';

// The realm's colour once it is known; the default until then.
let applied = DEFAULT_PRIMARY_COLOR;

export function applyBrandingPrimary(hex: string): Record<string, string> {
	applied = parsePrimaryColor(hex) ?? DEFAULT_PRIMARY_COLOR;
	const vars = primaryCssVariables(applied);
	if (typeof document === 'undefined') return vars;
	const root = document.documentElement;
	for (const [key, value] of Object.entries(vars)) {
		root.style.setProperty(key, value);
	}
	// Remembered for the next visit: app.html sets these before the first paint, so the loading mark is in the
	// realm's colour and the page does not start in the default one.
	try {
		localStorage.setItem(PRIMARY_VARS_KEY, JSON.stringify(vars));
	} catch {
		// storage unavailable: the next visit starts in the default colour
	}
	return vars;
}

/**
 * Put the realm's colour back after something else has written the primary variables. The theme's own start-up
 * writes a neutral grey scale over them, and it runs after the realm's colour has arrived: without this, every
 * control that follows the theme came out dark grey.
 */
export function reapplyBrandingPrimary(): Record<string, string> {
	return applyBrandingPrimary(applied);
}

export function readPrimaryCssVariables(): Record<string, string> {
	if (typeof document === 'undefined') return {};
	const root = document.documentElement;
	const style = getComputedStyle(root);
	const vars: Record<string, string> = {};
	for (const shade of SHADE_KEYS) {
		const name = `--color-primary-${shade}`;
		const value = style.getPropertyValue(name).trim();
		if (value) vars[name] = value;
	}
	return vars;
}
