import type { Principal } from '@icp-sdk/core/principal';
import type { ActorMethod } from '@icp-sdk/core/agent';
import type { IDL } from '@icp-sdk/core/candid';

export interface AccountBalanceArgs { 'account' : Uint8Array | number[] }
export type AccountIdentifier = Uint8Array | number[];
export type AddRealmResult = { 'Ok' : string } |
  { 'Err' : string };
export type Address = string;
export interface Archive { 'canister_id' : Principal }
export interface Archives { 'archives' : Array<Archive> }
export interface AssetCanisterService {
  'delete_asset' : ActorMethod<[_DeleteAssetArg], undefined>,
  'list' : ActorMethod<[_ListAssetsArg], string>,
  'store' : ActorMethod<[_AssetStoreArg], undefined>,
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
  'developer_name' : string,
  'pricing_summary' : string,
}
export type AssistantResult = { 'Ok' : AssistantListing } |
  { 'Err' : string };
export type AuthorityError = { 'GenericError' : GenericError } |
  { 'NonExistingTokenId' : null } |
  { 'Unauthorized' : null } |
  { 'InvalidRecipient' : null };
export type AuthorityResult = { 'Ok' : bigint } |
  { 'Err' : AuthorityError };
export type BitcoinAddress = string;
export type BitcoinNetwork = { 'Mainnet' : null } |
  { 'Regtest' : null } |
  { 'Testnet' : null };
export interface Block {
  'transaction' : Transaction,
  'timestamp' : TimeStamp,
  'parent_hash' : [] | [Uint8Array | number[]],
}
export type BlockHash = Uint8Array | number[];
export type BlockIndex = bigint;
export interface BlockRange { 'blocks' : Array<Block> }
export type Callback = ActorMethod<
  [StreamingToken],
  StreamingCallbackHttpResponse
>;
export interface CallbackStrategy {
  'token' : StreamingToken,
  'callback' : [Principal, string],
}
export interface CanisterInfo {
  'canister_id' : string,
  'canister_type' : string,
}
export interface CanisterSettings {
  'freezing_threshold' : [] | [bigint],
  'controllers' : [] | [Array<Principal>],
  'memory_allocation' : [] | [bigint],
  'compute_allocation' : [] | [bigint],
}
export type CanisterStatus = { 'stopped' : null } |
  { 'stopping' : null } |
  { 'running' : null };
export interface CanisterStatusArgs { 'canister_id' : Principal }
export interface CanisterStatusResult {
  'status' : CanisterStatus,
  'memory_size' : bigint,
  'cycles' : bigint,
  'settings' : DefiniteCanisterSettings,
  'module_hash' : [] | [Uint8Array | number[]],
}
export interface CapitalPopulationService {
  'report_quarter_population' : ActorMethod<[bigint], string>,
  'report_quarter_ready' : ActorMethod<[], string>,
}
export interface CasalsProvisionService {
  'create_canister' : ActorMethod<[string], string>,
}
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
  'developer_name' : string,
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
export interface CreateCanisterArgs { 'settings' : [] | [CanisterSettings] }
export interface CreateCanisterResult { 'canister_id' : Principal }
export interface CryptoResponse {
  'data' : CryptoResponseData,
  'success' : boolean,
}
export type CryptoResponseData = { 'scopeList' : ScopeListRecord } |
  { 'envelope' : EnvelopeRecord } |
  { 'error' : string } |
  { 'envelopeList' : EnvelopeListRecord } |
  { 'groupMembers' : GroupMembersRecord } |
  { 'group' : GroupRecord } |
  { 'message' : string } |
  { 'groupList' : GroupListRecord };
