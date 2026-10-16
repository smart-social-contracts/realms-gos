<script lang="ts">import { onMount } from "svelte";
import { _ } from "svelte-i18n";
import { page } from "$app/stores";
import { goto } from "$app/navigation";
import Spinner from "$lib/components/Spinner.svelte";
import { isAuthenticated, principalStore } from "$lib/auth";
import { uploadAndPublish } from "$lib/file-registry-client";
import { marketplaceClient, type ItemKind } from "$lib/marketplace-client";
import { checkPackage, type PackageProblem } from "$lib/packageCheck";
let step = 1;
let kind: ItemKind = "ext";
let id = "";
let realmType = "";
let name = "";
let description = "";
let version = "0.1.0";
let priceUsdCents = 0;
let icon = "";
let categoriesStr = "other";
let extLanguages = "en";
let runtime = "openai";
let endpointUrl = "";
let baseModel = "";
let requestedRole = "";
let requestedPermissions = "";
let domains = "governance";
let languages = "en";
let trainingDataSummary = "";
let evalReportUrl = "";
let pricingSummary = "";
let pickedFiles = [];
let baseFolder = "";
let license = null;
let licenseLoaded = false;
let checking = false;
let checkError = "";
let uploadProgress = {};
let uploading = false;
let uploadError = "";
let listingHref = "";
let manifestScreenshots = "";
let packageProblem: PackageProblem | null = null;
$: if (kind === "ext" && id.includes("/")) id = id.replace(/\//g, "_");
const dirInputAttrs = { webkitdirectory: "", directory: "" };
onMount(async () => {
  const prefill = $page.url.searchParams.get("prefill");
  if (!prefill) return;
  id = prefill;
  try {
    const a = await marketplaceClient.getAssistantDetails(prefill);
    if (a) {
      kind = "assistant";
      name = a.name;
      description = a.description;
      version = a.version;
      icon = a.icon;
      categoriesStr = a.categories;
      runtime = a.runtime;
      endpointUrl = a.endpoint_url;
      baseModel = a.base_model;
      requestedRole = a.requested_role;
      requestedPermissions = a.requested_permissions;
      domains = a.domains;
      languages = a.languages;
      trainingDataSummary = a.training_data_summary;
      evalReportUrl = a.eval_report_url;
      pricingSummary = a.pricing_summary;
      return;
    }
  } catch {
  }
  if (prefill.includes("/")) {
    try {
      const c = await marketplaceClient.getCodexDetails(prefill);
      if (c) {
        kind = "codex";
        name = c.name;
        description = c.description;
        version = c.version;
        icon = c.icon;
        categoriesStr = c.categories;
        realmType = c.realm_type;
        return;
      }
    } catch {
    }
  }
  try {
    const e = await marketplaceClient.getExtensionDetails(prefill);
    if (e) {
      kind = "ext";
      name = e.name;
      description = e.description;
      version = e.version;
      icon = e.icon;
      categoriesStr = e.categories;
      extLanguages = e.languages || "en";
    }
  } catch {
  }
});
$: ns = id && version ? `${kind}/${id}/${version}` : "";
$: if ($isAuthenticated && $principalStore) void loadLicense();
$: kind, id, version, (checkError = "");
async function loadLicense() {
  license = await marketplaceClient.getLicenseStatus();
  licenseLoaded = true;
}
async function next() {
  checking = true;
  checkError = "";
  try {
    await marketplaceClient.checkUpload(kind, id.trim(), version.trim());
    step = 2;
    if (pickedFiles.length) void readManifest(pickedFiles);
  } catch (e) {
    checkError = e?.message ?? String(e);
  } finally {
    checking = false;
  }
}
function onFiles(e) {
  const input = e.target;
  const list = Array.from(input.files ?? []);
  pickedFiles = list;
  manifestScreenshots = "";
  packageProblem = null;
  if (list.length > 0) {
    const first = list[0].webkitRelativePath;
    baseFolder = first?.split("/")?.[0] ?? "";
  }
  void readManifest(list);
}
async function readManifest(list) {
  const manifestFile = list.find((f) => relativePath(f) === "manifest.json");
  const text = manifestFile ? await manifestFile.text() : null;
  if (list !== pickedFiles) return;
  packageProblem = list.length ? checkPackage(kind, id, version, list.map(relativePath), text) : null;
  if (kind !== "ext" || text === null) return;
  try {
    const manifest = JSON.parse(text);
    if (Array.isArray(manifest.screenshots)) {
      manifestScreenshots = manifest.screenshots
        .map((p) => (typeof p === "string" ? p.trim() : ""))
        .filter(Boolean)
        .join(",");
    }
  } catch {
    manifestScreenshots = "";
  }
}
function relativePath(f) {
  const wkrel = f.webkitRelativePath;
  if (wkrel) {
    const parts = wkrel.split("/");
    if (parts.length > 1) return parts.slice(1).join("/");
    return wkrel;
  }
  return f.name;
}
function totalBytes() {
  return pickedFiles.reduce((s, f) => s + f.size, 0);
}
async function startUpload() {
  if (!ns || pickedFiles.length === 0 || uploading) return;
  uploading = true;
  uploadError = "";
  uploadProgress = {};
  step = 3;
  const items = pickedFiles.map((f) => ({ path: relativePath(f), file: f }));
  try {
    const target = await marketplaceClient.prepareUpload(kind, id.trim(), version.trim());
    const registryCanisterId = target.file_registry_canister_id;
    const ns = target.namespace;
    await uploadAndPublish(registryCanisterId, ns, items, (p) => {
      uploadProgress = { ...uploadProgress, [p.path]: p };
    });
    const priceE8s = BigInt(Math.max(0, Math.round(priceUsdCents / 100 * 1e8)));
    if (kind === "ext") {
      await marketplaceClient.createExtension({
        extension_id: id,
        name,
        description,
        version,
        price_e8s: priceE8s,
        icon,
        categories: categoriesStr,
        languages: extLanguages,
        screenshots: manifestScreenshots,
        file_registry_canister_id: registryCanisterId,
        file_registry_namespace: ns,
        download_url: ""
      });
      listingHref = `/extensions/${encodeURIComponent(id)}`;
    } else if (kind === "codex") {
      await marketplaceClient.createCodex({
        codex_id: id,
        realm_type: realmType,
        name,
        description,
        version,
        price_e8s: priceE8s,
        icon,
        categories: categoriesStr,
        file_registry_canister_id: registryCanisterId,
        file_registry_namespace: ns
      });
      listingHref = `/codices/${encodeURIComponent(id)}`;
    } else {
      await marketplaceClient.createAssistant({
        assistant_id: id,
        name,
        description,
        version,
        price_e8s: priceE8s,
        pricing_summary: pricingSummary,
        icon,
        categories: categoriesStr,
        runtime,
        endpoint_url: endpointUrl,
        base_model: baseModel,
        requested_role: requestedRole,
        requested_permissions: requestedPermissions,
        domains,
        languages,
        training_data_summary: trainingDataSummary,
        eval_report_url: evalReportUrl,
        file_registry_canister_id: registryCanisterId,
        file_registry_namespace: ns
      });
      listingHref = `/assistants/${encodeURIComponent(id)}`;
    }
    step = 4;
  } catch (e) {
    uploadError = e?.message ?? String(e);
  } finally {
    uploading = false;
  }
}
function reset() {
  step = 1;
  pickedFiles = [];
  uploadProgress = {};
  uploadError = "";
  listingHref = "";
  extLanguages = "en";
  manifestScreenshots = "";
  packageProblem = null;
}
</script>

<header class="head">
  <h1>{$_('upload.title')}</h1>
  <p>{$_('upload.subtitle')}</p>
</header>

{#if !$isAuthenticated}
  <div class="card warn">
    <h2>{$_('upload.signin_title')}</h2>
    <p>{$_('upload.signin_body')}</p>
    <p>{$_('upload.signin_note')}</p>
  </div>
{:else}
  <div class="wizard">
    <ol class="steps">
      <li class:active={step === 1} class:done={step > 1}>{$_('upload.step_metadata')}</li>
      <li class:active={step === 2} class:done={step > 2}>{$_('upload.step_files')}</li>
      <li class:active={step === 3} class:done={step > 3}>{$_('upload.step_upload')}</li>
      <li class:active={step === 4} class:done={step > 4}>{$_('upload.step_review')}</li>
      <li class:active={step === 5}>{$_('upload.step_done')}</li>
    </ol>

    {#if step === 1}
      {#if licenseLoaded && !license?.is_active}
        <div class="card warn notice">
          <h2>{$_('upload.license_needed_title')}</h2>
          <p>{$_('upload.license_needed_body')}</p>
          <a class="btn" href="/developer">{$_('upload.license_needed_link')}</a>
        </div>
      {/if}
      <section class="card">
        <h2>{$_('upload.what')}</h2>
        <div class="kind-switch">
          <button class:active={kind === 'ext'} on:click={() => (kind = 'ext')}>{$_('upload.kind_ext')}</button>
          <button class:active={kind === 'codex'} on:click={() => (kind = 'codex')}>{$_('upload.kind_codex')}</button>
          <button class:active={kind === 'assistant'} on:click={() => (kind = 'assistant')}>{$_('upload.kind_assistant')}</button>
        </div>

        <div class="grid">
          <label>
            <span>{kind === 'ext' ? 'extension_id' : kind === 'codex' ? 'codex_id' : 'assistant_id'}</span>
            <input
              type="text"
              placeholder={kind === 'ext' ? 'voting' : kind === 'codex' ? 'syntropia/membership' : 'smart-social-contracts/ashoka'}
              bind:value={id}
            />
          </label>
          {#if kind === 'codex'}
            <label>
              <span>{$_('upload.realm_type')}</span>
              <input type="text" placeholder="syntropia" bind:value={realmType} />
            </label>
          {/if}
          <label>
            <span>{$_('upload.version')}</span>
            <input type="text" placeholder="0.1.0" bind:value={version} />
          </label>
          <label>
            <span>{$_('upload.name')}</span>
            <input type="text" placeholder="Token-weighted Voting" bind:value={name} />
          </label>
          <label class="full">
            <span>{$_('upload.description')}</span>
            <textarea rows="3" bind:value={description} />
          </label>
          <label>
            <span>{$_('upload.icon')}</span>
            <input type="text" placeholder="🗳️" bind:value={icon} />
          </label>
          <label>
            <span>{$_('upload.categories')}</span>
            <input type="text" placeholder="public_services,governance" bind:value={categoriesStr} />
          </label>
          {#if kind === 'ext'}
            <label>
              <span>{$_('upload.languages')}</span>
              <input type="text" placeholder="en,es,fr" bind:value={extLanguages} />
            </label>
          {/if}
          <label>
            <span>{$_('upload.price')}</span>
            <input type="number" min="0" step="50" bind:value={priceUsdCents} />
          </label>
        </div>

        <p class="namespace-preview">
          {$_('upload.namespace')} <code>{ns || $_('upload.namespace_hint')}</code>
        </p>

        {#if checkError}
          <p class="error">{checkError}</p>
        {/if}
        <div class="actions">
          <button class="btn primary" disabled={!id || !name || !version || checking} on:click={next}>
            {checking ? $_('upload.checking') : $_('upload.next')}
          </button>
        </div>
      </section>
    {:else if step === 2}
      <section class="card">
        <h2>{$_('upload.pick_title')}</h2>
        <p class="muted">
          {$_('upload.pick_choose')}
          {#if kind === 'ext'}
            {$_('upload.pick_ext')}
          {:else if kind === 'codex'}
            {$_('upload.pick_codex')}
          {:else}
            {$_('upload.pick_assistant')}
          {/if}
        </p>
        <input
          type="file"
          multiple
          {...dirInputAttrs}
          on:change={onFiles}
        />

        {#if packageProblem}
          <p class="error">{$_(`upload.package_${packageProblem.key}`, { values: { ...packageProblem.values, id, version } })}</p>
        {/if}
        {#if pickedFiles.length > 0}
          <table class="files">
            <thead><tr><th>{$_('upload.col_path')}</th><th>{$_('upload.col_size')}</th></tr></thead>
            <tbody>
              {#each pickedFiles as f}
                <tr>
                  <td><code>{relativePath(f)}</code></td>
                  <td>{(f.size / 1024).toFixed(1)} KB</td>
                </tr>
              {/each}
            </tbody>
          </table>
          <p class="muted">{$_('upload.files_summary', { values: { count: pickedFiles.length, mb: (totalBytes() / 1024 / 1024).toFixed(2) } })}</p>
        {/if}

        <div class="actions">
          <button class="btn ghost" on:click={() => (step = 1)}>{$_('upload.back')}</button>
          <button class="btn primary" disabled={pickedFiles.length === 0 || uploading || packageProblem !== null} on:click={startUpload}>
            {$_('upload.upload_publish')}
          </button>
        </div>
      </section>
    {:else if step === 3}
      <section class="card">
        <h2>{$_('upload.uploading')}</h2>
        <p class="muted">{$_('upload.uploading_note')}</p>
        {#if uploadError}
          <p class="error">{uploadError}</p>
          <div class="actions">
            <button class="btn" on:click={() => (step = 2)}>{$_('upload.back')}</button>
            <button class="btn primary" on:click={startUpload}>{$_('upload.retry')}</button>
          </div>
        {:else}
          <div class="progress-list">
            {#each pickedFiles as f}
              {#if uploadProgress[relativePath(f)]}
                {@const p = uploadProgress[relativePath(f)]}
                <div class="row">
                  <div class="path"><code>{p.path}</code></div>
                  <div class="bar">
                    <div class="fill" style="width: {Math.min(100, (p.uploaded / Math.max(1, p.total)) * 100)}%"></div>
                  </div>
                  <div class="status">{p.status}</div>
                </div>
              {:else}
                <div class="row pending">
                  <div class="path"><code>{relativePath(f)}</code></div>
                  <div class="bar"></div>
                  <div class="status">{$_('upload.queued')}</div>
                </div>
              {/if}
            {/each}
          </div>
          {#if uploading}
            <div class="centered"><Spinner /></div>
          {/if}
        {/if}
      </section>
    {:else if step === 4}
      <section class="card success">
        <h2>{$_('upload.published')}</h2>
        <p>{$_('upload.published_body', { values: { kind: kind === 'ext' ? $_('upload.kind_ext') : kind === 'codex' ? $_('upload.kind_codex') : $_('upload.kind_assistant') } })}</p>
        <div class="actions">
          <a class="btn primary" href="/developer">{$_('upload.view_submissions')}</a>
          <a class="btn" href={listingHref}>{$_('upload.view_listing')}</a>
          <button class="btn ghost" on:click={reset}>{$_('upload.upload_another')}</button>
        </div>
      </section>
    {/if}
  </div>
{/if}

<style>
  .head h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
  .head p { margin: 0 0 1.5rem; color: var(--text-muted); }
  .wizard { max-width: 760px; margin: 0 auto; }
  .steps {
    list-style: none; display: flex; gap: 1rem; padding: 0; margin: 0 0 1.5rem;
    flex-wrap: wrap;
  }
  .steps li {
    flex: 1; min-width: 130px;
    padding: 0.6rem 0.85rem; border: 1px solid var(--border);
    border-radius: 0.5rem; background: var(--surface);
    color: var(--text-faint); font-size: 0.85rem;
  }
  .steps li.active { border-color: var(--primary); color: var(--text); font-weight: 600; }
  .steps li.done { border-color: var(--success); color: var(--success); }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    padding: 1.5rem 1.75rem; border-radius: 0.75rem;
  }
  .card.success { border-color: var(--border-strong); background: var(--surface-2); color: var(--text); }
  .card.warn { border-color: var(--border-strong); background: var(--surface-2); color: var(--text); }
  .card h2 { margin: 0 0 0.5rem; font-size: 1.15rem; }
  .kind-switch { display: flex; gap: 0.5rem; margin: 1rem 0 1.25rem; }
  .kind-switch button {
    background: var(--surface-2); border: 1px solid var(--border);
    padding: 0.6rem 1rem; border-radius: 0.5rem;
    color: var(--text-muted);
  }
  .kind-switch button.active {
    background: var(--primary); border-color: var(--primary); color: #fff;
  }
  .grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0.85rem 1.25rem;
  }
  .grid label.full { grid-column: 1 / -1; }
  label { display: flex; flex-direction: column; gap: 0.3rem; }
  label span { font-size: 0.8rem; color: var(--text-faint); }
  input, textarea {
    border: 1px solid var(--border); border-radius: 0.4rem;
    padding: 0.55rem 0.7rem; background: var(--surface); font: inherit; color: var(--text);
  }
  input:focus, textarea:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .namespace-preview { color: var(--text-muted); margin-top: 1rem; font-size: 0.85rem; }
  .namespace-preview code { background: var(--surface-2); padding: 0.15rem 0.45rem; border-radius: 0.3rem; }
  .actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1.5rem; }
  .btn {
    border: 1px solid var(--border); background: var(--surface); color: var(--text-muted);
    padding: 0.5rem 1rem; border-radius: 0.5rem; text-decoration: none;
  }
  .btn:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
  .btn.primary:hover:not(:disabled) { background: var(--primary-hover); }
  .btn.ghost { background: transparent; }
  .files { width: 100%; border-collapse: collapse; margin: 1rem 0 0.5rem; }
  .files th, .files td { text-align: left; padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--surface-2); font-size: 0.85rem; }
  .files th { color: var(--text-faint); font-weight: 500; }
  .progress-list { display: flex; flex-direction: column; gap: 0.5rem; margin: 1.25rem 0; }
  .progress-list .row { display: grid; grid-template-columns: 1fr 200px 90px; gap: 1rem; align-items: center; }
  .path code { font-size: 0.85rem; }
  .bar {
    background: var(--surface-2); height: 8px; border-radius: 999px; overflow: hidden;
  }
  .fill { background: var(--success); height: 100%; transition: width 0.2s ease; }
  .status { text-transform: capitalize; font-size: 0.8rem; color: var(--text-muted); }
  .centered { text-align: center; padding: 1rem 0; }
  .error { color: var(--danger); }
  .muted { color: var(--text-muted); font-size: 0.85rem; }
  .notice { margin-bottom: 1rem; }
  .notice p { margin: 0 0 0.85rem; color: var(--text-muted); }
  .notice .btn { display: inline-block; }
</style>
