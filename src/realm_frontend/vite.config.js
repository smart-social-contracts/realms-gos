import { fileURLToPath, URL } from 'url';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import environment from 'vite-plugin-environment';
import dotenv from 'dotenv';
import { execSync } from 'child_process';
import { readFileSync } from 'fs';

dotenv.config({ path: '../../.env' });

// Get build-time values for local development
function getBuildTimeValues() {
  let version = 'dev';
  let commitHash = 'local';
  const buildTime = new Date().toISOString().replace('T', ' ').substring(0, 19);
  
  try {
    version = readFileSync('../../version.txt', 'utf-8').trim();
  } catch (e) {
    // version.txt not found, use default
  }
  
  try {
    commitHash = execSync('git rev-parse --short HEAD', { encoding: 'utf-8' }).trim();
  } catch (e) {
    // git not available, use default
  }
  
  return { version, commitHash, buildTime };
}

const buildValues = getBuildTimeValues();

/** True when the bundle may include II-bypass / deterministic test identities. */
function realmsTestBuild(command) {
  if (process.env.REALMS_BUILD_VARIANT === 'test') return true;
  // `npm run dev` / `vite dev` — keep test identities available locally.
  if (command === 'serve') return true;
  return false;
}

export default defineConfig(({ command }) => {
  const testBuild = realmsTestBuild(command);

  return {
  build: {
    emptyOutDir: true,
    rollupOptions: {
      // Comment out external dependencies for development
      // external: [
      //   '@dfinity/agent',
      //   '@dfinity/principal',
      //   '@dfinity/auth-client',
      //   '@dfinity/identity',
      //   '@dfinity/candid'
      // ]
    },
    // Minimize build output
    minify: true,
    reportCompressedSize: false,
  },
  logLevel: 'error', // Only show errors
  define: {
    '__BUILD_VERSION__': JSON.stringify(buildValues.version),
    '__BUILD_COMMIT__': JSON.stringify(buildValues.commitHash),
    '__BUILD_TIME__': JSON.stringify(buildValues.buildTime),
    '__REALMS_TEST_BUILD__': testBuild,
  },
  optimizeDeps: {
    esbuildOptions: {
      define: {
        global: "globalThis",
        __REALMS_TEST_BUILD__: String(testBuild),
      },
    },
    include: [
      '@icp-sdk/core/agent',
      '@icp-sdk/core/principal',
      '@icp-sdk/auth/client',
      '@icp-sdk/core/identity',
      '@icp-sdk/core/candid'
    ]
  },
  plugins: [
    sveltekit(),
    environment("all", { prefix: "CANISTER_" }),
    environment("all", { prefix: "DFX_" }),
  ],
  resolve: {
    alias: [
      {
        find: "declarations",
        replacement: fileURLToPath(
          new URL("../declarations", import.meta.url)
        ),
      },
    ],
    dedupe: ['@icp-sdk/core'],
  },
  server: {
    fs: {
      allow: [
        'assets/images', // Add this directory
      ],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:4943',
        changeOrigin: true,
      },
    },
  },
};
});