export interface DecimalsResult { 'decimals' : number }
export interface DefiniteCanisterSettings {
  'freezing_threshold' : bigint,
  'controllers' : Array<Principal>,
  'memory_allocation' : bigint,
  'compute_allocation' : bigint,
}
export interface DeleteCanisterArgs { 'canister_id' : Principal }
export interface DemoRegistrationService {
  'register_demo_citizens' : ActorMethod<[string], string>,
}
export interface DepositCyclesArgs { 'canister_id' : Principal }
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
export type EcdsaCurve = { 'secp256k1' : null };
export interface EcdsaPublicKeyArgs {
  'key_id' : KeyId,
  'canister_id' : [] | [Principal],
  'derivation_path' : Array<Uint8Array | number[]>,
}
export interface EcdsaPublicKeyResult {
  'public_key' : Uint8Array | number[],
  'chain_code' : Uint8Array | number[],
}
export interface EnvelopeListRecord { 'envelopes' : Array<EnvelopeRecord> }
export interface EnvelopeRecord {
  'scope' : string,
  'principal_id' : string,
  'wrapped_dek' : string,
}
export interface ExtensionCallArgs {
  'args' : string,
  'function_name' : string,
  'extension_name' : string,
}
export interface ExtensionCallResponse {
  'response' : string,
  'success' : boolean,
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
  'developer_name' : string,
}
export type ExtensionResult = { 'Ok' : ExtensionListing } |
  { 'Err' : string };
export interface ExtensionsListRecord { 'extensions' : Array<string> }
export interface FederationService {
  'federation_message' : ActorMethod<[string], string>,
}
export interface FileRegistryService {
  'set_namespace_approval' : ActorMethod<[string], string>,
}
export interface ForceTransferArg {
  'to' : NftAccount,
  'token_id' : bigint,
  'memo' : [] | [string],
}
export interface ForcedTransferArgs {
  'to' : TokenAccount,
  'from' : TokenAccount,
  'memo' : [] | [string],
  'amount' : bigint,
}
export interface FreezeAccountArgs {
  'account' : TokenAccount,
  'reason' : [] | [string],
}
export interface FreezeArg { 'token_id' : bigint, 'reason' : [] | [string] }
export interface GenericError { 'message' : string, 'error_code' : bigint }
export type GenericResult = { 'Ok' : string } |
  { 'Err' : string };
export interface GetBalanceArgs {
  'network' : BitcoinNetwork,
  'address' : string,
  'min_confirmations' : [] | [number],
}
export interface GetBlocksArgs { 'start' : bigint, 'length' : bigint }
export type GetCreditsResult = { 'Ok' : UserCreditsRecord } |
  { 'Err' : string };
export interface GetCurrentFeePercentilesArgs { 'network' : BitcoinNetwork }
export interface GetUtxosArgs {
  'network' : BitcoinNetwork,
  'filter' : [] | [UtxosFilter],
  'address' : string,
}
export interface GetUtxosResult {
  'next_page' : [] | [Uint8Array | number[]],
  'tip_height' : number,
  'tip_block_hash' : Uint8Array | number[],
  'utxos' : Array<Utxo>,
}
export interface GroupListRecord { 'groups' : Array<GroupRecord> }
export interface GroupMemberRecord { 'role' : string, 'principal_id' : string }
export interface GroupMembersRecord { 'members' : Array<GroupMemberRecord> }
export interface GroupRecord { 'name' : string, 'description' : string }
export type GuardResult = { 'Ok' : null } |
  { 'Err' : string };
export type Header = [string, string];
export interface HttpHeader { 'value' : string, 'name' : string }
export type HttpMethod = { 'get' : null } |
  { 'head' : null } |
  { 'post' : null };
export interface HttpRequest {
  'url' : string,
  'method' : string,
  'body' : Uint8Array | number[],
  'headers' : Array<Header>,
}
export interface HttpRequestArgs {
  'url' : string,
  'method' : HttpMethod,
  'max_response_bytes' : [] | [bigint],
  'body' : [] | [Uint8Array | number[]],
  'transform' : [] | [HttpTransform],
  'headers' : Array<HttpHeader>,
}
export interface HttpResponse {
  'status' : bigint,
  'body' : Uint8Array | number[],
  'headers' : Array<HttpHeader>,
}
export interface HttpResponseIncoming {
  'body' : Uint8Array | number[],
  'headers' : Array<Header>,
  'upgrade' : [] | [boolean],
  'streaming_strategy' : [] | [string],
  'status_code' : number,
}
export interface HttpTransform {
  'function' : [Principal, string],
  'context' : Uint8Array | number[],
}
export interface HttpTransformArgs {
  'context' : Uint8Array | number[],
  'response' : HttpResponse,
}
export type HttpTransformFunc = ActorMethod<[HttpTransformArgs], HttpResponse>;
export interface Icrc1MetadataService {
  'icrc1_decimals' : ActorMethod<[], number>,
  'icrc1_name' : ActorMethod<[], string>,
  'icrc1_symbol' : ActorMethod<[], string>,
}
export type InsertError = {
    'ValueTooLarge' : { 'max' : number, 'given' : number }
  } |
  { 'KeyTooLarge' : { 'max' : number, 'given' : number } };
