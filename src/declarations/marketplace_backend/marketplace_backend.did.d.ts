import type { Principal } from '@icp-sdk/core/principal';
import type { ActorMethod } from '@icp-sdk/core/agent';
import type { IDL } from '@icp-sdk/core/candid';

export interface AdminListing {
  'updated_at' : number,
  'name' : string,
  'developer_name' : string,
  'verification_notes' : string,
  'installs' : bigint,
  'item_kind' : string,
  'created_at' : number,
  'verification_status' : string,
  'likes' : bigint,
  'version' : string,
  'is_active' : boolean,
  'item_id' : string,
  'file_registry_namespace' : string,
  'developer' : string,
}
export interface AdminUser {
  'is_suspended' : boolean,
  'principal' : string,
  'extensions_count' : bigint,
  'is_admin' : boolean,
  'is_reviewer' : boolean,
  'display_name' : string,
  'license_active' : boolean,
  'assistants_count' : bigint,
  'codices_count' : bigint,
  'license_note' : string,
  'license_payment_method' : string,
  'license_expires_at' : number,
  'suspended_reason' : string,
  'has_license' : boolean,
}
export interface AssistantInput {
  'categories' : string,
  'eval_report_url' : string,
  'training_data_summary' : string,
  'requested_role' : string,
  'icon' : string,
  'name' : string,
  'requested_permissions' : string,
  'languages' : string,
  'description' : string,
  'version' : string,
  'endpoint_url' : string,
  'domains' : string,
  'assistant_id' : string,
  'base_model' : string,
  'file_registry_canister_id' : string,
  'price_e8s' : bigint,
  'runtime' : string,
  'file_registry_namespace' : string,
  'pricing_summary' : string,
}
export interface AssistantListResult {
  'per_page' : bigint,
  'listings' : Array<AssistantListing>,
  'page' : bigint,
  'total_count' : bigint,
}
export interface AssistantListing {
  'categories' : string,
  'eval_report_url' : string,
  'training_data_summary' : string,
  'requested_role' : string,
  'updated_at' : number,
  'icon' : string,
  'name' : string,
  'developer_name' : string,
  'verification_notes' : string,
  'requested_permissions' : string,
  'languages' : string,
  'installs' : bigint,
  'description' : string,
  'created_at' : number,
  'verification_status' : string,
  'likes' : bigint,
  'version' : string,
  'endpoint_url' : string,
  'domains' : string,
  'assistant_id' : string,
  'base_model' : string,
  'assistant_alias' : string,
  'is_active' : boolean,
  'file_registry_canister_id' : string,
  'price_e8s' : bigint,
  'runtime' : string,
  'file_registry_namespace' : string,
  'developer' : string,
  'pricing_summary' : string,
}
export interface AssistantPage {
  'listings' : Array<AssistantListing>,
  'next_cursor' : bigint,
}
export type AssistantResult = { 'Ok' : AssistantListing } |
  { 'Err' : string };
export interface CodexInput {
  'categories' : string,
  'icon' : string,
  'name' : string,
  'description' : string,
  'version' : string,
  'codex_id' : string,
  'realm_type' : string,
  'file_registry_canister_id' : string,
  'price_e8s' : bigint,
  'file_registry_namespace' : string,
}
export interface CodexListResult {
  'per_page' : bigint,
  'listings' : Array<CodexListing>,
  'page' : bigint,
  'total_count' : bigint,
}
export interface CodexListing {
  'categories' : string,
  'updated_at' : number,
  'icon' : string,
  'name' : string,
  'developer_name' : string,
  'verification_notes' : string,
  'installs' : bigint,
  'description' : string,
  'created_at' : number,
  'verification_status' : string,
  'likes' : bigint,
  'version' : string,
  'codex_id' : string,
  'realm_type' : string,
  'is_active' : boolean,
  'file_registry_canister_id' : string,
  'codex_alias' : string,
  'price_e8s' : bigint,
  'file_registry_namespace' : string,
  'developer' : string,
}
export interface CodexPage {
  'listings' : Array<CodexListing>,
  'next_cursor' : bigint,
}
export type CodexResult = { 'Ok' : CodexListing } |
  { 'Err' : string };
