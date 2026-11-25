#!/usr/bin/env node
/**
 * Re-install extensions from file_registry to copy /ext/ frontend bundles.
 * Grants backend Commit on the frontend canister when needed.
 *
 * Usage:
 *   node scripts/resync-extension-frontends.mjs <backend> [--frontend <id>] [--registry <id>]
 */
import fs from 'node:fs';
import { HttpAgent, Actor } from '@icp-sdk/core/agent';
import { IDL } from '@icp-sdk/core/candid';
import { Principal } from '@icp-sdk/core/principal';
import { Secp256k1KeyIdentity } from '@icp-sdk/core/identity/secp256k1';

const DEFAULT_REGISTRY = 'iebdk-kqaaa-aaaau-agoxq-cai';
const DEFAULT_PEM = '/root/.config/dfx/identity/deployer/identity.pem';

const assetIdl = ({ IDL: idl }) =>
  idl.Service({
    grant_permission: idl.Func(
      [
        idl.Record({
          to_principal: idl.Principal,
          permission: idl.Variant({
            Commit: idl.Null,
            Prepare: idl.Null,
            ManagePermissions: idl.Null,
          }),
        }),
      ],
      [],
      [],
    ),
  });

const realmIdl = ({ IDL: idl }) =>
  idl.Service({
    list_runtime_extensions: idl.Func([], [idl.Text], ['query']),
    install_extension_from_registry: idl.Func([idl.Text], [idl.Text], []),
  });

function parseArgs(argv) {
  const opts = {
    registry: DEFAULT_REGISTRY,
    frontend: null,
    pem: DEFAULT_PEM,
    ext: null,
  };
  const positional = [];
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--registry') opts.registry = argv[++i];
    else if (a === '--frontend') opts.frontend = argv[++i];
    else if (a === '--identity-pem') opts.pem = argv[++i];
    else if (a === '--ext') opts.ext = argv[++i];
    else positional.push(a);
  }
  opts.backend = positional[0];
  if (!opts.backend) throw new Error('backend canister id required');
  return opts;
}

async function main() {
  const opts = parseArgs(process.argv);
  const identity = Secp256k1KeyIdentity.fromPem(fs.readFileSync(opts.pem, 'utf8'));
  console.log('Caller:', identity.getPrincipal().toText());
  console.log('Backend:', opts.backend);

  const agent = new HttpAgent({ host: 'https://icp0.io', identity });
  await agent.fetchRootKey();

  const realm = Actor.createActor(realmIdl, {
    agent,
    canisterId: Principal.fromText(opts.backend),
  });

  const listed = JSON.parse(await realm.list_runtime_extensions());
  const extIds = opts.ext
    ? [opts.ext]
    : listed.runtime_extensions || listed.installed || [];
  const manifests = listed.all_manifests || {};

  let frontend = opts.frontend;
  if (!frontend) {
    const canisters = listed?.canisters || [];
    frontend = canisters.find((c) => c.canister_type === 'frontend')?.canister_id;
  }
  if (!frontend) {
    throw new Error('frontend canister id not found — pass --frontend');
  }
  console.log('Frontend:', frontend);
  console.log('Registry:', opts.registry);

  const asset = Actor.createActor(assetIdl, {
    agent,
    canisterId: Principal.fromText(frontend),
  });
  console.log(`\nGranting Commit on frontend to backend...`);
  await asset.grant_permission({
    to_principal: Principal.fromText(opts.backend),
    permission: { Commit: null },
  });
  console.log('✓ Commit granted\n');

  console.log(`Resyncing ${extIds.length} extension(s)...`);
  let ok = 0;
  let fail = 0;
  for (const extId of extIds) {
    const version = manifests[extId]?.version ?? '?';
    process.stdout.write(`→ ${extId}@${version} ... `);
    const payload = JSON.stringify({
      registry_canister_id: opts.registry,
      ext_id: extId,
      version: null,
      frontend_canister_id: frontend,
    });
    try {
      const raw = await realm.install_extension_from_registry(payload);
      const result = JSON.parse(raw);
      if (result.success) {
        console.log(`✓ (${result.frontend_files_copied ?? '?'} files)`);
        ok++;
      } else {
        console.log(`✗ ${result.error}`);
        fail++;
      }
    } catch (e) {
      console.log(`✗ ${e.message ?? e}`);
      fail++;
    }
  }
  console.log(`\nDone: ${ok} ok, ${fail} failed`);
  if (fail) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