export interface InstallCodeArgs {
  'arg' : Uint8Array | number[],
  'wasm_module' : Uint8Array | number[],
  'mode' : InstallCodeMode,
  'canister_id' : Principal,
}
export type InstallCodeMode = { 'reinstall' : null } |
  { 'upgrade' : null } |
  { 'install' : null };
export interface InstallerProvisionService {
  'provision_quarter' : ActorMethod<[string], string>,
}
export interface KeyId { 'name' : string, 'curve' : EcdsaCurve }
export interface KeyTooLarge { 'max' : number, 'given' : number }
export interface LLMChatResponse { 'response' : string }
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
export type Memo = bigint;
export type MetadataValue = { 'Int' : bigint } |
  { 'Nat' : bigint } |
  { 'Blob' : Uint8Array | number[] } |
  { 'Text' : string };
export type MillisatoshiPerByte = bigint;
export interface MintArg {
  'token_id' : [] | [bigint],
  'owner' : NftAccount,
  'metadata' : [] | [Array<[string, MetadataValue]>],
}
export type MintError = { 'GenericError' : GenericError } |
  { 'SupplyCapReached' : null } |
  { 'Unauthorized' : null } |
  { 'TokenIdAlreadyExists' : null };
export type MintResult = { 'Ok' : bigint } |
  { 'Err' : MintError };
export interface NFTService {
  'force_transfer' : ActorMethod<[ForceTransferArg], AuthorityResult>,
  'freeze_token' : ActorMethod<[FreezeArg], AuthorityResult>,
  'mint' : ActorMethod<[MintArg], MintResult>,
  'unfreeze_token' : ActorMethod<[bigint], AuthorityResult>,
}
export interface NameResult { 'name' : string }
export interface NftAccount {
  'owner' : Principal,
  'subaccount' : [] | [Uint8Array | number[]],
}
export type NotifyResult = { 'Ok' : null } |
  {
    'Err' : { 'NoError' : null } |
      { 'CanisterError' : null } |
      { 'SysTransient' : null } |
      { 'DestinationInvalid' : null } |
      { 'SysFatal' : null } |
      { 'CanisterReject' : null }
  };
export interface ObjectsListRecord { 'objects' : Array<string> }
export interface ObjectsListRecordPaginated {
  'pagination' : PaginationInfo,
  'objects' : Array<string>,
}
export type Operation = { 'Burn' : Operation_Burn } |
  { 'Mint' : Operation_Mint } |
  { 'Transfer' : Operation_Transfer };
