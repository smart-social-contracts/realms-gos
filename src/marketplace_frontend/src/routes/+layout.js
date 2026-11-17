// Static SPA build — disable prerender for routes that depend on a backend
// canister, but keep the manifest crawler happy with a fallback rule.
export const prerender = false;
export const ssr = false;

// RED-PROOF 2/3: planted build break
import './red-proof-does-not-exist.js';
