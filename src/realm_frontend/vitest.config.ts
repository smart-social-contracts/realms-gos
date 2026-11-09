import { fileURLToPath } from 'node:url';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

// Lightweight unit-test config (no SvelteKit plugin) for plain TS modules such as the crypto sharing helpers,
// and for the Svelte components whose tests mount them in jsdom ($app/stores and $app/environment are stubs).
export default defineConfig({
	plugins: [svelte(), svelteTesting()],
	resolve: {
		alias: {
			$lib: fileURLToPath(new URL('./src/lib', import.meta.url)),
			'$app/stores': fileURLToPath(new URL('./src/lib/test-stubs/app-stores.ts', import.meta.url)),
			'$app/environment': fileURLToPath(new URL('./src/lib/test-stubs/app-environment.ts', import.meta.url))
		}
	},
	define: {
		__REALMS_TEST_BUILD__: true
	},
	test: {
		environment: 'node',
		setupFiles: ['./src/lib/test-stubs/setup.ts'],
		include: ['src/**/*.test.ts']
	}
});
