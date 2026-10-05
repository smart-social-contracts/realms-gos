<script lang="ts">import { _ } from "svelte-i18n";
import VerifiedBadge from "$lib/components/VerifiedBadge.svelte";
import Spinner from "$lib/components/Spinner.svelte";
import { isAuthenticated, principalStore } from "$lib/auth";
import { ACCOUNT_ALPHA, CONFIG } from "$lib/config";
import { marketplaceClient } from "$lib/marketplace-client";
import { formatTimeAgo, formatCount, formatPriceUsd } from "$lib/format";
let loading = false;
let error = "";
let license = null;
let pricing = { license_price_usd_cents: 0, license_duration_seconds: 0 };
let myExtensions = [];
let myCodices = [];
let myAssistants = [];
let buyingLicense = false;
let licenseError = "";
$: void load($isAuthenticated);
async function load(_authed) {
  if (!_authed) {
    license = null;
    myExtensions = [];
    myCodices = [];
    myAssistants = [];
    return;
  }
  loading = true;
  error = "";
  try {
    pricing = await marketplaceClient.getLicensePricing();
    license = await marketplaceClient.getLicenseStatus();
    myExtensions = await marketplaceClient.getMyExtensions();
    myCodices = await marketplaceClient.getMyCodices();
    myAssistants = await marketplaceClient.getMyAssistants();
  } catch (e) {
    error = e?.message ?? String(e);
  } finally {
    loading = false;
  }
}
async function buyLicense() {
  if (ACCOUNT_ALPHA || buyingLicense || !$principalStore) return;
  buyingLicense = true;
  licenseError = "";
  try {
    const url = `${CONFIG.billing_service_url.replace(/\/$/, "")}/marketplace/license/checkout`;
    const returnUrl = `${window.location.origin}/developer?license_status=success`;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        principal_id: $principalStore.toText(),
        return_url: returnUrl,
        marketplace_canister_id: CONFIG.marketplace_canister_id
      })
    });
    if (!resp.ok) {
      const txt = await resp.text().catch(() => "");
      throw new Error(`HTTP ${resp.status}${txt ? `: ${txt}` : ""}`);
    }
    const data = await resp.json();
    if (!data.checkout_url) throw new Error("billing service returned no checkout_url");
    window.location.href = data.checkout_url;
  } catch (e) {
    licenseError = $_("developer.billing_unavailable", { values: { error: e?.message ?? e } });
  } finally {
    buyingLicense = false;
  }
}
async function requestAuditFor(kind, id) {
  if (ACCOUNT_ALPHA) return;
  try {
    await marketplaceClient.requestAudit(kind, id);
    await load(true);
  } catch (e) {
    alert($_("developer.could_not_audit", { values: { error: e?.message ?? e } }));
  }
}
async function delist(kind, id) {
  if (ACCOUNT_ALPHA) return;
  if (!confirm($_("developer.delist_confirm", { values: { kind, id } }))) return;
  try {
    if (kind === "ext") await marketplaceClient.delistExtension(id);
    else if (kind === "assistant") await marketplaceClient.delistAssistant(id);
    else await marketplaceClient.delistCodex(id);
    await load(true);
  } catch (e) {
    alert($_("developer.could_not_delist", { values: { error: e?.message ?? e } }));
  }
}
</script>

<header class="head">
  <h1>{$_('developer.title')}</h1>
  <p>{$_('developer.subtitle')}</p>
</header>

