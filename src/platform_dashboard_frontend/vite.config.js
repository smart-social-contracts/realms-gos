import { readFileSync, existsSync } from 'fs';
import { fileURLToPath, URL } from 'url';
import { join, dirname } from 'path';
import { execSync } from 'child_process';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import environment from 'vite-plugin-environment';
import dotenv from 'dotenv';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(__dirname, '..', '..');

dotenv.config({ path: join(repoRoot, '.env') });

function getBuildTimeValues() {
  let version = 'dev';
  let commitHash = 'local';
  const buildTime = new Date().toISOString().replace('T', ' ').substring(0, 19);
  try { version = readFileSync(join(repoRoot, 'version.txt'), 'utf-8').trim(); } catch {}
  try { commitHash = execSync('git rev-parse --short HEAD', { encoding: 'utf-8' }).trim(); } catch {}
  return { version, commitHash, buildTime };
}

const buildValues = getBuildTimeValues();

/**
 * CI and `dfx deploy` often run without a populated .env. Declarations expect
 * process.env.CANISTER_ID_* at bundle time — load from canister_ids.json.
 *
 * Use DFX_NETWORK / IC_ASSET_BUILD_NETWORK to pick the key (e.g. staging).
 * The browser bundle always talks to IC mainnet hosts, so DFX_NETWORK is
 * forced to `ic` after this (see below) so agents do not call fetchRootKey().
 */
function injectCanisterIdsFromRepo() {
  const network =
    process.env.IC_ASSET_BUILD_NETWORK ||
    process.env.DFX_NETWORK ||
    'staging';
  const path = join(repoRoot, 'canister_ids.json');
  if (!existsSync(path)) return;
  let data;
  try {
    data = JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return;
  }
  const pick = (name) => {
    const entry = data[name];
    if (!entry || typeof entry !== 'object') return '';
    return (
      entry[network] ||
      entry.ic ||
      entry.demo ||
      ''
    );
  };
  const pairs = [
    ['CANISTER_ID_REALM_REGISTRY_BACKEND', 'realm_registry_backend'],
    ['CANISTER_ID_REALM_REGISTRY_FRONTEND', 'realm_registry_frontend'],
    ['CANISTER_ID_REALM_INSTALLER', 'realm_installer'],
    ['CANISTER_ID_FILE_REGISTRY', 'file_registry'],
    ['CANISTER_ID_FILE_REGISTRY_FRONTEND', 'file_registry_frontend'],
    ['CANISTER_ID_MARKETPLACE_BACKEND', 'marketplace_backend'],
    ['CANISTER_ID_MARKETPLACE_FRONTEND', 'marketplace_frontend'],
  ];
  for (const [envKey, jsonName] of pairs) {
    if (!process.env[envKey]) {
      const id = pick(jsonName);
      if (id) process.env[envKey] = id;
    }
  }
  const instFe = pick('realm_installer_frontend');
  if (instFe && !process.env.CANISTER_ID_REALM_INSTALLER_FRONTEND) {
    process.env.CANISTER_ID_REALM_INSTALLER_FRONTEND = instFe;
  }
}

injectCanisterIdsFromRepo();
process.env.DFX_NETWORK = 'ic';

export default defineConfig({
  build: { emptyOutDir: true },
  define: {
    '__BUILD_VERSION__': JSON.stringify(buildValues.version),
    '__BUILD_COMMIT__': JSON.stringify(buildValues.commitHash),
    '__BUILD_TIME__': JSON.stringify(buildValues.buildTime),
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
