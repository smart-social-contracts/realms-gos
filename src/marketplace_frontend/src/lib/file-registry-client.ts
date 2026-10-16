// Browser-side client for the file_registry canister.
//
// Two responsibilities:
//   1. Build URLs to fetch published files over HTTP (browser → registry).
//   2. Upload a folder of files in chunks (browser → registry update calls).
//
// The registry's chunked-upload protocol:
//   - store_file_chunk({namespace, path, chunk_index, total_chunks, data_b64, content_type})
//     Each chunk should be ≤128 KB to stay under the per-message
//     instruction budget. Call N times for an N-chunk file.
//   - finalize_chunked_file_step({namespace, path, expected_sha256, batch_size})
//     Call repeatedly with batch_size=1 until {done: true}. The first
//     call also fixes the file's expected SHA-256 from the browser's
//     pre-computed hash.
//
// All uploads use the chunked path (~7x cheaper per KB than store_file on the
// WASI Python registry; see scripts/BENCHMARK_RESULTS.md).

import { Actor, HttpAgent } from '@dfinity/agent';
import { Principal } from '@dfinity/principal';
import { IDL } from '@dfinity/candid';

import { getIdentity } from './auth';
import { CONFIG } from './config';
import { screenshots as parseScreenshots } from './format';

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

export function fileRegistryBaseUrl(canisterId: string): string {
  if (typeof window === 'undefined') return `https://${canisterId}.icp0.io`;
  const host = window.location.host;
  const isLocal = host.includes('localhost') || host.includes('127.0.0.1');
  if (isLocal) {
    const port = host.split(':')[1] ?? '4943';
    return `http://${canisterId}.localhost:${port}`;
  }
  return `https://${canisterId}.icp0.io`;
}

export function fileUrl(canisterId: string, namespace: string, path: string): string {
  const cleanPath = path.replace(/^\/+/, '');
  return `${fileRegistryBaseUrl(canisterId)}/${namespace}/${cleanPath}`;
}

/** Builtin catalog rows leave canister id empty; use the env's registry. */
export function listingScreenshotUrls(item: {
  screenshots?: string;
  file_registry_canister_id?: string;
  file_registry_namespace?: string;
}): string[] {
  const canister = item.file_registry_canister_id || CONFIG.file_registry_canister_id;
  const ns = item.file_registry_namespace || '';
  if (!canister || !ns) return [];
  return parseScreenshots(item.screenshots ?? '').map((p) => fileUrl(canister, ns, p));
}

export interface RegistryFile {
  path: string;
  size: number;
  content_type: string;
  sha256: string;
  updated: number;
}