{#if !$isAuthenticated}
  <div class="card warn">
    <h2>{$_('auth.sign_in_required')}</h2>
    <p>{$_('developer.signin_body')}</p>
  </div>
{:else if loading}
  <div class="state"><Spinner /></div>
{:else if error}
  <div class="state error">{error}</div>
{:else}
  <section class="card">
    <h2>{$_('developer.license_h')}</h2>
    {#if license && license.is_active}
      <p class="ok">
        {$_('developer.active_until', { values: { date: new Date(license.expires_at / 1_000_000).toLocaleString() } })}
        {#if license.payment_method}<span class="muted">{$_('developer.paid_via', { values: { method: license.payment_method } })}</span>{/if}
      </p>
      <p class="small">
        {$_('developer.license_grants')}
      </p>
    {:else}
      <p>{$_('developer.no_license')}</p>
      <ul class="benefits">
        <li>{$_('developer.benefit_1')}</li>
        <li>{$_('developer.benefit_2')}</li>
        <li>{$_('developer.benefit_3')}</li>
      </ul>
      <p class="price">
        {$_('developer.per_month', { values: { price: formatPriceUsd(pricing.license_price_usd_cents) } })}
      </p>
      <button class="btn primary" disabled={buyingLicense} on:click={buyLicense}>
        {buyingLicense ? $_('developer.redirecting') : $_('developer.pay_card')}
      </button>
      {#if licenseError}
        <p class="license-error">{licenseError}</p>
      {/if}
      <p class="small muted">
        {$_('developer.billing_note', { values: { url: CONFIG.billing_service_url } })}
      </p>
    {/if}
  </section>

  <section class="card">
    <h2>{$_('developer.my_extensions', { values: { count: myExtensions.length } })}</h2>
    {#if myExtensions.length === 0}
      <p class="muted">{$_('developer.none_ext')} <a href="/upload">{$_('developer.upload_one')}</a></p>
    {:else}
      <table>
        <thead><tr><th>{$_('developer.col_id')}</th><th>{$_('developer.col_version')}</th><th>{$_('developer.col_verified')}</th><th>{$_('developer.col_installs')}</th><th>{$_('developer.col_likes')}</th><th>{$_('developer.col_updated')}</th><th></th></tr></thead>
        <tbody>
          {#each myExtensions as e (e.extension_id)}
            <tr>
              <td><a class="link" href={`/extensions/${encodeURIComponent(e.extension_id)}`}>{e.extension_id}</a></td>
              <td>{e.version}</td>
              <td><VerifiedBadge status={e.verification_status} /></td>
              <td>{formatCount(e.installs)}</td>
              <td>{formatCount(e.likes)}</td>
              <td>{formatTimeAgo(e.updated_at)}</td>
              <td>
                <button class="btn tiny" disabled={e.verification_status === 'pending_audit' || e.verification_status === 'verified'} on:click={() => requestAuditFor('ext', e.extension_id)}>
                  {$_('developer.request_audit')}
                </button>
                <button class="btn tiny ghost" on:click={() => delist('ext', e.extension_id)}>{$_('developer.delist')}</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>

  <section class="card">
    <h2>{$_('developer.my_codices', { values: { count: myCodices.length } })}</h2>
    {#if myCodices.length === 0}
      <p class="muted">{$_('developer.none_codex')} <a href="/upload">{$_('developer.upload_one')}</a></p>
    {:else}
      <table>
        <thead><tr><th>{$_('developer.col_id')}</th><th>{$_('developer.col_version')}</th><th>{$_('developer.col_verified')}</th><th>{$_('developer.col_installs')}</th><th>{$_('developer.col_likes')}</th><th>{$_('developer.col_updated')}</th><th></th></tr></thead>
        <tbody>
          {#each myCodices as c (c.codex_id)}
            <tr>
              <td><a class="link" href={`/codices/${encodeURIComponent(c.codex_id)}`}>{c.codex_id}</a></td>
              <td>{c.version}</td>
              <td><VerifiedBadge status={c.verification_status} /></td>
              <td>{formatCount(c.installs)}</td>
              <td>{formatCount(c.likes)}</td>
              <td>{formatTimeAgo(c.updated_at)}</td>
              <td>
                <button class="btn tiny" disabled={c.verification_status === 'pending_audit' || c.verification_status === 'verified'} on:click={() => requestAuditFor('codex', c.codex_id)}>
                  {$_('developer.request_audit')}
                </button>
                <button class="btn tiny ghost" on:click={() => delist('codex', c.codex_id)}>{$_('developer.delist')}</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>

  <section class="card">
    <h2>{$_('developer.my_assistants', { values: { count: myAssistants.length } })}</h2>
    {#if myAssistants.length === 0}
      <p class="muted">{$_('developer.none_assistant')} <a href="/upload">{$_('developer.upload_one')}</a></p>
    {:else}
      <table>
        <thead><tr><th>{$_('developer.col_id')}</th><th>{$_('developer.col_version')}</th><th>{$_('developer.col_verified')}</th><th>{$_('developer.col_installs')}</th><th>{$_('developer.col_likes')}</th><th>{$_('developer.col_updated')}</th><th></th></tr></thead>
        <tbody>
          {#each myAssistants as a (a.assistant_id)}
            <tr>
              <td><a class="link" href={`/assistants/${encodeURIComponent(a.assistant_id)}`}>{a.assistant_id}</a></td>
              <td>{a.version}</td>
              <td><VerifiedBadge status={a.verification_status} /></td>
              <td>{formatCount(a.installs)}</td>
              <td>{formatCount(a.likes)}</td>
              <td>{formatTimeAgo(a.updated_at)}</td>
              <td>
                <button class="btn tiny" disabled={a.verification_status === 'pending_audit' || a.verification_status === 'verified'} on:click={() => requestAuditFor('assistant', a.assistant_id)}>
                  {$_('developer.request_audit')}
                </button>
                <button class="btn tiny ghost" on:click={() => delist('assistant', a.assistant_id)}>{$_('developer.delist')}</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  </section>
{/if}

<style>
  .head h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
  .head p { margin: 0 0 1.5rem; color: var(--text-muted); }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    padding: 1.5rem 1.75rem; border-radius: 0.75rem; margin-bottom: 1.25rem;
  }
  .card.warn { border-color: var(--border-strong); background: var(--surface-2); color: var(--text); }
  .card h2 { margin: 0 0 0.85rem; font-size: 1.1rem; }
  .ok { color: var(--success); font-weight: 500; }
  .price { font-size: 1.5rem; font-weight: 700; margin: 0.85rem 0; color: var(--text); }
  .license-error {
    margin: 0.75rem 0 0;
    padding: 0.65rem 0.85rem;
    background: #FEE2E2;
    border: 1px solid var(--danger);
    color: #991B1B;
    border-radius: 0.5rem;
    font-size: 0.85rem;
  }
  .benefits { margin: 0.5rem 0 0.85rem 1.25rem; color: var(--text-muted); }
  .small { font-size: 0.8rem; }
  .muted { color: var(--text-faint); }
  .btn {
    border: 1px solid var(--border); background: var(--surface); color: var(--text-muted);
    padding: 0.5rem 1rem; border-radius: 0.4rem;
  }
  .btn:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
  .btn.primary:hover:not(:disabled) { background: var(--primary-hover); }
  .btn.tiny { padding: 0.3rem 0.6rem; font-size: 0.75rem; margin-right: 0.3rem; }
  .btn.ghost { background: transparent; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--surface-2); font-size: 0.85rem; }
  th { color: var(--text-faint); font-weight: 500; }
  .link { color: var(--accent); }
  .state { text-align: center; padding: 3rem; color: var(--text-muted); }
  .state.error { color: var(--danger); }
</style>