export interface ConfigRecord {
  'billing_service_principal' : string,
  'license_price_usd_cents' : bigint,
  'license_duration_seconds' : bigint,
  'file_registry_canister_id' : string,
}
export type ConfigResult = { 'Ok' : ConfigRecord } |
  { 'Err' : string };
export interface DeveloperLicense {
  'last_payment_amount_usd_cents' : bigint,
  'principal' : string,
  'note' : string,
  'last_payment_id' : string,
  'created_at' : number,
  'payment_method' : string,
  'is_active' : boolean,
  'expires_at' : number,
}
export interface ExtensionInput {
  'categories' : string,
  'extension_id' : string,
  'icon' : string,
  'name' : string,
  'download_url' : string,
  'description' : string,
  'version' : string,
  'screenshots' : string,
  'file_registry_canister_id' : string,
  'price_e8s' : bigint,
  'file_registry_namespace' : string,
}
export interface ExtensionListResult {
  'per_page' : bigint,
  'listings' : Array<ExtensionListing>,
  'page' : bigint,
  'total_count' : bigint,
}
export interface ExtensionListing {
  'categories' : string,
  'updated_at' : number,
  'extension_id' : string,
  'icon' : string,
  'name' : string,
  'developer_name' : string,
  'verification_notes' : string,
  'installs' : bigint,
  'download_url' : string,
  'description' : string,
  'created_at' : number,
  'verification_status' : string,
  'likes' : bigint,
  'version' : string,
  'screenshots' : string,
  'is_active' : boolean,
  'file_registry_canister_id' : string,
  'price_e8s' : bigint,
  'file_registry_namespace' : string,
  'developer' : string,
}
export interface ExtensionPage {
  'listings' : Array<ExtensionListing>,
  'next_cursor' : bigint,
}
export type ExtensionResult = { 'Ok' : ExtensionListing } |
  { 'Err' : string };
export type GenericResult = { 'Ok' : string } |
  { 'Err' : string };
export type Header = [string, string];
export interface HttpRequest {
  'url' : string,
  'method' : string,
  'body' : Uint8Array | number[],
  'headers' : Array<Header>,
}
export interface HttpResponseIncoming {
  'body' : Uint8Array | number[],
  'headers' : Array<Header>,
  'upgrade' : [] | [boolean],
  'streaming_strategy' : [] | [string],
  'status_code' : number,
}
export interface LicensePaymentInput {
  'principal' : string,
  'duration_seconds' : bigint,
  'note' : string,
  'payment_method' : string,
  'stripe_session_id' : string,
  'amount_usd_cents' : bigint,
}
export interface LicensePricingRecord {
  'license_price_usd_cents' : bigint,
  'license_duration_seconds' : bigint,
}
export type LicenseResult = { 'Ok' : DeveloperLicense } |
  { 'Err' : string };
export interface LikeRecord {
  'item_kind' : string,
  'created_at' : number,
  'item_id' : string,
}
export interface MarketplaceInitArg {
  'billing_service_principal' : [] | [string],
  'file_registry' : [] | [string],
}
export interface MyRolesRecord {
  'is_admin' : boolean,
  'is_reviewer' : boolean,
  'is_controller' : boolean,
}
export interface PendingAudit {
  'updated_at' : number,
  'name' : string,
  'item_kind' : string,
  'version' : string,
  'item_id' : string,
  'developer' : string,
}
export interface PurchaseRecord {
  'purchased_at' : number,
  'item_kind' : string,
  'purchase_id' : string,
  'realm_principal' : string,
  'price_paid_e8s' : bigint,
  'item_id' : string,
  'developer' : string,
}
/**
 * Generated by scripts/marketplace_candid.py. Do not edit by hand, and do not commit a build's output:
 * a build writes every type it finds under src/ onto this file. To regenerate it and its declarations:
 * /tmp/marketplace-venv/bin/python scripts/marketplace_candid.py
 * (the script's docstring shows how to create that venv)
 */
