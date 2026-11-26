import type { Principal } from '@icp-sdk/core/principal';
import type { ActorMethod } from '@icp-sdk/core/agent';
import type { IDL } from '@icp-sdk/core/candid';

export interface AccountBalanceArgs { 'account' : Uint8Array | number[] }
export type AccountIdentifier = Uint8Array | number[];
export type AddCreditsResult = { 'Ok' : UserCreditsRecord } |
  { 'Err' : string };
export type AddRealmResult = { 'Ok' : string } |
  { 'Err' : string };
export type Address = string;
export interface Archive { 'canister_id' : Principal }
export interface Archives { 'archives' : Array<Archive> }
export interface BillingStatusRecord {
  'total_spent' : bigint,
  'total_balance' : bigint,
  'total_purchased' : bigint,
  'users_count' : bigint,
}
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
export interface CasalsConfigView {
  'provision_via_casals' : boolean,
  'casals_section' : string,
  'registry_principal' : string,
  'casals_canister_id' : string,
}
export interface CasalsService {
  'create_stand' : ActorMethod<[string], string>,
  'destroy_stand' : ActorMethod<[string], string>,
  'get_cycles_cached' : ActorMethod<[], string>,
  'get_tree' : ActorMethod<[], string>,
}
export interface CreateCanisterArgs { 'settings' : [] | [CanisterSettings] }
export interface CreateCanisterResult { 'canister_id' : Principal }
export interface CreditTransactionRecord {
  'id' : string,
  'transaction_type' : string,
  'description' : string,
  'timestamp' : number,
  'stripe_session_id' : string,
  'principal_id' : string,
  'amount' : bigint,
}
export interface DecimalsResult { 'decimals' : number }
export type DeductCreditsResult = { 'Ok' : UserCreditsRecord } |
  { 'Err' : string };
export interface DefiniteCanisterSettings {
  'freezing_threshold' : bigint,
  'controllers' : Array<Principal>,
  'memory_allocation' : bigint,
  'compute_allocation' : bigint,
}
export interface DeleteCanisterArgs { 'canister_id' : Principal }
export interface DeployStepView {
  'idx' : number,
  'status' : string,
  'kind' : string,
  'label' : string,
  'error' : string,
}
export interface DeployTaskView {
  'status' : string,
  'task_id' : string,
  'completed_count' : number,
  'steps' : Array<DeployStepView>,
  'total_count' : number,
}
export interface DeploymentJobView {
  'status' : string,
  'expected_wasm_hash' : string,
  'registry_canister_id' : string,
  'backend_canister_id' : string,
  'ext_deploy_task_id' : string,
  'assets_verified' : number,
  'expected_step_count' : number,
  'network' : string,
  'frontend_wasm_verified' : number,
  'created_at' : bigint,
  'error' : string,
  'wasm_verified' : number,
  'job_id' : string,
  'realm_name' : string,
  'actual_wasm_hash' : string,
  'caller_principal' : string,
  'completed_at' : bigint,
  'frontend_canister_id' : string,
}
export interface DepositCyclesArgs { 'canister_id' : Principal }
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
export interface EnqueueOk {
  'status' : string,
  'network' : string,
  'job_id' : string,
  'realm_name' : string,
}
export interface FileRegistryService {
  'get_extension_manifest' : ActorMethod<[string], string>,
}
export type GenericResult = { 'Ok' : string } |
  { 'Err' : string };
export interface GetBalanceArgs {
  'network' : BitcoinNetwork,
  'address' : string,
  'min_confirmations' : [] | [number],
}
export type GetBillingStatusResult = { 'Ok' : BillingStatusRecord } |
  { 'Err' : string };
export interface GetBlocksArgs { 'start' : bigint, 'length' : bigint }
export type GetCreditsResult = { 'Ok' : UserCreditsRecord } |
  { 'Err' : string };
export interface GetCurrentFeePercentilesArgs { 'network' : BitcoinNetwork }
export type GetRealmResult = { 'Ok' : RealmRecord } |
  { 'Err' : string };
export type GetStatusResult = { 'Ok' : StatusRecord } |
  { 'Err' : string };
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
export type GuardResult = { 'Ok' : null } |
  { 'Err' : string };
