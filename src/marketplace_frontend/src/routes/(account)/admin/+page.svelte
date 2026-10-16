<script lang="ts">
  import { _ } from 'svelte-i18n';
  import Spinner from '$lib/components/Spinner.svelte';
  import { isAuthenticated } from '$lib/auth';
  import { roles, refreshRoles } from '$lib/roles';
  import { marketplaceClient } from '$lib/marketplace-client';
  import type { AdminListing, AdminUser, ItemKind, MarketplaceStatus } from '$lib/marketplace-client';
  import { formatDate, formatTimeAgo, shortPrincipal } from '$lib/format';

  type Tab = 'listings' | 'users' | 'roles';
  const QUEUED = ['pending_review', 'pending_audit'];
  const STATUSES = ['unverified', 'pending_review', 'pending_audit', 'verified', 'rejected'];
  const DAY_SECONDS = 86400;

  let tab: Tab = 'listings';
  let loading = false;
  let error = '';
  let busy = '';

  let status: MarketplaceStatus | null = null;
  let listings: AdminListing[] = [];
  let users: AdminUser[] = [];
  let admins: string[] = [];
  let reviewers: string[] = [];

  let kindFilter: 'all' | ItemKind = 'all';
  let statusFilter = 'all';
  let showDelisted = true;
  let listingQuery = '';
  let userQuery = '';

  let grantPrincipal = '';
  let grantDays = 30;
  let grantNote = 'manual grant';
  let newReviewer = '';

  $: void load($isAuthenticated, $roles.is_admin);

  async function load(authed: boolean, isAdmin: boolean) {
    if (!authed || !isAdmin) return;
    loading = true;
    error = '';
    try {
      [status, listings, users, admins, reviewers] = await Promise.all([
        marketplaceClient.getStatus(),
        marketplaceClient.adminListListings(),
        marketplaceClient.adminListUsers(),
        marketplaceClient.listAdmins(),
        marketplaceClient.listReviewers(),
      ]);
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  async function act(key: string, fn: () => Promise<unknown>) {
    if (busy) return;
    busy = key;
    try {
      await fn();
      await load(true, true);
    } catch (e: any) {
      alert($_('admin.action_failed', { values: { error: e?.message ?? e } }));
    } finally {
      busy = '';
    }
  }

  const keyOf = (l: AdminListing) => `${l.item_kind}:${l.item_id}`;

  $: queuedCount = listings.filter((l) => QUEUED.includes(l.verification_status)).length;
  $: filteredListings = listings.filter((l) => {
    if (kindFilter !== 'all' && l.item_kind !== kindFilter) return false;
    if (statusFilter === 'queued' && !QUEUED.includes(l.verification_status)) return false;
    if (statusFilter !== 'all' && statusFilter !== 'queued' && l.verification_status !== statusFilter) return false;
    if (!showDelisted && !l.is_active) return false;
    const q = listingQuery.trim().toLowerCase();
    if (q && ![l.item_id, l.name, l.developer, l.developer_name].some((s) => s.toLowerCase().includes(q))) return false;
    return true;
  });
  $: filteredUsers = users.filter((u) => {
    const q = userQuery.trim().toLowerCase();
    return !q || u.principal.toLowerCase().includes(q) || u.display_name.toLowerCase().includes(q);
  });

  function itemHref(l: AdminListing): string {
    const base = l.item_kind === 'ext' ? 'extensions' : l.item_kind === 'codex' ? 'codices' : 'assistants';
    return `/${base}/${encodeURIComponent(l.item_id)}`;
  }

  function review(l: AdminListing, approve: boolean) {
    const note = prompt($_(approve ? 'admin.approve_prompt' : 'admin.reject_prompt'), '');
    if (note === null) return;
    act(`review:${keyOf(l)}`, () => marketplaceClient.reviewListing(l.item_kind, l.item_id, approve, note));
  }

  function resetToQueue(l: AdminListing) {
    act(`reset:${keyOf(l)}`, () =>
      marketplaceClient.setVerificationStatus(l.item_kind, l.item_id, 'pending_review', $_('admin.reset_note')),
    );
  }

  function toggleActive(l: AdminListing) {
    if (l.is_active && !confirm($_('admin.delist_confirm', { values: { id: l.item_id } }))) return;
    act(`active:${keyOf(l)}`, () => marketplaceClient.adminSetListingActive(l.item_kind, l.item_id, !l.is_active));
  }

  function transfer(l: AdminListing) {
    const to = prompt($_('admin.transfer_prompt', { values: { id: l.item_id } }), '');
    if (!to || !to.trim()) return;
    act(`transfer:${keyOf(l)}`, () => marketplaceClient.transferListing(l.item_kind, l.item_id, to.trim()));
  }

  function grantFor(u: AdminUser) {
    const raw = prompt($_('admin.grant_days_prompt'), String(grantDays));
    if (raw === null) return;
    const days = Number(raw);
    if (!Number.isFinite(days) || days <= 0) return;
    act(`grant:${u.principal}`, () =>
      marketplaceClient.grantManualLicense(u.principal, Math.round(days * DAY_SECONDS), grantNote),
    );
  }

  function revokeFor(u: AdminUser) {
    if (!confirm($_('admin.revoke_confirm', { values: { who: u.display_name || u.principal } }))) return;
    act(`revoke:${u.principal}`, () => marketplaceClient.revokeLicense(u.principal));
  }

  function toggleSuspended(u: AdminUser) {
    if (u.is_suspended) {
      act(`suspend:${u.principal}`, () => marketplaceClient.adminSetUserSuspended(u.principal, false, ''));
      return;
    }
    const reason = prompt($_('admin.suspend_prompt'), '');
    if (reason === null) return;
    act(`suspend:${u.principal}`, () => marketplaceClient.adminSetUserSuspended(u.principal, true, reason));
  }

  function rename(u: AdminUser) {
    const name = prompt($_('admin.rename_prompt'), u.display_name);
    if (name === null) return;
    act(`rename:${u.principal}`, () => marketplaceClient.setDeveloperName(u.principal, name));
  }

  function toggleReviewer(principal: string, isReviewer: boolean) {
    act(`reviewer:${principal}`, () =>
      isReviewer ? marketplaceClient.removeReviewer(principal) : marketplaceClient.addReviewer(principal),
    );
  }

  function grantByPrincipal() {
    const p = grantPrincipal.trim();
    if (!p) return;
    const seconds = Math.max(1, Math.round(grantDays * DAY_SECONDS));
    act('grant-form', async () => {
      await marketplaceClient.grantManualLicense(p, seconds, grantNote);
      grantPrincipal = '';
    });
  }

  function addReviewerByPrincipal() {
    const p = newReviewer.trim();
    if (!p) return;
    act('reviewer-form', async () => {
      await marketplaceClient.addReviewer(p);
      newReviewer = '';
    });
  }

  function licenseLabel(u: AdminUser): string {
    if (!u.has_license) return $_('admin.license_none');
    if (u.license_active) return $_('admin.license_until', { values: { date: formatDate(u.license_expires_at) } });
    if (u.license_note === 'revoked') return $_('admin.license_revoked');
    return $_('admin.license_expired');
  }

  const kindLabel = (k: ItemKind) => $_(`admin.kind_${k}`);
</script>

<header class="head">
  <h1>{$_('admin.title')}</h1>
  <p>{$_('admin.subtitle')}</p>
</header>

{#if !$isAuthenticated}
  <div class="card warn"><h2>{$_('auth.sign_in_required')}</h2></div>
{:else if !$roles.is_admin}
  <div class="card warn">
    <h2>{$_('admin.not_admin_h')}</h2>
    <p>{$_('admin.not_admin_body')}</p>
    <button class="btn tiny" on:click={() => refreshRoles()}>{$_('admin.recheck')}</button>
  </div>
{:else}
  {#if status}
    <ul class="stats">
      <li><strong>{status.extensions_count}</strong><span>{$_('admin.extensions')}</span></li>
      <li><strong>{status.codices_count}</strong><span>{$_('admin.codices')}</span></li>
      <li><strong>{status.assistants_count}</strong><span>{$_('admin.assistants')}</span></li>
      <li><strong>{users.length}</strong><span>{$_('admin.users')}</span></li>
      <li><strong>{status.licenses_count}</strong><span>{$_('admin.licenses')}</span></li>
      <li><strong>{status.purchases_count}</strong><span>{$_('admin.purchases')}</span></li>
    </ul>
  {/if}

  <div class="tabs" role="tablist">
    <button role="tab" aria-selected={tab === 'listings'} class:active={tab === 'listings'} on:click={() => (tab = 'listings')}>
      {$_('admin.tab_listings')}
      {#if queuedCount > 0}<span class="count">{queuedCount}</span>{/if}
    </button>
    <button role="tab" aria-selected={tab === 'users'} class:active={tab === 'users'} on:click={() => (tab = 'users')}>
      {$_('admin.tab_users')}
    </button>
    <button role="tab" aria-selected={tab === 'roles'} class:active={tab === 'roles'} on:click={() => (tab = 'roles')}>
      {$_('admin.tab_roles')}
    </button>
    {#if loading}<span class="loading"><Spinner /></span>{/if}
  </div>

  {#if error}<div class="state error">{error}</div>{/if}

  {#if tab === 'listings'}
    <section class="card">
      <div class="filters">
        <input type="search" placeholder={$_('admin.search_listings')} bind:value={listingQuery} />
        <select bind:value={kindFilter} aria-label={$_('admin.col_kind')}>
          <option value="all">{$_('admin.all_kinds')}</option>
          <option value="ext">{$_('admin.kind_ext')}</option>
          <option value="codex">{$_('admin.kind_codex')}</option>
          <option value="assistant">{$_('admin.kind_assistant')}</option>
        </select>
        <select bind:value={statusFilter} aria-label={$_('admin.col_status')}>
          <option value="all">{$_('admin.all_statuses')}</option>
          <option value="queued">{$_('admin.awaiting_review', { values: { count: queuedCount } })}</option>
          {#each STATUSES as s}<option value={s}>{$_(`admin.status_${s}`)}</option>{/each}
        </select>
        <label class="check"><input type="checkbox" bind:checked={showDelisted} /> {$_('admin.show_delisted')}</label>
      </div>

      {#if filteredListings.length === 0}
        <p class="muted">{$_('admin.no_listings')}</p>
      {:else}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{$_('admin.col_kind')}</th>
                <th>{$_('admin.col_listing')}</th>
                <th>{$_('admin.col_developer')}</th>
                <th>{$_('admin.col_version')}</th>
                <th>{$_('admin.col_status')}</th>
                <th>{$_('admin.col_updated')}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each filteredListings as l (keyOf(l))}
                <tr class:inactive={!l.is_active}>
                  <td><span class="kind">{kindLabel(l.item_kind)}</span></td>
                  <td>
                    {#if l.is_active}<a class="link" href={itemHref(l)}>{l.name || l.item_id}</a>{:else}{l.name || l.item_id}{/if}
                    <div class="sub"><code>{l.item_id}</code>{#if !l.is_active} · <span class="pill off">{$_('admin.delisted')}</span>{/if}</div>
                  </td>
                  <td title={l.developer}>
                    {l.developer_name || ''}
                    <div class="sub"><code>{shortPrincipal(l.developer)}</code></div>
                  </td>
                  <td>{l.version}</td>
                  <td>
                    <span class="pill {l.verification_status}">{$_(`admin.status_${l.verification_status}`)}</span>
                    {#if l.verification_notes}<div class="sub" title={l.verification_notes}>{l.verification_notes}</div>{/if}
                  </td>
                  <td>{formatTimeAgo(l.updated_at)}</td>
                  <td class="actions"><div class="btns">
                    {#if l.verification_status !== 'verified'}
                      <button class="btn primary tiny" disabled={!!busy} on:click={() => review(l, true)}>{$_('admin.approve')}</button>
                    {/if}
                    {#if l.verification_status !== 'rejected'}
                      <button class="btn danger tiny" disabled={!!busy} on:click={() => review(l, false)}>{$_('admin.reject')}</button>
                    {/if}
                    {#if !QUEUED.includes(l.verification_status)}
                      <button class="btn tiny" disabled={!!busy} on:click={() => resetToQueue(l)}>{$_('admin.reset')}</button>
                    {/if}
                    <button class="btn tiny" disabled={!!busy} on:click={() => toggleActive(l)}>
                      {l.is_active ? $_('admin.delist') : $_('admin.relist')}
                    </button>
                    <button class="btn tiny ghost" disabled={!!busy} on:click={() => transfer(l)}>{$_('admin.transfer')}</button>
                  </div></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </section>
  {:else if tab === 'users'}
    <section class="card">
      <div class="filters">
        <input type="search" placeholder={$_('admin.search_users')} bind:value={userQuery} />
      </div>
      {#if filteredUsers.length === 0}
        <p class="muted">{$_('admin.no_users')}</p>
      {:else}
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{$_('admin.col_user')}</th>
                <th>{$_('admin.col_roles')}</th>
                <th>{$_('admin.col_license')}</th>
                <th>{$_('admin.col_listings')}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each filteredUsers as u (u.principal)}
                <tr class:inactive={u.is_suspended}>
                  <td title={u.principal}>
                    {u.display_name || ''}
                    <div class="sub"><code>{u.principal}</code></div>
                  </td>
                  <td>
                    {#if u.is_admin}<span class="pill admin">{$_('admin.role_admin')}</span>{/if}
                    {#if u.is_reviewer}<span class="pill reviewer">{$_('admin.role_reviewer')}</span>{/if}
                    {#if u.is_suspended}<span class="pill rejected" title={u.suspended_reason}>{$_('admin.suspended')}</span>{/if}
                  </td>
                  <td>
                    <span class:ok={u.license_active}>{licenseLabel(u)}</span>
                    {#if u.license_payment_method}<div class="sub">{u.license_payment_method}</div>{/if}
                  </td>
                  <td>{u.extensions_count} / {u.codices_count} / {u.assistants_count}</td>
                  <td class="actions"><div class="btns">
                    <button class="btn tiny" disabled={!!busy} on:click={() => grantFor(u)}>
                      {u.license_active ? $_('admin.extend') : $_('admin.grant')}
                    </button>
                    {#if u.license_active}
                      <button class="btn tiny" disabled={!!busy} on:click={() => revokeFor(u)}>{$_('admin.revoke')}</button>
                    {/if}
                    {#if !u.is_admin}
                      <button class="btn tiny" class:danger={!u.is_suspended} disabled={!!busy} on:click={() => toggleSuspended(u)}>
                        {u.is_suspended ? $_('admin.reinstate') : $_('admin.suspend')}
                      </button>
                    {/if}
                    {#if !u.is_admin}
                      <button class="btn tiny ghost" disabled={!!busy} on:click={() => toggleReviewer(u.principal, u.is_reviewer)}>
                        {u.is_reviewer ? $_('admin.remove_reviewer') : $_('admin.make_reviewer')}
                      </button>
                    {/if}
                    <button class="btn tiny ghost" disabled={!!busy} on:click={() => rename(u)}>{$_('admin.rename')}</button>
                  </div></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="muted small">{$_('admin.listings_legend')}</p>
      {/if}
    </section>

    <section class="card">
      <h2>{$_('admin.grant_h')}</h2>
      <p class="muted small">{$_('admin.grant_note')}</p>
      <div class="grid">
        <label>
          <span>{$_('admin.principal')}</span>
          <input type="text" placeholder="abcde-12345-…" bind:value={grantPrincipal} />
        </label>
        <label>
          <span>{$_('admin.days')}</span>
          <input type="number" min="1" bind:value={grantDays} />
        </label>
        <label class="full">
          <span>{$_('admin.note')}</span>
          <input type="text" bind:value={grantNote} />
        </label>
      </div>
      <div class="form-actions">
        <button class="btn primary" disabled={!grantPrincipal.trim() || !!busy} on:click={grantByPrincipal}>{$_('admin.grant_btn')}</button>
      </div>
    </section>
  {:else}
    <section class="card">
      <h2>{$_('admin.admins_h', { values: { count: admins.length } })}</h2>
      <p class="muted small">{$_('admin.admins_note')}</p>
      <pre class="cmd">dfx canister call marketplace_backend add_admin '("&lt;principal&gt;")'
dfx canister call marketplace_backend remove_admin '("&lt;principal&gt;")'</pre>
      {#if admins.length === 0}
        <p class="muted">{$_('admin.no_admins')}</p>
      {:else}
        <ul class="principals">
          {#each admins as p}<li><code>{p}</code></li>{/each}
        </ul>
      {/if}
    </section>

    <section class="card">
      <h2>{$_('admin.reviewers_h', { values: { count: reviewers.length } })}</h2>
      <p class="muted small">{$_('admin.reviewers_note')}</p>
      {#if reviewers.length > 0}
        <ul class="principals">
          {#each reviewers as p}
            <li>
              <code>{p}</code>
              <button class="btn tiny ghost" disabled={!!busy} on:click={() => toggleReviewer(p, true)}>{$_('admin.remove')}</button>
            </li>
          {/each}
        </ul>
      {/if}
      <div class="inline-form">
        <input type="text" placeholder={$_('admin.principal')} bind:value={newReviewer} />
        <button class="btn primary" disabled={!newReviewer.trim() || !!busy} on:click={addReviewerByPrincipal}>{$_('admin.add_reviewer')}</button>
      </div>
    </section>

    {#if status}
      <section class="card">
        <h2>{$_('admin.config_h')}</h2>
        <ul class="config">
          <li><span>{$_('admin.file_registry')}</span><code>{status.file_registry_canister_id || $_('admin.unset')}</code></li>
          <li><span>{$_('admin.billing_principal')}</span><code>{status.billing_service_principal || $_('admin.unset')}</code></li>
          <li><span>{$_('admin.version')}</span><code>{status.version} ({status.commit.slice(0, 8)})</code></li>
        </ul>
        <p class="muted small">{$_('admin.config_note')}</p>
      </section>
    {/if}
  {/if}
{/if}

<style>
  .head h1 { margin: 0 0 0.25rem; font-size: 1.8rem; }
  .head p { margin: 0 0 1.5rem; color: var(--text-muted); }
  .card { background: var(--surface); border: 1px solid var(--border); padding: 1.25rem 1.5rem; border-radius: 0.75rem; margin-bottom: 1.25rem; }
  .card.warn { border-color: var(--border-strong); background: var(--surface-2); color: var(--text); }
  .card h2 { margin: 0 0 0.6rem; font-size: 1.1rem; }
  .stats { list-style: none; padding: 0; margin: 0 0 1.25rem; display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 0.6rem; }
  .stats li { background: var(--surface); border: 1px solid var(--border); padding: 0.7rem 0.9rem; border-radius: 0.6rem; display: flex; flex-direction: column; }
  .stats strong { font-size: 1.3rem; }
  .stats span { font-size: 0.75rem; color: var(--text-faint); }
  .tabs { display: flex; align-items: center; gap: 0.25rem; margin-bottom: 0.85rem; border-bottom: 1px solid var(--border); }
  .tabs button { border: none; background: none; padding: 0.6rem 0.9rem; color: var(--text-muted); font-size: 0.9rem; border-bottom: 2px solid transparent; margin-bottom: -1px; cursor: pointer; display: inline-flex; align-items: center; gap: 0.4rem; }
  .tabs button.active { color: var(--text); border-bottom-color: var(--primary); font-weight: 500; }
  .tabs .count { background: var(--primary); color: #fff; border-radius: 999px; font-size: 0.7rem; padding: 0.05rem 0.45rem; }
  .tabs .loading { margin-left: auto; transform: scale(0.6); }
  .filters { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 0.85rem; }
  .filters input[type='search'] { flex: 1; min-width: 180px; }
  .check { display: inline-flex; flex-direction: row; align-items: center; gap: 0.35rem; font-size: 0.85rem; color: var(--text-muted); }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.55rem; border-bottom: 1px solid var(--surface-2); font-size: 0.85rem; vertical-align: top; }
  th { color: var(--text-faint); font-weight: 500; white-space: nowrap; }
  tr.inactive td { opacity: 0.6; }
  td.actions { width: 1%; }
  .btns { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 0.25rem; min-width: 170px; max-width: 320px; margin-left: auto; }
  .btns .btn { white-space: nowrap; }
  .sub { font-size: 0.72rem; color: var(--text-faint); margin-top: 0.15rem; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  code { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.78rem; }
  .kind { font-size: 0.75rem; color: var(--text-muted); }
  .link { color: var(--accent); }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 0.7rem; background: var(--surface-2); color: var(--text-muted); margin-right: 0.2rem; }
  .pill.verified { background: #DCFCE7; color: #166534; }
  .pill.pending_review, .pill.pending_audit { background: #FEF3C7; color: #92400E; }
  .pill.rejected { background: #FEE2E2; color: #991B1B; }
  .pill.admin { background: #E0E7FF; color: #3730A3; }
  .pill.reviewer { background: #F3E8FF; color: #6B21A8; }
  .pill.off { background: var(--surface-3); }
  .ok { color: var(--success); }
  .muted { color: var(--text-faint); }
  .small { font-size: 0.8rem; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.85rem 1.25rem; margin-top: 0.85rem; }
  .grid label.full { grid-column: 1 / -1; }
  label { display: flex; flex-direction: column; gap: 0.3rem; }
  label span { font-size: 0.8rem; color: var(--text-faint); }
  input, select { border: 1px solid var(--border); border-radius: 0.4rem; padding: 0.5rem 0.7rem; background: var(--surface); font-size: 0.85rem; }
  .form-actions { display: flex; justify-content: flex-end; margin-top: 1rem; }
  .inline-form { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
  .inline-form input { flex: 1; }
  .principals { list-style: none; padding: 0; margin: 0.5rem 0 0; }
  .principals li { display: flex; align-items: center; justify-content: space-between; padding: 0.4rem 0; border-bottom: 1px solid var(--surface-2); }
  .cmd { background: var(--surface-2); padding: 0.6rem 0.8rem; border-radius: 0.4rem; font-size: 0.75rem; overflow-x: auto; }
  .config { list-style: none; padding: 0; margin: 0; }
  .config li { display: flex; justify-content: space-between; gap: 1rem; padding: 0.35rem 0; font-size: 0.85rem; }
  .config span { color: var(--text-faint); }
  .btn { border: 1px solid var(--border); background: var(--surface); color: var(--text-muted); padding: 0.5rem 1rem; border-radius: 0.4rem; cursor: pointer; }
  .btn:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
  .btn.danger { background: var(--danger); border-color: var(--danger); color: #fff; }
  .btn.ghost { background: transparent; }
  .btn.tiny { padding: 0.28rem 0.55rem; font-size: 0.73rem; }
  .state { text-align: center; padding: 1rem; color: var(--text-muted); }
  .state.error { color: var(--danger); }
  @media (max-width: 760px) {
    .grid { grid-template-columns: 1fr; }
  }
</style>
