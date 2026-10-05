// Typed wrapper around the marketplace_backend actor.
//
// The auto-generated declarations cover the candid layer; this module
// adds two affordances:
//
//   * Result/Variant unwrapping: every {Ok, Err} variant gets unwrapped
//     into a discriminated union or a thrown error, depending on the
//     caller's preference.
//   * BigInt → number coercion for fields the UI treats as plain
//     numbers (likes, installs, timestamps).

import { marketplace } from './canisters';
import { builtinExtensions, builtinCodices } from './builtin-catalog';
import { listingsOrBuiltinFallback, paginateList } from './verified-filter';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toNumber(v: any): number {
  if (typeof v === 'bigint') return Number(v);
  if (typeof v === 'number') return v;
  if (v == null) return 0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function unwrap<T>(variant: any): T {
  if (!variant) throw new Error('empty result');
  if ('Ok' in variant) return variant.Ok as T;
  if ('Err' in variant) throw new Error(String(variant.Err));
  return variant as T;
}

// ---------------------------------------------------------------------------
// Types (mirroring the candid records)
// ---------------------------------------------------------------------------

export interface ExtensionListing {
  extension_id: string;
  developer: string;
  developer_name: string;
  name: string;
  description: string;
  version: string;
  price_e8s: number;
  icon: string;
  categories: string;
  screenshots: string;
  file_registry_canister_id: string;
  file_registry_namespace: string;
  download_url: string;
  installs: number;
  likes: number;
  verification_status: string;
  verification_notes: string;
  is_active: boolean;
  created_at: number;
  updated_at: number;
}

export interface CodexListing {
  codex_id: string;
  codex_alias: string;
  realm_type: string;
  developer: string;
  developer_name: string;
  name: string;
  description: string;
  version: string;
  price_e8s: number;
  icon: string;
  categories: string;
  file_registry_canister_id: string;
  file_registry_namespace: string;
  installs: number;
  likes: number;
  verification_status: string;
  verification_notes: string;
  is_active: boolean;
  created_at: number;
  updated_at: number;
}

export interface ExtensionInput {
  extension_id: string;
  name: string;
  description: string;
  version: string;
  price_e8s: bigint;
  icon: string;
  categories: string;
  screenshots: string;
  file_registry_canister_id: string;
  file_registry_namespace: string;
  download_url: string;
}

export interface CodexInput {
  codex_id: string;
  realm_type: string;
  name: string;
  description: string;
  version: string;
  price_e8s: bigint;
  icon: string;
  categories: string;
  file_registry_canister_id: string;
  file_registry_namespace: string;
}

export interface AssistantListing {
  assistant_id: string;
  assistant_alias: string;
  developer: string;
  developer_name: string;
  name: string;
  description: string;
  version: string;
  price_e8s: number;
  pricing_summary: string;
  icon: string;
  categories: string;
  runtime: string;
  endpoint_url: string;
  base_model: string;
  requested_role: string;
  requested_permissions: string;
  domains: string;
  languages: string;
  training_data_summary: string;
  eval_report_url: string;
  file_registry_canister_id: string;
  file_registry_namespace: string;
  installs: number;
  likes: number;
  verification_status: string;
  verification_notes: string;
  is_active: boolean;
  created_at: number;
  updated_at: number;
}

export interface AssistantInput {
  assistant_id: string;
  name: string;
  description: string;
  version: string;
  price_e8s: bigint;
  pricing_summary: string;
  icon: string;
  categories: string;
  runtime: string;
  endpoint_url: string;
  base_model: string;
  requested_role: string;
  requested_permissions: string;
  domains: string;
  languages: string;
  training_data_summary: string;
  eval_report_url: string;
  file_registry_canister_id: string;
  file_registry_namespace: string;
}

export interface PurchaseRecord {
  purchase_id: string;
  realm_principal: string;
  item_kind: string;
  item_id: string;
  developer: string;
  price_paid_e8s: number;
  purchased_at: number;
}

export interface LikeRecord {
  item_kind: string;
  item_id: string;
  created_at: number;
}

export interface DeveloperLicense {
  principal: string;
  created_at: number;
  expires_at: number;
  last_payment_id: string;
  last_payment_amount_usd_cents: number;
  payment_method: string;
  note: string;
  is_active: boolean;
}

export interface PendingAudit {
  item_kind: string;
  item_id: string;
  name: string;
  developer: string;
  version: string;
  updated_at: number;
}

export interface MarketplaceStatus {
  version: string;
  commit: string;
  commit_datetime: string;
  status: string;
  extensions_count: number;
  codices_count: number;
  assistants_count: number;
  purchases_count: number;
  likes_count: number;
  licenses_count: number;
  file_registry_canister_id: string;
  billing_service_principal: string;
  license_price_usd_cents: number;
  license_duration_seconds: number;
  is_caller_controller: boolean;
  dependencies: string[];
  python_version: string;
}

// ---------------------------------------------------------------------------
// Normalisers
// ---------------------------------------------------------------------------

function normExt(raw: any): ExtensionListing {
  return {
    extension_id: String(raw.extension_id ?? ''),
    developer: String(raw.developer ?? ''),
    developer_name: String(raw.developer_name ?? ''),
    name: String(raw.name ?? ''),
    description: String(raw.description ?? ''),
    version: String(raw.version ?? ''),
    price_e8s: toNumber(raw.price_e8s),
    icon: String(raw.icon ?? ''),
    categories: String(raw.categories ?? ''),
    screenshots: String(raw.screenshots ?? ''),
    file_registry_canister_id: String(raw.file_registry_canister_id ?? ''),
    file_registry_namespace: String(raw.file_registry_namespace ?? ''),
    download_url: String(raw.download_url ?? ''),
    installs: toNumber(raw.installs),
    likes: toNumber(raw.likes),
    verification_status: String(raw.verification_status ?? 'unverified'),
    verification_notes: String(raw.verification_notes ?? ''),
    is_active: Boolean(raw.is_active),
    created_at: toNumber(raw.created_at),
    updated_at: toNumber(raw.updated_at),
  };
}

function normAssistant(raw: any): AssistantListing {
  return {
    assistant_id: String(raw.assistant_id ?? ''),
    assistant_alias: String(raw.assistant_alias ?? ''),
    developer: String(raw.developer ?? ''),
    developer_name: String(raw.developer_name ?? ''),
    name: String(raw.name ?? ''),
    description: String(raw.description ?? ''),
    version: String(raw.version ?? ''),
    price_e8s: toNumber(raw.price_e8s),
    pricing_summary: String(raw.pricing_summary ?? ''),
    icon: String(raw.icon ?? ''),
    categories: String(raw.categories ?? ''),
    runtime: String(raw.runtime ?? ''),
    endpoint_url: String(raw.endpoint_url ?? ''),
    base_model: String(raw.base_model ?? ''),
    requested_role: String(raw.requested_role ?? ''),
    requested_permissions: String(raw.requested_permissions ?? ''),
    domains: String(raw.domains ?? ''),
    languages: String(raw.languages ?? ''),
    training_data_summary: String(raw.training_data_summary ?? ''),
    eval_report_url: String(raw.eval_report_url ?? ''),
    file_registry_canister_id: String(raw.file_registry_canister_id ?? ''),
    file_registry_namespace: String(raw.file_registry_namespace ?? ''),
    installs: toNumber(raw.installs),
    likes: toNumber(raw.likes),
    verification_status: String(raw.verification_status ?? 'unverified'),
    verification_notes: String(raw.verification_notes ?? ''),
    is_active: Boolean(raw.is_active),
    created_at: toNumber(raw.created_at),
    updated_at: toNumber(raw.updated_at),
  };
}


function normCodex(raw: any): CodexListing {
  return {
    codex_id: String(raw.codex_id ?? ''),
    codex_alias: String(raw.codex_alias ?? ''),
    realm_type: String(raw.realm_type ?? ''),
    developer: String(raw.developer ?? ''),
    developer_name: String(raw.developer_name ?? ''),
    name: String(raw.name ?? ''),
    description: String(raw.description ?? ''),
    version: String(raw.version ?? ''),
    price_e8s: toNumber(raw.price_e8s),
    icon: String(raw.icon ?? ''),
    categories: String(raw.categories ?? ''),
    file_registry_canister_id: String(raw.file_registry_canister_id ?? ''),
    file_registry_namespace: String(raw.file_registry_namespace ?? ''),
    installs: toNumber(raw.installs),
    likes: toNumber(raw.likes),
    verification_status: String(raw.verification_status ?? 'unverified'),
    verification_notes: String(raw.verification_notes ?? ''),
    is_active: Boolean(raw.is_active),
    created_at: toNumber(raw.created_at),
    updated_at: toNumber(raw.updated_at),
  };
}

function normPurchase(raw: any): PurchaseRecord {
  return {
    purchase_id: String(raw.purchase_id ?? ''),
    realm_principal: String(raw.realm_principal ?? ''),
    item_kind: String(raw.item_kind ?? ''),
    item_id: String(raw.item_id ?? ''),
    developer: String(raw.developer ?? ''),
    price_paid_e8s: toNumber(raw.price_paid_e8s),
    purchased_at: toNumber(raw.purchased_at),
  };
}

function normLike(raw: any): LikeRecord {
  return {
    item_kind: String(raw.item_kind ?? ''),
    item_id: String(raw.item_id ?? ''),
    created_at: toNumber(raw.created_at),
  };
}

function normLicense(raw: any): DeveloperLicense {
  return {
    principal: String(raw.principal ?? ''),
    created_at: toNumber(raw.created_at),
    expires_at: toNumber(raw.expires_at),
    last_payment_id: String(raw.last_payment_id ?? ''),
    last_payment_amount_usd_cents: toNumber(raw.last_payment_amount_usd_cents),
    payment_method: String(raw.payment_method ?? ''),
    note: String(raw.note ?? ''),
    is_active: Boolean(raw.is_active),
  };
}

function normPendingAudit(raw: any): PendingAudit {
  return {
    item_kind: String(raw.item_kind ?? ''),
    item_id: String(raw.item_id ?? ''),
    name: String(raw.name ?? ''),
    developer: String(raw.developer ?? ''),
    version: String(raw.version ?? ''),
    updated_at: toNumber(raw.updated_at),
  };
}

function fallbackExtensions(canisterItems: ExtensionListing[], verifiedOnly: boolean): ExtensionListing[] {
  return listingsOrBuiltinFallback(canisterItems, builtinExtensions, verifiedOnly);
}

function fallbackCodices(canisterItems: CodexListing[], verifiedOnly: boolean): CodexListing[] {
  return listingsOrBuiltinFallback(canisterItems, builtinCodices, verifiedOnly);
}

function matchesQuery(text: string, q: string): boolean {
  const lower = q.toLowerCase();
  return text.toLowerCase().includes(lower);
}

function normStatus(raw: any): MarketplaceStatus {
  return {
    version: String(raw.version ?? ''),
    commit: String(raw.commit ?? ''),
    commit_datetime: String(raw.commit_datetime ?? ''),
    status: String(raw.status ?? ''),
    extensions_count: toNumber(raw.extensions_count),
    codices_count: toNumber(raw.codices_count),
    assistants_count: toNumber(raw.assistants_count),
    purchases_count: toNumber(raw.purchases_count),
    likes_count: toNumber(raw.likes_count),
    licenses_count: toNumber(raw.licenses_count),
    file_registry_canister_id: String(raw.file_registry_canister_id ?? ''),
    billing_service_principal: String(raw.billing_service_principal ?? ''),
    license_price_usd_cents: toNumber(raw.license_price_usd_cents),
    license_duration_seconds: toNumber(raw.license_duration_seconds),
    is_caller_controller: Boolean(raw.is_caller_controller),
    dependencies: Array.isArray(raw.dependencies) ? raw.dependencies.map(String) : [],
    python_version: String(raw.python_version ?? ''),
  };
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const marketplaceClient = {
  // --- status / config -------------------------------------------------
  async getStatus(): Promise<MarketplaceStatus> {
    const r = await marketplace.status();
    return normStatus(unwrap<any>(r));
  },
  async getFileRegistryCanisterId(): Promise<string> {
    return String(await marketplace.get_file_registry_canister_id_q());
  },
  async getLicensePricing() {
    const r = await marketplace.get_license_pricing_q();
    return {
      license_price_usd_cents: toNumber(r.license_price_usd_cents),
      license_duration_seconds: toNumber(r.license_duration_seconds),
    };
  },

  // --- extensions ------------------------------------------------------
  async createExtension(input: ExtensionInput): Promise<string> {
    const r = await marketplace.create_extension(input);
    return unwrap<string>(r);
  },
  async updateExtension(input: ExtensionInput): Promise<string> {
    const r = await marketplace.update_extension(input);
    return unwrap<string>(r);
  },
  async delistExtension(extensionId: string): Promise<string> {
    const r = await marketplace.delist_extension(extensionId);
    return unwrap<string>(r);
  },
  async getExtensionDetails(id: string): Promise<ExtensionListing> {
    try {
      const r = await marketplace.get_extension_details(id);
      return normExt(unwrap<any>(r));
    } catch {
      const found = builtinExtensions.find((e) => e.extension_id === id);
      if (found) return found;
      throw new Error(`Extension '${id}' not found`);
    }
  },
  async listExtensions(page: number, perPage: number, verifiedOnly = true): Promise<{
    listings: ExtensionListing[];
    total_count: number;
    page: number;
    per_page: number;
  }> {
    try {
      const r = await marketplace.list_marketplace_extensions(BigInt(page), BigInt(perPage), verifiedOnly);
      const listings = r.listings.map(normExt);
      if (listings.length > 0) {
        return { listings, total_count: toNumber(r.total_count), page: toNumber(r.page), per_page: toNumber(r.per_page) };
      }
    } catch { /* fall through — empty or offline, never fake Verified */ }
    return paginateList(fallbackExtensions([], verifiedOnly), page, perPage);
  },
  async searchExtensions(q: string, verifiedOnly = true): Promise<ExtensionListing[]> {
    try {
      const r = await marketplace.search_extensions(q, verifiedOnly);
      const items = (r as any[]).map(normExt);
      if (items.length > 0) return items;
    } catch { /* fall through */ }
    const matches = builtinExtensions.filter(
      (e) => matchesQuery(e.name, q) || matchesQuery(e.description, q) || matchesQuery(e.categories, q),
    );
    return listingsOrBuiltinFallback([], matches, verifiedOnly);
  },
  async getMyExtensions(): Promise<ExtensionListing[]> {
    const r = await marketplace.get_my_extensions();
    return (r as any[]).map(normExt);
  },

  // --- codices ---------------------------------------------------------
  async createCodex(input: CodexInput): Promise<string> {
    const r = await marketplace.create_codex(input);
    return unwrap<string>(r);
  },
  async delistCodex(codexId: string): Promise<string> {
    const r = await marketplace.delist_codex(codexId);
    return unwrap<string>(r);
  },
  async getCodexDetails(id: string): Promise<CodexListing> {
    try {
      const r = await marketplace.get_codex_details(id);
      return normCodex(unwrap<any>(r));
    } catch {
      const found = builtinCodices.find((c) => c.codex_id === id);
      if (found) return found;
      throw new Error(`Codex '${id}' not found`);
    }
  },
  async listCodices(page: number, perPage: number, verifiedOnly = true): Promise<{
    listings: CodexListing[];
    total_count: number;
    page: number;
    per_page: number;
  }> {
    try {
      const r = await marketplace.list_marketplace_codices(BigInt(page), BigInt(perPage), verifiedOnly);
      const listings = r.listings.map(normCodex);
      if (listings.length > 0) {
        return { listings, total_count: toNumber(r.total_count), page: toNumber(r.page), per_page: toNumber(r.per_page) };
      }
    } catch { /* fall through — empty or offline, never fake Verified */ }
    return paginateList(fallbackCodices([], verifiedOnly), page, perPage);
  },
  async searchCodices(q: string, verifiedOnly = true): Promise<CodexListing[]> {
    try {
      const r = await marketplace.search_codices(q, verifiedOnly);
      const items = (r as any[]).map(normCodex);
      if (items.length > 0) return items;
    } catch { /* fall through */ }
    const matches = builtinCodices.filter(
      (c) => matchesQuery(c.name, q) || matchesQuery(c.description, q) || matchesQuery(c.categories, q),
    );
    return listingsOrBuiltinFallback([], matches, verifiedOnly);
  },
  async getMyCodices(): Promise<CodexListing[]> {
    const r = await marketplace.get_my_codices();
    return (r as any[]).map(normCodex);
  },

  // --- assistants -----------------------------------------------------
  async createAssistant(input: AssistantInput): Promise<string> {
    const r = await marketplace.create_assistant(input);
    return unwrap<string>(r);
  },
  async updateAssistant(input: AssistantInput): Promise<string> {
    const r = await marketplace.update_assistant(input);
    return unwrap<string>(r);
  },
  async delistAssistant(assistantId: string): Promise<string> {
    const r = await marketplace.delist_assistant(assistantId);
    return unwrap<string>(r);
  },
  async getAssistantDetails(id: string): Promise<AssistantListing> {
    const r = await marketplace.get_assistant_details(id);
    return normAssistant(unwrap<any>(r));
  },
  async listAssistants(page: number, perPage: number, verifiedOnly = true): Promise<{
    listings: AssistantListing[];
    total_count: number;
    page: number;
    per_page: number;
  }> {
    const r = await marketplace.list_marketplace_assistants(BigInt(page), BigInt(perPage), verifiedOnly);
    return {
      listings: r.listings.map(normAssistant),
      total_count: toNumber(r.total_count),
      page: toNumber(r.page),
      per_page: toNumber(r.per_page),
    };
  },
  async searchAssistants(q: string, verifiedOnly = true): Promise<AssistantListing[]> {
    const r = await marketplace.search_assistants(q, verifiedOnly);
    return (r as any[]).map(normAssistant);
  },
  async getMyAssistants(): Promise<AssistantListing[]> {
    const r = await marketplace.get_my_assistants();
    return (r as any[]).map(normAssistant);
  },
  async buyAssistant(id: string): Promise<string> {
    return unwrap<string>(await marketplace.buy_assistant(id));
  },
  async hasPurchasedAssistant(realm: string, id: string): Promise<boolean> {
    return Boolean(await marketplace.has_purchased_assistant(realm, id));
  },

  // --- purchases / likes ----------------------------------------------
  async buyExtension(id: string): Promise<string> {
    return unwrap<string>(await marketplace.buy_extension(id));
  },
  async buyCodex(id: string): Promise<string> {
    return unwrap<string>(await marketplace.buy_codex(id));
  },
  async hasPurchasedExtension(realm: string, id: string): Promise<boolean> {
    return Boolean(await marketplace.has_purchased_extension(realm, id));
  },
  async hasPurchasedCodex(realm: string, id: string): Promise<boolean> {
    return Boolean(await marketplace.has_purchased_codex(realm, id));
  },
  async getMyPurchases(): Promise<PurchaseRecord[]> {
    const r = await marketplace.get_my_purchases();
    return (r as any[]).map(normPurchase);
  },
  async likeItem(kind: 'ext' | 'codex' | 'assistant', id: string): Promise<string> {
    return unwrap<string>(await marketplace.like_item(kind, id));
  },
  async unlikeItem(kind: 'ext' | 'codex' | 'assistant', id: string): Promise<string> {
    return unwrap<string>(await marketplace.unlike_item(kind, id));
  },
  async hasLiked(principal: string, kind: 'ext' | 'codex' | 'assistant', id: string): Promise<boolean> {
    return Boolean(await marketplace.has_liked(principal, kind, id));
  },
  async myLikes(): Promise<LikeRecord[]> {
    const r = await marketplace.my_likes();
    return (r as any[]).map(normLike);
  },

  // --- rankings --------------------------------------------------------
  async topExtensionsByDownloads(n = 20, verifiedOnly = true): Promise<ExtensionListing[]> {
    try {
      const r = await marketplace.top_extensions_by_downloads(BigInt(n), verifiedOnly);
      const items = (r as any[]).map(normExt);
      if (items.length > 0) return items;
    } catch { /* fall through */ }
    return fallbackExtensions([], verifiedOnly).slice(0, n);
  },
  async topExtensionsByLikes(n = 20, verifiedOnly = true): Promise<ExtensionListing[]> {
    try {
      const r = await marketplace.top_extensions_by_likes(BigInt(n), verifiedOnly);
      const items = (r as any[]).map(normExt);
      if (items.length > 0) return items;
    } catch { /* fall through */ }
    return fallbackExtensions([], verifiedOnly).slice(0, n);
  },
  async topCodicesByDownloads(n = 20, verifiedOnly = true): Promise<CodexListing[]> {
    try {
      const r = await marketplace.top_codices_by_downloads(BigInt(n), verifiedOnly);
      const items = (r as any[]).map(normCodex);
      if (items.length > 0) return items;
    } catch { /* fall through */ }
    return fallbackCodices([], verifiedOnly).slice(0, n);
  },
  async topCodicesByLikes(n = 20, verifiedOnly = true): Promise<CodexListing[]> {
    try {
      const r = await marketplace.top_codices_by_likes(BigInt(n), verifiedOnly);
      const items = (r as any[]).map(normCodex);
      if (items.length > 0) return items;
    } catch { /* fall through */ }
    return fallbackCodices([], verifiedOnly).slice(0, n);
  },
  async topAssistantsByDownloads(n = 20, verifiedOnly = true): Promise<AssistantListing[]> {
    const r = await marketplace.top_assistants_by_downloads(BigInt(n), verifiedOnly);
    return (r as any[]).map(normAssistant);
  },
  async topAssistantsByLikes(n = 20, verifiedOnly = true): Promise<AssistantListing[]> {
    const r = await marketplace.top_assistants_by_likes(BigInt(n), verifiedOnly);
    return (r as any[]).map(normAssistant);
  },

  // --- licenses --------------------------------------------------------
  async checkLicense(principal: string): Promise<DeveloperLicense> {
    const r = await marketplace.check_license(principal);
    return normLicense(unwrap<any>(r));
  },
  async getLicenseStatus(): Promise<DeveloperLicense | null> {
    try {
      const r = await marketplace.get_license_status();
      return normLicense(unwrap<any>(r));
    } catch {
      return null;
    }
  },
  async grantManualLicense(principal: string, durationSeconds: number, note: string): Promise<string> {
    return unwrap<string>(await marketplace.grant_manual_license(principal, BigInt(durationSeconds), note));
  },
  async revokeLicense(principal: string): Promise<string> {
    return unwrap<string>(await marketplace.revoke_license(principal));
  },

  // --- verification ----------------------------------------------------
  async requestAudit(kind: 'ext' | 'codex' | 'assistant', id: string): Promise<string> {
    return unwrap<string>(await marketplace.request_audit(kind, id));
  },
  async setVerificationStatus(kind: 'ext' | 'codex' | 'assistant', id: string, status: string, notes: string): Promise<string> {
    return unwrap<string>(await marketplace.set_verification_status(kind, id, status, notes));
  },
  async listPendingAudits(): Promise<PendingAudit[]> {
    const r = await marketplace.list_pending_audits();
    return (r as any[]).map(normPendingAudit);
  },
};

export type {
  ExtensionListing as ExtensionListingType,
  CodexListing as CodexListingType,
  AssistantListing as AssistantListingType,
};