export async function listFiles(canisterId: string, namespace: string): Promise<RegistryFile[]> {
  const url = `${fileRegistryBaseUrl(canisterId)}/${namespace}`;
  const resp = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} from ${url}`);
  return (await resp.json()) as RegistryFile[];
}

// ---------------------------------------------------------------------------
// Actor for file_registry update calls (chunked upload)
// ---------------------------------------------------------------------------

const fileRegistryIdlFactory = ({ IDL: idl }: { IDL: typeof IDL }) =>
  idl.Service({
    store_file_chunk: idl.Func([idl.Text], [idl.Text], []),
    finalize_chunked_file_step: idl.Func([idl.Text], [idl.Text], []),
    finalize_chunked_file: idl.Func([idl.Text], [idl.Text], []),
    publish_namespace: idl.Func([idl.Text], [idl.Text], []),
    list_files: idl.Func([idl.Text], [idl.Text], ['query']),
    list_namespaces: idl.Func([], [idl.Text], ['query']),
  });

async function getRegistryActor(canisterId: string) {
  const identity = await getIdentity();
  const agent = new HttpAgent(identity ? { identity } : {});
  if (typeof window !== 'undefined' && (window.location.hostname.includes('localhost') || window.location.hostname.includes('127.0.0.1'))) {
    try {
      await agent.fetchRootKey();
    } catch (e) {
      console.warn('fetchRootKey failed:', e);
    }
  }
  return Actor.createActor(fileRegistryIdlFactory, {
    agent,
    canisterId: Principal.fromText(canisterId),
  });
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

const CHUNK_BYTES = 64 * 1024;

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + chunk)));
  }
  return typeof btoa === 'function' ? btoa(binary) : Buffer.from(binary, 'binary').toString('base64');
}

async function sha256HexFromBlob(file: File | Blob): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest('SHA-256', buf);
  const bytes = new Uint8Array(digest);
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('');
}

function guessContentType(path: string, fileType: string | undefined): string {
  if (fileType) return fileType;
  const lower = path.toLowerCase();
  if (lower.endsWith('.py')) return 'text/plain';
  if (lower.endsWith('.js') || lower.endsWith('.mjs')) return 'application/javascript';
  if (lower.endsWith('.json')) return 'application/json';
  if (lower.endsWith('.html')) return 'text/html';
  if (lower.endsWith('.css')) return 'text/css';
  if (lower.endsWith('.svg')) return 'image/svg+xml';
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
  if (lower.endsWith('.webp')) return 'image/webp';
  if (lower.endsWith('.wasm')) return 'application/wasm';
  if (lower.endsWith('.md') || lower.endsWith('.txt')) return 'text/plain';
  return 'application/octet-stream';
}

export interface UploadOnePayload {
  namespace: string;
  path: string;
  file: File | Blob;
}

export interface UploadProgress {
  path: string;
  uploaded: number;
  total: number;
  status: 'queued' | 'hashing' | 'uploading' | 'finalizing' | 'done' | 'error';
  error?: string;
}

export type ProgressCallback = (p: UploadProgress) => void;

/** Upload one file (small or chunked) to a file_registry namespace. */
export async function uploadFile(
  canisterId: string,
  payload: UploadOnePayload,
  onProgress?: ProgressCallback,
): Promise<void> {
  const { namespace, path, file } = payload;
  const total = file.size;
  const ct = guessContentType(path, (file as File).type);
  const actor = await getRegistryActor(canisterId);

  onProgress?.({ path, uploaded: 0, total, status: 'queued' });
  onProgress?.({ path, uploaded: 0, total, status: 'hashing' });
  const sha256 = await sha256HexFromBlob(file);
  const totalChunks = Math.max(1, Math.ceil(total / CHUNK_BYTES));

  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_BYTES;
    const end = Math.min(start + CHUNK_BYTES, total);
    const slice = file.slice(start, end);
    const buf = await slice.arrayBuffer();
    const args = JSON.stringify({
      namespace,
      path,
      chunk_index: i,
      total_chunks: totalChunks,
      data_b64: arrayBufferToBase64(buf),
      content_type: ct,
    });
    const resp = await (actor as any).store_file_chunk(args);
    const parsed = JSON.parse(String(resp));
    if (parsed?.error) throw new Error(`store_file_chunk[${i}]: ${parsed.error}`);
    onProgress?.({ path, uploaded: Math.min(end, total), total, status: 'uploading' });
  }

  // Finalize step-by-step. Send expected_sha256 only on the first call;
  // it's cached in the registry's pending state. Registries without the
  // step method (ic-file-registry ≥ 0.1) assemble while chunks arrive and
  // take one finalize_chunked_file call instead.
  onProgress?.({ path, uploaded: total, total, status: 'finalizing' });
  let firstCall = true;
  while (true) {
    const args = JSON.stringify(
      firstCall
        ? { namespace, path, expected_sha256: sha256, batch_size: 1 }
        : { namespace, path, batch_size: 1 },
    );
    let resp: unknown;
    try {
      resp = await (actor as any).finalize_chunked_file_step(args);
    } catch (e) {
      if (!firstCall || !isMissingMethod(e)) throw e;
      resp = await (actor as any).finalize_chunked_file(JSON.stringify({ namespace, path, sha256 }));
      const parsed = JSON.parse(String(resp));
      if (parsed?.error) throw new Error(`finalize: ${parsed.error}`);
      break;
    }
    firstCall = false;
    const parsed = JSON.parse(String(resp));
    if (parsed?.error) throw new Error(`finalize: ${parsed.error}`);
    if (parsed?.done) break;
  }

  onProgress?.({ path, uploaded: total, total, status: 'done' });
}

export interface UploadFolderItem {
  /** Path within the namespace, e.g. "manifest.json" or "backend/entry.py". */
  path: string;
  file: File | Blob;
}

/** Upload a folder of files into a single namespace, then publish it. */
export async function uploadAndPublish(
  canisterId: string,
  namespace: string,
  files: UploadFolderItem[],
  onProgress?: ProgressCallback,
): Promise<void> {
  for (const item of files) {
    await uploadFile(canisterId, { namespace, path: item.path, file: item.file }, onProgress);
  }
  const actor = await getRegistryActor(canisterId);
  let resp: unknown;
  try {
    resp = await (actor as any).publish_namespace(JSON.stringify({ namespace }));
  } catch (e) {
    // Registries without publish_namespace serve stored files right away.
    if (isMissingMethod(e)) return;
    throw e;
  }
  const parsed = JSON.parse(String(resp));
  if (parsed?.error) throw new Error(`publish_namespace: ${parsed.error}`);
}

/** The canister rejected the call because it has no such method (IC0536). */
export function isMissingMethod(e: unknown): boolean {
  const msg = String((e as any)?.message ?? e).toLowerCase();
  return msg.includes('ic0536') || msg.includes('has no update method') || msg.includes('has no query method');
}
