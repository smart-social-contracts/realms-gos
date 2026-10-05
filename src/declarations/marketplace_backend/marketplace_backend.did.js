export const idlFactory = ({ IDL }) => {
  const MarketplaceInitArg = IDL.Record({
    'billing_service_principal' : IDL.Opt(IDL.Text),
    'file_registry' : IDL.Opt(IDL.Text),
  });
  const GenericResult = IDL.Variant({ 'Ok' : IDL.Text, 'Err' : IDL.Text });
  const DeveloperLicense = IDL.Record({
    'last_payment_amount_usd_cents' : IDL.Nat64,
    'principal' : IDL.Text,
    'note' : IDL.Text,
    'last_payment_id' : IDL.Text,
    'created_at' : IDL.Float64,
    'payment_method' : IDL.Text,
    'is_active' : IDL.Bool,
    'expires_at' : IDL.Float64,
  });
  const LicenseResult = IDL.Variant({
    'Ok' : DeveloperLicense,
    'Err' : IDL.Text,
  });
  const AssistantInput = IDL.Record({
    'categories' : IDL.Text,
    'eval_report_url' : IDL.Text,
    'training_data_summary' : IDL.Text,
    'requested_role' : IDL.Text,
    'icon' : IDL.Text,
    'name' : IDL.Text,
    'requested_permissions' : IDL.Text,
    'languages' : IDL.Text,
    'description' : IDL.Text,
    'version' : IDL.Text,
    'endpoint_url' : IDL.Text,
    'domains' : IDL.Text,
    'assistant_id' : IDL.Text,
    'base_model' : IDL.Text,
    'file_registry_canister_id' : IDL.Text,
    'price_e8s' : IDL.Nat64,
    'runtime' : IDL.Text,
    'file_registry_namespace' : IDL.Text,
    'pricing_summary' : IDL.Text,
  });
  const CodexInput = IDL.Record({
    'categories' : IDL.Text,
    'icon' : IDL.Text,
    'name' : IDL.Text,
    'description' : IDL.Text,
    'version' : IDL.Text,
    'codex_id' : IDL.Text,
    'realm_type' : IDL.Text,
    'file_registry_canister_id' : IDL.Text,
    'price_e8s' : IDL.Nat64,
    'file_registry_namespace' : IDL.Text,
  });
  const ExtensionInput = IDL.Record({
    'categories' : IDL.Text,
    'extension_id' : IDL.Text,
    'icon' : IDL.Text,
    'name' : IDL.Text,
    'download_url' : IDL.Text,
    'description' : IDL.Text,
    'version' : IDL.Text,
    'screenshots' : IDL.Text,
    'file_registry_canister_id' : IDL.Text,
    'price_e8s' : IDL.Nat64,
    'file_registry_namespace' : IDL.Text,
  });
  const AssistantListing = IDL.Record({
    'categories' : IDL.Text,
    'eval_report_url' : IDL.Text,
    'training_data_summary' : IDL.Text,
    'requested_role' : IDL.Text,
    'updated_at' : IDL.Float64,
    'icon' : IDL.Text,
    'name' : IDL.Text,
    'verification_notes' : IDL.Text,
    'requested_permissions' : IDL.Text,
    'languages' : IDL.Text,
    'installs' : IDL.Nat64,
    'description' : IDL.Text,
    'created_at' : IDL.Float64,
    'verification_status' : IDL.Text,
    'likes' : IDL.Nat64,
    'version' : IDL.Text,
    'endpoint_url' : IDL.Text,
    'domains' : IDL.Text,
    'assistant_id' : IDL.Text,
    'base_model' : IDL.Text,
    'assistant_alias' : IDL.Text,
    'is_active' : IDL.Bool,
    'file_registry_canister_id' : IDL.Text,
    'price_e8s' : IDL.Nat64,
    'runtime' : IDL.Text,
    'file_registry_namespace' : IDL.Text,
    'developer' : IDL.Text,
    'developer_name' : IDL.Text,
    'pricing_summary' : IDL.Text,
  });
  const AssistantResult = IDL.Variant({
    'Ok' : AssistantListing,
    'Err' : IDL.Text,
  });
  const CodexListing = IDL.Record({
    'categories' : IDL.Text,
    'updated_at' : IDL.Float64,
    'icon' : IDL.Text,
    'name' : IDL.Text,
    'verification_notes' : IDL.Text,
    'installs' : IDL.Nat64,
    'description' : IDL.Text,
    'created_at' : IDL.Float64,
    'verification_status' : IDL.Text,
    'likes' : IDL.Nat64,
    'version' : IDL.Text,
    'codex_id' : IDL.Text,
    'realm_type' : IDL.Text,
    'is_active' : IDL.Bool,
    'file_registry_canister_id' : IDL.Text,
    'codex_alias' : IDL.Text,
    'price_e8s' : IDL.Nat64,
    'file_registry_namespace' : IDL.Text,
    'developer' : IDL.Text,
    'developer_name' : IDL.Text,
  });
  const CodexResult = IDL.Variant({ 'Ok' : CodexListing, 'Err' : IDL.Text });
  const ExtensionListing = IDL.Record({
    'categories' : IDL.Text,
    'updated_at' : IDL.Float64,
    'extension_id' : IDL.Text,
    'icon' : IDL.Text,
    'name' : IDL.Text,
    'verification_notes' : IDL.Text,
    'installs' : IDL.Nat64,
    'download_url' : IDL.Text,
    'description' : IDL.Text,
    'created_at' : IDL.Float64,
    'verification_status' : IDL.Text,
    'likes' : IDL.Nat64,
    'version' : IDL.Text,
    'screenshots' : IDL.Text,
    'is_active' : IDL.Bool,
    'file_registry_canister_id' : IDL.Text,
    'price_e8s' : IDL.Nat64,
    'file_registry_namespace' : IDL.Text,
    'developer' : IDL.Text,
    'developer_name' : IDL.Text,
  });
  const ExtensionResult = IDL.Variant({
    'Ok' : ExtensionListing,
    'Err' : IDL.Text,
  });
  const LicensePricingRecord = IDL.Record({
    'license_price_usd_cents' : IDL.Nat64,
    'license_duration_seconds' : IDL.Nat64,
  });
  const ConfigRecord = IDL.Record({
    'billing_service_principal' : IDL.Text,
    'license_price_usd_cents' : IDL.Nat64,
    'license_duration_seconds' : IDL.Nat64,
    'file_registry_canister_id' : IDL.Text,
  });
  const ConfigResult = IDL.Variant({ 'Ok' : ConfigRecord, 'Err' : IDL.Text });
  const PurchaseRecord = IDL.Record({
    'purchased_at' : IDL.Float64,
    'item_kind' : IDL.Text,
    'purchase_id' : IDL.Text,
    'realm_principal' : IDL.Text,
    'price_paid_e8s' : IDL.Nat64,
    'item_id' : IDL.Text,
    'developer' : IDL.Text,
  });
  const Header = IDL.Tuple(IDL.Text, IDL.Text);
  const HttpRequest = IDL.Record({
    'url' : IDL.Text,
    'method' : IDL.Text,
    'body' : IDL.Vec(IDL.Nat8),
    'headers' : IDL.Vec(Header),
  });
  const HttpResponseIncoming = IDL.Record({
    'body' : IDL.Vec(IDL.Nat8),
    'headers' : IDL.Vec(Header),
    'upgrade' : IDL.Opt(IDL.Bool),
    'streaming_strategy' : IDL.Opt(IDL.Text),
    'status_code' : IDL.Nat16,
  });
  const AssistantListResult = IDL.Record({
    'per_page' : IDL.Nat64,
    'listings' : IDL.Vec(AssistantListing),
    'page' : IDL.Nat64,
    'total_count' : IDL.Nat64,
  });
  const CodexListResult = IDL.Record({
    'per_page' : IDL.Nat64,
    'listings' : IDL.Vec(CodexListing),
    'page' : IDL.Nat64,
    'total_count' : IDL.Nat64,
  });
  const ExtensionListResult = IDL.Record({
    'per_page' : IDL.Nat64,
    'listings' : IDL.Vec(ExtensionListing),
    'page' : IDL.Nat64,
    'total_count' : IDL.Nat64,
  });
  const PendingAudit = IDL.Record({
    'updated_at' : IDL.Float64,
    'name' : IDL.Text,
    'item_kind' : IDL.Text,
    'version' : IDL.Text,
    'item_id' : IDL.Text,
    'developer' : IDL.Text,
  });
  const LikeRecord = IDL.Record({
    'item_kind' : IDL.Text,
    'created_at' : IDL.Float64,
    'item_id' : IDL.Text,
  });
  const LicensePaymentInput = IDL.Record({
    'principal' : IDL.Text,
    'duration_seconds' : IDL.Nat64,
    'note' : IDL.Text,
    'payment_method' : IDL.Text,
    'stripe_session_id' : IDL.Text,
    'amount_usd_cents' : IDL.Nat64,
  });
  const StatusRecord = IDL.Record({
    'python_version' : IDL.Text,
    'billing_service_principal' : IDL.Text,
    'status' : IDL.Text,
    'purchases_count' : IDL.Nat64,
    'extensions_count' : IDL.Nat64,
    'license_price_usd_cents' : IDL.Nat64,
    'version' : IDL.Text,
    'license_duration_seconds' : IDL.Nat64,
    'dependencies' : IDL.Vec(IDL.Text),
    'file_registry_canister_id' : IDL.Text,
    'assistants_count' : IDL.Nat64,
    'commit' : IDL.Text,
    'codices_count' : IDL.Nat64,
    'is_caller_controller' : IDL.Bool,
    'licenses_count' : IDL.Nat64,
    'commit_datetime' : IDL.Text,
    'likes_count' : IDL.Nat64,
  });
  const StatusResult = IDL.Variant({ 'Ok' : StatusRecord, 'Err' : IDL.Text });
  return IDL.Service({
    '__get_candid_interface_tmp_hack' : IDL.Func([], [IDL.Text], ['query']),
    'add_reviewer' : IDL.Func([IDL.Text], [GenericResult], []),
    'admin_approve_namespace' : IDL.Func([IDL.Text, IDL.Text], [IDL.Text], []),
    'buy_assistant' : IDL.Func([IDL.Text], [GenericResult], []),
    'buy_codex' : IDL.Func([IDL.Text], [GenericResult], []),
    'buy_extension' : IDL.Func([IDL.Text], [GenericResult], []),
    'check_license' : IDL.Func([IDL.Text], [LicenseResult], ['query']),
    'create_assistant' : IDL.Func([AssistantInput], [GenericResult], []),
    'create_codex' : IDL.Func([CodexInput], [GenericResult], []),
    'create_extension' : IDL.Func([ExtensionInput], [GenericResult], []),
    'delist_assistant' : IDL.Func([IDL.Text], [GenericResult], []),
    'transfer_listing' : IDL.Func(
        [IDL.Text, IDL.Text, IDL.Text],
        [GenericResult],
        [],
      ),
    'delist_codex' : IDL.Func([IDL.Text], [GenericResult], []),
    'delist_extension' : IDL.Func([IDL.Text], [GenericResult], []),
    'get_assistant_details' : IDL.Func(
        [IDL.Text],
        [AssistantResult],
        ['query'],
      ),
    'get_billing_service_principal_q' : IDL.Func([], [IDL.Text], ['query']),
    'get_casals_frontend_canister_id_q' : IDL.Func([], [IDL.Text], ['query']),
    'get_codex_details' : IDL.Func([IDL.Text], [CodexResult], ['query']),
    'get_extension_details' : IDL.Func(
        [IDL.Text],
        [ExtensionResult],
        ['query'],
      ),
    'get_file_registry_canister_id_q' : IDL.Func([], [IDL.Text], ['query']),
    'get_license_pricing_q' : IDL.Func([], [LicensePricingRecord], ['query']),
    'get_license_status' : IDL.Func([], [LicenseResult], ['query']),
    'get_marketplace_config' : IDL.Func([], [ConfigResult], ['query']),
    'get_my_assistants' : IDL.Func([], [IDL.Vec(AssistantListing)], ['query']),
    'get_my_codices' : IDL.Func([], [IDL.Vec(CodexListing)], ['query']),
    'get_my_extensions' : IDL.Func([], [IDL.Vec(ExtensionListing)], ['query']),
    'get_my_purchases' : IDL.Func([], [IDL.Vec(PurchaseRecord)], ['query']),
    'grant_manual_license' : IDL.Func(
        [IDL.Text, IDL.Nat64, IDL.Text],
        [GenericResult],
        [],
      ),
    'greet' : IDL.Func([IDL.Text], [IDL.Text], ['query']),
    'has_liked' : IDL.Func(
        [IDL.Text, IDL.Text, IDL.Text],
        [IDL.Bool],
        ['query'],
      ),
    'has_purchased_assistant' : IDL.Func(
        [IDL.Text, IDL.Text],
        [IDL.Bool],
        ['query'],
      ),
    'has_purchased_codex' : IDL.Func(
        [IDL.Text, IDL.Text],
        [IDL.Bool],
        ['query'],
      ),
    'has_purchased_extension' : IDL.Func(
        [IDL.Text, IDL.Text],
        [IDL.Bool],
        ['query'],
      ),
    'http_request' : IDL.Func([HttpRequest], [HttpResponseIncoming], ['query']),
    'http_request_update' : IDL.Func([HttpRequest], [HttpResponseIncoming], []),
    'like_item' : IDL.Func([IDL.Text, IDL.Text], [GenericResult], []),
    'list_marketplace_assistants' : IDL.Func(
        [IDL.Nat64, IDL.Nat64, IDL.Bool],
        [AssistantListResult],
        ['query'],
      ),
    'list_marketplace_codices' : IDL.Func(
        [IDL.Nat64, IDL.Nat64, IDL.Bool],
        [CodexListResult],
        ['query'],
      ),
    'list_marketplace_extensions' : IDL.Func(
        [IDL.Nat64, IDL.Nat64, IDL.Bool],
        [ExtensionListResult],
        ['query'],
      ),
    'list_pending_audits' : IDL.Func([], [IDL.Vec(PendingAudit)], ['query']),
    'list_reviewers' : IDL.Func([], [IDL.Vec(IDL.Text)], ['query']),
    'my_likes' : IDL.Func([], [IDL.Vec(LikeRecord)], ['query']),
    'record_license_payment' : IDL.Func(
        [LicensePaymentInput],
        [GenericResult],
        [],
      ),
    'recount_listing_likes' : IDL.Func([], [GenericResult], []),
    'remove_reviewer' : IDL.Func([IDL.Text], [GenericResult], []),
    'request_audit' : IDL.Func([IDL.Text, IDL.Text], [GenericResult], []),
    'review_listing' : IDL.Func(
        [IDL.Text, IDL.Text, IDL.Bool, IDL.Text],
        [IDL.Text],
        [],
      ),
    'revoke_license' : IDL.Func([IDL.Text], [GenericResult], []),
    'search_assistants' : IDL.Func(
        [IDL.Text, IDL.Bool],
        [IDL.Vec(AssistantListing)],
        ['query'],
      ),
    'search_codices' : IDL.Func(
        [IDL.Text, IDL.Bool],
        [IDL.Vec(CodexListing)],
        ['query'],
      ),
    'search_extensions' : IDL.Func(
        [IDL.Text, IDL.Bool],
        [IDL.Vec(ExtensionListing)],
        ['query'],
      ),
    'set_billing_service_principal' : IDL.Func([IDL.Text], [GenericResult], []),
    'set_casals_frontend_canister_id' : IDL.Func(
        [IDL.Text],
        [GenericResult],
        [],
      ),
    'set_file_registry_canister_id' : IDL.Func([IDL.Text], [GenericResult], []),
    'set_license_pricing' : IDL.Func(
        [IDL.Nat64, IDL.Nat64],
        [GenericResult],
        [],
      ),
    'set_verification_status' : IDL.Func(
        [IDL.Text, IDL.Text, IDL.Text, IDL.Text],
        [GenericResult],
        [],
      ),
    'status' : IDL.Func([], [StatusResult], ['query']),
    'top_assistants_by_downloads' : IDL.Func(
        [IDL.Nat64, IDL.Bool],
        [IDL.Vec(AssistantListing)],
        ['query'],
      ),
    'top_assistants_by_likes' : IDL.Func(
        [IDL.Nat64, IDL.Bool],
        [IDL.Vec(AssistantListing)],
        ['query'],
      ),
    'top_codices_by_downloads' : IDL.Func(
        [IDL.Nat64, IDL.Bool],
        [IDL.Vec(CodexListing)],
        ['query'],
      ),
    'top_codices_by_likes' : IDL.Func(
        [IDL.Nat64, IDL.Bool],
        [IDL.Vec(CodexListing)],
        ['query'],
      ),
    'top_extensions_by_downloads' : IDL.Func(
        [IDL.Nat64, IDL.Bool],
        [IDL.Vec(ExtensionListing)],
        ['query'],
      ),
    'top_extensions_by_likes' : IDL.Func(
        [IDL.Nat64, IDL.Bool],
        [IDL.Vec(ExtensionListing)],
        ['query'],
      ),
    'unlike_item' : IDL.Func([IDL.Text, IDL.Text], [GenericResult], []),
    'update_assistant' : IDL.Func([AssistantInput], [GenericResult], []),
    'update_codex' : IDL.Func([CodexInput], [GenericResult], []),
    'update_extension' : IDL.Func([ExtensionInput], [GenericResult], []),
  });
};
export const init = ({ IDL }) => {
  const MarketplaceInitArg = IDL.Record({
    'billing_service_principal' : IDL.Opt(IDL.Text),
    'file_registry' : IDL.Opt(IDL.Text),
  });
  return [IDL.Opt(MarketplaceInitArg)];
};
