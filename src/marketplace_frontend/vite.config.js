import { fileURLToPath, URL } from 'url';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import environment from 'vite-plugin-environment';
import dotenv from 'dotenv';
import { execSync } from 'child_process';
import { readFileSync } from 'fs';

dotenv.config({ path: '../../.env' });

function getBuildTimeValues() {
  let version = 'dev';
  let commitHash = 'local';
  const buildTime = new Date().toISOString().replace('T', ' ').substring(0, 19);
  try {
    version = readFileSync('../../version.txt', 'utf-8').trim();
  } catch (e) { /* no version.txt */ }
  try {
    commitHash = execSync('git rev-parse --short HEAD', { encoding: 'utf-8' }).trim();
  } catch (e) { /* no git */ }
  return { version, commitHash, buildTime };
}

const buildValues = getBuildTimeValues();

export default defineConfig({
  build: { emptyOutDir: true },
  define: {
    __BUILD_VERSION__: JSON.stringify(buildValues.version),
    __BUILD_COMMIT__: JSON.stringify(buildValues.commitHash),
    __BUILD_TIME__: JSON.stringify(buildValues.buildTime),
  },
  optimizeDeps: {
    esbuildOptions: { define: { global: 'globalThis' } },
  },
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:4943', changeOrigin: true },
    },
  },
  plugins: [
    sveltekit(),
    environment('all', { prefix: 'CANISTER_' }),
    environment('all', { prefix: 'DFX_' }),
    environment('all', { prefix: 'VITE_' }),
    // Plan §4.3.1: BILLING_SERVICE_URL — bare name, not VITE_-prefixed.
    // Default to empty string so unset envs don't crash the build; the
    // CONFIG.billing_service_url helper in src/lib/config.ts then falls
    // back to the production URL.
    environment({ BILLING_SERVICE_URL: '' }, { defineOn: 'process.env' }),
  ],
  resolve: {
    alias: [
      {
        find: 'declarations',
        replacement: fileURLToPath(new URL('../declarations', import.meta.url)),
      },
    ],
    dedupe: ['@icp-sdk/core'],
  },
});