export interface Operation_Burn {
  'from' : Uint8Array | number[],
  'amount' : Tokens,
}
export interface Operation_Mint {
  'to' : Uint8Array | number[],
  'amount' : Tokens,
}
export interface Operation_Transfer {
  'to' : Uint8Array | number[],
  'fee' : Tokens,
  'from' : Uint8Array | number[],
  'amount' : Tokens,
}
export interface Outpoint { 'txid' : Uint8Array | number[], 'vout' : number }
export type Page = Uint8Array | number[];
export interface PaginationInfo {
  'page_size' : bigint,
  'total_pages' : bigint,
  'total_items_count' : bigint,
  'page_num' : bigint,
}
export interface PendingAudit {
  'updated_at' : number,
  'name' : string,
  'item_kind' : string,
  'version' : string,
  'item_id' : string,
  'developer' : string,
}
export interface PositionHoldersService {
  'list_position_holders' : ActorMethod<[], string>,
}
export interface ProvisionalCreateCanisterWithCyclesArgs {
  'settings' : [] | [CanisterSettings],
  'amount' : [] | [bigint],
}
export interface ProvisionalCreateCanisterWithCyclesResult {
  'canister_id' : Principal,
}
export interface ProvisionalTopUpCanisterArgs {
  'canister_id' : Principal,
  'amount' : bigint,
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
export interface QuarterBootstrapService {
  'bootstrap_as_quarter' : ActorMethod<[string], string>,
}
export interface QuarterCodexSyncService {
  'request_codex_sync' : ActorMethod<[string], string>,
}
export interface QuarterDirectoryService {
  'get_quarter_directory' : ActorMethod<[], string>,
}
export interface QuarterInfoRecord {
  'status' : string,
  'name' : string,
  'canister_id' : string,
  'is_capital' : boolean,
  'index' : bigint,
  'population' : bigint,
}
export type QueryArchiveError = {
    'BadFirstBlockIndex' : QueryArchiveError_BadFirstBlockIndex
  } |
  { 'Other' : QueryArchiveError_Other };
export interface QueryArchiveError_BadFirstBlockIndex {
  'requested_index' : bigint,
  'first_valid_index' : bigint,
}
export interface QueryArchiveError_Other {
  'error_message' : string,
  'error_code' : bigint,
}
export type QueryArchiveFn = ActorMethod<[GetBlocksArgs], QueryArchiveResult>;
export type QueryArchiveResult = { 'Ok' : BlockRange } |
  { 'Err' : QueryArchiveError };
export interface QueryBlocksResponse {
  'certificate' : [] | [Uint8Array | number[]],
  'blocks' : Array<Block>,
  'chain_length' : bigint,
  'first_block_index' : bigint,
  'archived_blocks' : Array<QueryBlocksResponse_archived_blocks>,
}
export interface QueryBlocksResponse_archived_blocks {
  'callback' : [Principal, string],
  'start' : bigint,
  'length' : bigint,
}
export interface RealmData {
  'json' : string,
  'timestamp' : bigint,
  'principal_id' : string,
}
export interface RealmMessagingService {
  'receive_realm_message' : ActorMethod<
    [string, string, string, string],
    string
  >,
}
export interface RealmRegistryService {
  'register_realm' : ActorMethod<
    [string, string, string, string, string],
    AddRealmResult
  >,
}
export interface RealmRegistryUpgradeService {
  'get_credits' : ActorMethod<[string], GetCreditsResult>,
  'get_latest_version' : ActorMethod<[], UpgradeResult>,
  'request_upgrade' : ActorMethod<[string], string>,
}
export interface RealmResponse {
  'data' : RealmResponseData,
  'success' : boolean,
}
export type RealmResponseData = { 'status' : StatusRecord } |
  { 'objectsListPaginated' : ObjectsListRecordPaginated } |
  { 'objectsList' : ObjectsListRecord } |
  { 'extensionsList' : ExtensionsListRecord } |
  { 'userGet' : UserGetRecord } |
  { 'error' : string } |
  { 'message' : string };
export type RecordKey = [string, string];
export type RejectionCode = { 'NoError' : null } |
  { 'CanisterError' : null } |
  { 'SysTransient' : null } |
  { 'DestinationInvalid' : null } |
  { 'SysFatal' : null } |
  { 'CanisterReject' : null };
export type Satoshi = bigint;
export interface ScopeListRecord { 'scopes' : Array<string> }
export interface SendTransactionArgs {
  'transaction' : Uint8Array | number[],
  'network' : BitcoinNetwork,
}
export type SendTransactionError = { 'QueueFull' : null } |
  { 'MalformedTransaction' : null };
export interface SignWithEcdsaArgs {
  'key_id' : KeyId,
  'derivation_path' : Array<Uint8Array | number[]>,
  'message_hash' : Uint8Array | number[],
}
export interface SignWithEcdsaResult { 'signature' : Uint8Array | number[] }
export type Stable64GrowResult = { 'Ok' : bigint } |
  { 'Err' : { 'OutOfBounds' : null } | { 'OutOfMemory' : null } };
export type StableGrowResult = { 'Ok' : number } |
  { 'Err' : { 'OutOfBounds' : null } | { 'OutOfMemory' : null } };
export type StableMemoryError = { 'OutOfBounds' : null } |
  { 'OutOfMemory' : null };
export interface StartCanisterArgs { 'canister_id' : Principal }
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
export interface StopCanisterArgs { 'canister_id' : Principal }
export interface StreamingCallbackHttpResponse {
  'token' : [] | [StreamingToken],
  'body' : Uint8Array | number[],
}
export type StreamingStrategy = { 'Callback' : CallbackStrategy };
export interface StreamingToken { 'key' : string }
export type SubAccount = Uint8Array | number[];
export interface SymbolResult { 'symbol' : string }
export interface TestBenchResponse { 'data' : string }
export interface TimeStamp { 'timestamp_nanos' : bigint }
export interface TokenAccount {
  'owner' : Principal,
  'subaccount' : [] | [Uint8Array | number[]],
}
export type TokenAuthorityError = { 'GenericError' : string } |
  { 'InsufficientBalance' : null } |
  { 'Unauthorized' : null } |
  { 'InvalidRecipient' : null };
export type TokenAuthorityResult = { 'Ok' : bigint } |
  { 'Err' : TokenAuthorityError };
export interface TokenAuthorityService {
  'forced_transfer' : ActorMethod<[ForcedTransferArgs], TokenAuthorityResult>,
  'freeze_account' : ActorMethod<[FreezeAccountArgs], TokenAuthorityResult>,
  'unfreeze_account' : ActorMethod<[TokenAccount], TokenAuthorityResult>,
}
export interface Tokens { 'e8s' : bigint }
export interface Transaction {
  'memo' : bigint,
  'operation' : [] | [Operation],
  'created_at_time' : TimeStamp,
}
export interface TransferArgs {
  'to' : Uint8Array | number[],
  'fee' : Tokens,
  'memo' : bigint,
  'from_subaccount' : [] | [Uint8Array | number[]],
  'created_at_time' : [] | [TimeStamp],
  'amount' : Tokens,
}
export type TransferError = { 'TxTooOld' : TransferError_TxTooOld } |
  { 'BadFee' : TransferError_BadFee } |
  { 'TxDuplicate' : TransferError_TxDuplicate } |
  { 'TxCreatedInFuture' : null } |
  { 'InsufficientFunds' : TransferError_InsufficientFunds };
export interface TransferError_BadFee { 'expected_fee' : Tokens }
export interface TransferError_InsufficientFunds { 'balance' : Tokens }
export interface TransferError_TxDuplicate { 'duplicate_of' : bigint }
export interface TransferError_TxTooOld { 'allowed_window_nanos' : bigint }
export interface TransferFee { 'transfer_fee' : Tokens }
export type TransferFeeArg = {};
export type TransferResult = { 'Ok' : bigint } |
  { 'Err' : TransferError };
export interface UninstallCodeArgs { 'canister_id' : Principal }
export interface UpdateSettingsArgs {
  'canister_id' : Principal,
  'settings' : CanisterSettings,
}
export type UpgradeResult = { 'Ok' : string } |
  { 'Err' : string };
export interface UserCreditsRecord {
  'total_spent' : bigint,
  'balance' : bigint,
  'principal_id' : string,
  'total_purchased' : bigint,
}
export interface UserGetRecord {
  'assigned_quarter' : string,
  'principal' : Principal,
  'departments' : Array<string>,
  'private_data' : string,
  'nickname' : string,
  'profiles' : Array<string>,
  'avatar' : string,
}
export interface Utxo {
  'height' : number,
  'value' : bigint,
  'outpoint' : Outpoint,
}
export type UtxosFilter = { 'Page' : Uint8Array | number[] } |
  { 'MinConfirmations' : number };
export interface ValueTooLarge { 'max' : number, 'given' : number }
export interface _AssetStoreArg {
  'key' : string,
  'content' : Uint8Array | number[],
  'sha256' : [] | [Uint8Array | number[]],
  'content_type' : string,
  'content_encoding' : string,
}
export interface _DeleteAssetArg { 'key' : string }
export interface _GetAccountTransactionsArgs {
  'max_results' : bigint,
  'start' : [] | [bigint],
  'account' : _IcrcAccount,
}
export interface _GetAccountTransactionsOk {
  'balance' : bigint,
  'transactions' : Array<_IcrcTransactionWithId>,
}
export type _GetAccountTransactionsResult = {
    'Ok' : _GetAccountTransactionsOk
  } |
  { 'Err' : string };
export interface _ICRC1IndexerService {
  'get_account_transactions' : ActorMethod<
    [_GetAccountTransactionsArgs],
    _GetAccountTransactionsResult
  >,
}
export interface _IcrcAccount {
  'owner' : Principal,
  'subaccount' : [] | [Uint8Array | number[]],
}
export interface _IcrcTransaction {
  'kind' : string,
  'transfer' : [] | [_IcrcTransfer],
}
export interface _IcrcTransactionWithId {
  'id' : bigint,
  'transaction' : _IcrcTransaction,
}
export interface _IcrcTransfer { 'to' : _IcrcAccount, 'amount' : bigint }
export interface _ListAssetsArg {
  'start' : [] | [bigint],
  'length' : [] | [bigint],
}
export interface _SERVICE {
  '__get_candid_interface_tmp_hack' : ActorMethod<[], string>,
  'add_reviewer' : ActorMethod<[string], GenericResult>,
  'admin_approve_namespace' : ActorMethod<[string, string], string>,
  'buy_assistant' : ActorMethod<[string], GenericResult>,
  'buy_codex' : ActorMethod<[string], GenericResult>,
  'buy_extension' : ActorMethod<[string], GenericResult>,
  'check_license' : ActorMethod<[string], LicenseResult>,
  'create_assistant' : ActorMethod<[AssistantInput], GenericResult>,
  'create_codex' : ActorMethod<[CodexInput], GenericResult>,
  'create_extension' : ActorMethod<[ExtensionInput], GenericResult>,
  'delist_assistant' : ActorMethod<[string], GenericResult>,
  'transfer_listing' : ActorMethod<[string, string, string], GenericResult>,
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
  'grant_manual_license' : ActorMethod<[string, bigint, string], GenericResult>,
  'greet' : ActorMethod<[string], string>,
  'has_liked' : ActorMethod<[string, string, string], boolean>,
  'has_purchased_assistant' : ActorMethod<[string, string], boolean>,
  'has_purchased_codex' : ActorMethod<[string, string], boolean>,
  'has_purchased_extension' : ActorMethod<[string, string], boolean>,
  'http_request' : ActorMethod<[HttpRequest], HttpResponseIncoming>,
  'http_request_update' : ActorMethod<[HttpRequest], HttpResponseIncoming>,
  'like_item' : ActorMethod<[string, string], GenericResult>,
  'list_marketplace_assistants' : ActorMethod<
    [bigint, bigint, boolean],
    AssistantListResult
  >,
  'list_marketplace_codices' : ActorMethod<
    [bigint, bigint, boolean],
    CodexListResult
  >,
  'list_marketplace_extensions' : ActorMethod<
    [bigint, bigint, boolean],
    ExtensionListResult
  >,
  'list_pending_audits' : ActorMethod<[], Array<PendingAudit>>,
  'list_reviewers' : ActorMethod<[], Array<string>>,
  'my_likes' : ActorMethod<[], Array<LikeRecord>>,
  'record_license_payment' : ActorMethod<[LicensePaymentInput], GenericResult>,
  'recount_listing_likes' : ActorMethod<[], GenericResult>,
  'remove_reviewer' : ActorMethod<[string], GenericResult>,
  'request_audit' : ActorMethod<[string, string], GenericResult>,
  'review_listing' : ActorMethod<[string, string, boolean, string], string>,
  'revoke_license' : ActorMethod<[string], GenericResult>,
  'search_assistants' : ActorMethod<[string, boolean], Array<AssistantListing>>,
  'search_codices' : ActorMethod<[string, boolean], Array<CodexListing>>,
  'search_extensions' : ActorMethod<[string, boolean], Array<ExtensionListing>>,
  'set_billing_service_principal' : ActorMethod<[string], GenericResult>,
  'set_casals_frontend_canister_id' : ActorMethod<[string], GenericResult>,
  'set_file_registry_canister_id' : ActorMethod<[string], GenericResult>,
  'set_license_pricing' : ActorMethod<[bigint, bigint], GenericResult>,
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
  'unlike_item' : ActorMethod<[string, string], GenericResult>,
  'update_assistant' : ActorMethod<[AssistantInput], GenericResult>,
  'update_codex' : ActorMethod<[CodexInput], GenericResult>,
  'update_extension' : ActorMethod<[ExtensionInput], GenericResult>,
}
export declare const idlFactory: IDL.InterfaceFactory;
export declare const init: (args: { IDL: typeof IDL }) => IDL.Type[];