export interface StatusRecord {
  'python_version' : string,
  'billing_service_principal' : string,
  'status' : string,
  'purchases_count' : bigint,
  'extensions_count' : bigint,
  'license_price_usd_cents' : bigint,
  'version' : string,
  'license_duration_seconds' : bigint,
  'dependencies' : Array<string>,
  'file_registry_canister_id' : string,
  'assistants_count' : bigint,
  'commit' : string,
  'codices_count' : bigint,
  'is_caller_controller' : boolean,
  'licenses_count' : bigint,
  'commit_datetime' : string,
  'likes_count' : bigint,
}
export type StatusResult = { 'Ok' : StatusRecord } |
  { 'Err' : string };
export interface _SERVICE {
  '__get_candid_interface_tmp_hack' : ActorMethod<[], string>,
  'add_admin' : ActorMethod<[string], GenericResult>,
  'add_reviewer' : ActorMethod<[string], GenericResult>,
  'admin_approve_namespace' : ActorMethod<[string, string], string>,
  'admin_grant_publisher' : ActorMethod<[string], string>,
  'admin_list_listings' : ActorMethod<[], Array<AdminListing>>,
  'admin_list_users' : ActorMethod<[], Array<AdminUser>>,
  'admin_set_developer_name' : ActorMethod<[string], string>,
  'admin_set_listing_active' : ActorMethod<
    [string, string, boolean],
    GenericResult
  >,
  'admin_set_user_suspended' : ActorMethod<
    [string, boolean, string],
    GenericResult
  >,
  'admin_status' : ActorMethod<[], string>,
  'buy_assistant' : ActorMethod<[string], GenericResult>,
  'buy_codex' : ActorMethod<[string], GenericResult>,
  'buy_extension' : ActorMethod<[string], GenericResult>,
  'check_license' : ActorMethod<[string], LicenseResult>,
  'check_upload' : ActorMethod<[string, string, string], string>,
  'create_assistant' : ActorMethod<[AssistantInput], GenericResult>,
  'create_codex' : ActorMethod<[CodexInput], GenericResult>,
  'create_extension' : ActorMethod<[ExtensionInput], GenericResult>,
  'delist_assistant' : ActorMethod<[string], GenericResult>,
  'delist_codex' : ActorMethod<[string], GenericResult>,
  'delist_extension' : ActorMethod<[string], GenericResult>,
  'get_assistant_details' : ActorMethod<[string], AssistantResult>,
  'get_billing_service_principal_q' : ActorMethod<[], string>,
  'get_casals_frontend_canister_id_q' : ActorMethod<[], string>,
  'get_codex_details' : ActorMethod<[string], CodexResult>,
  'get_extension_details' : ActorMethod<[string], ExtensionResult>,
  'get_file_registry_canister_id_q' : ActorMethod<[], string>,
  'get_license_pricing_q' : ActorMethod<[], LicensePricingRecord>,
  'get_license_status' : ActorMethod<[], LicenseResult>,
  'get_marketplace_config' : ActorMethod<[], ConfigResult>,
  'get_my_assistants' : ActorMethod<[], Array<AssistantListing>>,
  'get_my_codices' : ActorMethod<[], Array<CodexListing>>,
  'get_my_extensions' : ActorMethod<[], Array<ExtensionListing>>,
  'get_my_purchases' : ActorMethod<[], Array<PurchaseRecord>>,
  'get_my_roles' : ActorMethod<[], MyRolesRecord>,
  'get_purchases_enabled' : ActorMethod<[], boolean>,
  'grant_manual_license' : ActorMethod<[string, bigint, string], GenericResult>,
  'greet' : ActorMethod<[string], string>,
  'has_liked' : ActorMethod<[string, string, string], boolean>,
  'has_purchased_assistant' : ActorMethod<[string, string], boolean>,
  'has_purchased_codex' : ActorMethod<[string, string], boolean>,
  'has_purchased_extension' : ActorMethod<[string, string], boolean>,
  'http_request' : ActorMethod<[HttpRequest], HttpResponseIncoming>,
  'http_request_update' : ActorMethod<[HttpRequest], HttpResponseIncoming>,
  'like_item' : ActorMethod<[string, string], GenericResult>,
  'list_admins' : ActorMethod<[], Array<string>>,
  'list_marketplace_assistants' : ActorMethod<
    [bigint, bigint, boolean],
    AssistantListResult
  >,
  'list_marketplace_assistants_page' : ActorMethod<
    [bigint, bigint, boolean],
    AssistantPage
  >,
  'list_marketplace_codices' : ActorMethod<
    [bigint, bigint, boolean],
    CodexListResult
  >,
  'list_marketplace_codices_page' : ActorMethod<
    [bigint, bigint, boolean],
    CodexPage
  >,
  'list_marketplace_extensions' : ActorMethod<
    [bigint, bigint, boolean],
    ExtensionListResult
  >,
  'list_marketplace_extensions_page' : ActorMethod<
    [bigint, bigint, boolean],
    ExtensionPage
  >,
  'list_pending_audits' : ActorMethod<[], Array<PendingAudit>>,
  'list_reviewers' : ActorMethod<[], Array<string>>,
  'my_likes' : ActorMethod<[], Array<LikeRecord>>,
  'prepare_upload' : ActorMethod<[string, string, string], string>,
  'record_license_payment' : ActorMethod<[LicensePaymentInput], GenericResult>,
  'recount_listing_likes' : ActorMethod<[], GenericResult>,
  'remove_admin' : ActorMethod<[string], GenericResult>,
  'remove_reviewer' : ActorMethod<[string], GenericResult>,
  'request_audit' : ActorMethod<[string, string], GenericResult>,
  'review_listing' : ActorMethod<[string, string, boolean, string], string>,
  'revoke_license' : ActorMethod<[string], GenericResult>,
  'search_assistants' : ActorMethod<[string, boolean], Array<AssistantListing>>,
  'search_assistants_page' : ActorMethod<
    [string, bigint, bigint, boolean],
    AssistantPage
  >,
  'search_codices' : ActorMethod<[string, boolean], Array<CodexListing>>,
  'search_codices_page' : ActorMethod<
    [string, bigint, bigint, boolean],
    CodexPage
  >,
  'search_extensions' : ActorMethod<[string, boolean], Array<ExtensionListing>>,
  'search_extensions_page' : ActorMethod<
    [string, bigint, bigint, boolean],
    ExtensionPage
  >,
  'set_billing_service_principal' : ActorMethod<[string], GenericResult>,
  'set_casals_frontend_canister_id' : ActorMethod<[string], GenericResult>,
  'set_file_registry_canister_id' : ActorMethod<[string], GenericResult>,
  'set_license_pricing' : ActorMethod<[bigint, bigint], GenericResult>,
  'set_purchases_enabled' : ActorMethod<[boolean], GenericResult>,
  'set_verification_status' : ActorMethod<
    [string, string, string, string],
    GenericResult
  >,
  'status' : ActorMethod<[], StatusResult>,
  'top_assistants_by_downloads' : ActorMethod<
    [bigint, boolean],
    Array<AssistantListing>
  >,
  'top_assistants_by_likes' : ActorMethod<
    [bigint, boolean],
    Array<AssistantListing>
  >,
  'top_codices_by_downloads' : ActorMethod<
    [bigint, boolean],
    Array<CodexListing>
  >,
  'top_codices_by_likes' : ActorMethod<[bigint, boolean], Array<CodexListing>>,
  'top_extensions_by_downloads' : ActorMethod<
    [bigint, boolean],
    Array<ExtensionListing>
  >,
  'top_extensions_by_likes' : ActorMethod<
    [bigint, boolean],
    Array<ExtensionListing>
  >,
  'transfer_listing' : ActorMethod<[string, string, string], GenericResult>,
  'unlike_item' : ActorMethod<[string, string], GenericResult>,
  'update_assistant' : ActorMethod<[AssistantInput], GenericResult>,
  'update_codex' : ActorMethod<[CodexInput], GenericResult>,
  'update_extension' : ActorMethod<[ExtensionInput], GenericResult>,
}
export declare const idlFactory: IDL.InterfaceFactory;
export declare const init: (args: { IDL: typeof IDL }) => IDL.Type[];
