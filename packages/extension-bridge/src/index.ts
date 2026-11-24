export {
	BRIDGE_PROTOCOL_VERSION,
	BRIDGE_SOURCE,
	type BridgeErrorCode,
	type BridgeErrorPayload,
	type HostRealmInfo,
	type HostState,
	type NotifyLevel,
	type ModalAction,
	type BridgeMessage,
	type ExtToHostMessage,
	type HostToExtMessage,
	type TaskResultStatus,
	type TaskResultPayload,
	type VaultSealed,
	type FileSaved,
	isBridgeMessage,
	bridgeProtocolMajor,
	bridgeVersionsCompatible,
} from './protocol.js';

export {
	createExtensionClient,
	type ExtensionClient,
	type ExtensionClientOptions,
	type OpenModalOptions,
	type CallExtensionAsyncOptions,
	type RealmReply,
} from './client.js';

export {
	createBridgeServer,
	type BridgeServer,
	type BridgeServerOptions,
} from './server.js';

export {
	validateExtToHostMessage,
	validateHello,
	validateCallExtension,
	validateCallExtensionAsync,
	validateNavigate,
	validateNotify,
	validateOpenModal,
	validateResize,
	validateGetState,
	validateGoodbye,
	validateVaultSeal,
	validateVaultOpen,
	validateSaveFile,
	SAVE_FILE_TYPES,
	VAULT_RECIPIENTS_MAX,
	type ExtMessageValidationFailure,
	type ExtMessageValidationResult,
} from './validators.js';