export type Header = [string, string];
export interface HealthView { 'ok' : boolean, 'canister' : string }
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
export interface InstallerError { 'message' : string, 'traceback' : string }
export interface JobStatusAck {
  'status' : string,
  'prev_status' : string,
  'noop' : boolean,
  'job_id' : string,
}
export interface JobsListOk {
  'jobs' : Array<DeploymentJobView>,
  'count' : number,
}
export interface KeyId { 'name' : string, 'curve' : EcdsaCurve }
export interface KeyTooLarge { 'max' : number, 'given' : number }
export type Memo = bigint;
export type MillisatoshiPerByte = bigint;
export interface NameResult { 'name' : string }
export type NotifyResult = { 'Ok' : null } |
  {
    'Err' : { 'NoError' : null } |
      { 'CanisterError' : null } |
      { 'SysTransient' : null } |
      { 'DestinationInvalid' : null } |
      { 'SysFatal' : null } |
      { 'CanisterReject' : null }
  };
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
export interface PendingJobEntry {
  'job' : DeploymentJobView,
  'manifest' : string,
}
export interface PendingJobsOk {
  'jobs' : Array<PendingJobEntry>,
  'count' : number,
}
export interface ProvisionOk {
  'status' : string,
  'backend_canister_id' : string,
  'stand' : string,
  'job_id' : string,
  'frontend_canister_id' : string,
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
export interface PublicLogEntry {
  'id' : bigint,
  'level' : string,
  'logger_name' : string,
  'message' : string,
  'timestamp' : bigint,
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
export interface REnqueueOk {
  'status' : string,
  'network' : string,
  'job_id' : string,
  'realm_name' : string,
}
export interface RInstallerError { 'message' : string, 'traceback' : string }
export interface RProvisionOk {
  'status' : string,
  'backend_canister_id' : string,
  'stand' : string,
  'job_id' : string,
  'frontend_canister_id' : string,
}
export type RResultEnqueue = { 'Ok' : REnqueueOk } |
  { 'Err' : RInstallerError };
export type RResultProvision = { 'Ok' : RProvisionOk } |
  { 'Err' : RInstallerError };
export interface RealmInstallerService {
  'cancel_deployment' : ActorMethod<[string], string>,
  'enqueue_deployment' : ActorMethod<[string], RResultEnqueue>,
  'provision_via_casals' : ActorMethod<[string], RResultProvision>,
}
export interface RealmRecord {
  'id' : string,
  'url' : string,
  'logo' : string,
  'name' : string,
  'created_at' : number,
  'listing_status' : string,
  'backend_url' : string,
  'frontend_canister_id' : string,
  'users_count' : bigint,
}
export interface RealmRegistryService {
  'deployment_failed' : ActorMethod<[string, string, string], string>,
  'deployment_succeeded' : ActorMethod<[string, string], string>,
  'register_realm' : ActorMethod<
    [string, string, string, string, string],
    string
  >,
  'remove_realm' : ActorMethod<[string], string>,
}
export interface RealmTargetService {
  'install_codex_from_registry' : ActorMethod<[string], string>,
  'install_extension_from_registry' : ActorMethod<[string], string>,
  'resync_extension_frontends' : ActorMethod<[string], string>,
  'run_codex_init' : ActorMethod<[string], string>,
}
export type RejectionCode = { 'NoError' : null } |
  { 'CanisterError' : null } |
  { 'SysTransient' : null } |
  { 'DestinationInvalid' : null } |
  { 'SysFatal' : null } |
  { 'CanisterReject' : null };
export interface ReportFrontendOk {
  'status' : string,
  'assets_verified' : number,
  'failed_verification' : boolean,
  'frontend_wasm_verified' : boolean,
  'job_id' : string,
  'actual_assets_hash' : string,
  'actual_frontend_wasm_hash' : string,
}
export interface ReportReadyOk {
  'status' : string,
  'expected_wasm_hash' : string,
  'failed_verification' : boolean,
  'extensions_started' : boolean,
  'wasm_verified' : boolean,
  'job_id' : string,
  'actual_wasm_hash' : string,
}
export type ResultCasalsConfig = { 'Ok' : CasalsConfigView } |
  { 'Err' : InstallerError };
export type ResultDeployTaskStatus = { 'Ok' : DeployTaskView } |
  { 'Err' : InstallerError };
export type ResultEnqueue = { 'Ok' : EnqueueOk } |
  { 'Err' : InstallerError };
export type ResultJobCancel = { 'Ok' : JobStatusAck } |
  { 'Err' : InstallerError };
export type ResultJobIdStatus = { 'Ok' : DeploymentJobView } |
  { 'Err' : InstallerError };
export type ResultJobManifest = { 'Ok' : string } |
  { 'Err' : InstallerError };
export type ResultJobsList = { 'Ok' : JobsListOk } |
  { 'Err' : InstallerError };
export type ResultPendingJobs = { 'Ok' : PendingJobsOk } |
  { 'Err' : InstallerError };
export type ResultProvision = { 'Ok' : ProvisionOk } |
  { 'Err' : InstallerError };
export type ResultReportFailure = { 'Ok' : JobStatusAck } |
  { 'Err' : InstallerError };
export type ResultReportFrontend = { 'Ok' : ReportFrontendOk } |
  { 'Err' : InstallerError };
export type ResultReportReady = { 'Ok' : ReportReadyOk } |
  { 'Err' : InstallerError };
export type ResultTakeSnapshot = { 'Ok' : TakeSnapshotOk } |
  { 'Err' : InstallerError };
export type Satoshi = bigint;
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
  'status' : string,
  'version' : string,
  'commit' : string,
  'commit_datetime' : string,
}
export interface StopCanisterArgs { 'canister_id' : Principal }
export type SubAccount = Uint8Array | number[];
export interface SymbolResult { 'symbol' : string }
export interface TakeSnapshotOk {
  'skipped' : boolean,
  'job_id' : string,
  'snapshot_id' : string,
}
export interface TimeStamp { 'timestamp_nanos' : bigint }
export interface Tokens { 'e8s' : bigint }
export interface Transaction {
  'memo' : bigint,
  'operation' : [] | [Operation],
  'created_at_time' : TimeStamp,
}
export type TransactionHistoryResult = {
    'Ok' : Array<CreditTransactionRecord>
  } |
  { 'Err' : string };
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
export interface Utxo {
  'height' : number,
  'value' : bigint,
  'outpoint' : Outpoint,
}
export type UtxosFilter = { 'Page' : Uint8Array | number[] } |
  { 'MinConfirmations' : number };
export interface ValueTooLarge { 'max' : number, 'given' : number }
export interface VersionInfoRecord {
  'backend_wasm_hash' : string,
  'backend_wasm_url' : string,
  'published_at' : number,
  'frontend_tar_url' : string,
  'version' : string,
  'frontend_tar_hash' : string,
}
export interface _SERVICE {
  '__browse__' : ActorMethod<[string], string>,
  '__get_candid_interface_tmp_hack' : ActorMethod<[], string>,
  '__shell__' : ActorMethod<[string], string>,
  'backfill_job_refs_batch' : ActorMethod<[], string>,
  'cancel_deployment' : ActorMethod<[string], ResultJobCancel>,
  'configure' : ActorMethod<[string], string>,
  'delete_deployment_job' : ActorMethod<[string], ResultJobCancel>,
  'destroy_realm_job' : ActorMethod<[string], ResultJobCancel>,
  'enqueue_deployment' : ActorMethod<[string], ResultEnqueue>,
  'finalize_deployment' : ActorMethod<[string], ResultJobCancel>,
  'get_canister_logs' : ActorMethod<
    [[] | [bigint], [] | [bigint], [] | [string], [] | [string]],
    Array<PublicLogEntry>
  >,
  'get_casals_config' : ActorMethod<[], ResultCasalsConfig>,
  'get_deploy_task_status' : ActorMethod<[string], ResultDeployTaskStatus>,
  'get_deployment_job_status' : ActorMethod<[string], ResultJobIdStatus>,
  'get_deployment_manifest' : ActorMethod<[string], ResultJobManifest>,
  'get_installer_config' : ActorMethod<[], string>,
  'get_pending_deployments' : ActorMethod<[], ResultPendingJobs>,
  'health' : ActorMethod<[], HealthView>,
  'http_request' : ActorMethod<[HttpRequest], HttpResponseIncoming>,
  'http_request_update' : ActorMethod<[HttpRequest], HttpResponseIncoming>,
  'list_deployment_jobs' : ActorMethod<
    [[] | [number], [] | [number]],
    ResultJobsList
  >,
  'provision_via_casals' : ActorMethod<[string], ResultProvision>,
  'report_canister_ready' : ActorMethod<[string], ResultReportReady>,
  'report_deployment_failure' : ActorMethod<[string], ResultReportFailure>,
  'report_frontend_verified' : ActorMethod<[string], ResultReportFrontend>,
  'retry_deployment' : ActorMethod<[string], ResultProvision>,
  'set_casals_config' : ActorMethod<[string], ResultCasalsConfig>,
  'status' : ActorMethod<[], GetStatusResult>,
  'take_pre_upgrade_snapshot' : ActorMethod<[string], ResultTakeSnapshot>,
}
export declare const idlFactory: IDL.InterfaceFactory;
export declare const init: (args: { IDL: typeof IDL }) => IDL.Type[];
