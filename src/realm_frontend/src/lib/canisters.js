import { building } from '$app/environment';
import { writable, get } from 'svelte/store';
import { authClient, initializeAuthClient, login } from '$lib/auth';
import { getTestModeIIBypass } from '$lib/config.js';
import { isEmbeddedInPortal } from '$lib/portal-bridge.ts';

let createActor, canisterId, HttpAgent;
let importsInitialized = false;

async function initializeImports() {
	if (importsInitialized) return;

	console.log('🏭 Loading IC backend implementations');

	// Canister ID comes from /canister_ids.js (uploaded by the installer at deploy time).
	// Falls back to build-time declarations for local dev.
	const runtimeIds = globalThis.__CANISTER_IDS;
	if (runtimeIds?.realm_backend) {
		canisterId = runtimeIds.realm_backend;
		console.log(`✅ realm_backend from canister_ids.js: ${canisterId}`);
	}

	try {
		const { createActor: ca, canisterId: cid } = await import('$lib/declarations/realm_backend');
		createActor = ca;
		if (!canisterId) {
			canisterId = cid;
			console.log(`✅ realm_backend from declarations: ${canisterId}`);
		}
	} catch (e) {
		console.error('Failed to load backend declarations:', e);
		throw new Error('Could not load backend declarations. Please run: dfx generate');
	}

	const agentModule = await import('@dfinity/agent');
	HttpAgent = agentModule.HttpAgent;

	if (!canisterId) {
		throw new Error('Canister ID not found. Ensure /canister_ids.js is deployed.');
	}

	importsInitialized = true;
}

function dummyActor() {
	return new Proxy(
		{},
		{
			get() {
				throw new Error('Canister invoked while building');
			}
		}
	);
}

const buildingOrTesting = building || process.env.NODE_ENV === 'test';

// Detect if we're running in local development
// Use a more reliable method than process.env which might not work in browser
function isLocalDevelopment() {
	if (typeof window === 'undefined') return false;
	return (
		window.location.hostname.includes('localhost') || window.location.hostname.includes('127.0.0.1')
	);
}

// Create a writable store for the backend actor
export const backendStore = writable(buildingOrTesting ? dummyActor() : null);

// Principal the current backend actor signs as ('' while anonymous).
export const backendPrincipal = writable('');

/** Resolves once the backend actor signs as ``principalText`` (or after ``timeoutMs``). */
export function whenBackendSignsAs(principalText, timeoutMs = 15000) {
	if (!principalText || buildingOrTesting) return Promise.resolve();
	return new Promise((resolve) => {
		let unsubscribe = null;
		let done = false;
		const finish = () => {
			if (done) return;
			done = true;
			clearTimeout(timer);
			if (unsubscribe) unsubscribe();
			resolve();
		};
		const timer = setTimeout(finish, timeoutMs);
		unsubscribe = backendPrincipal.subscribe((p) => {
			if (p === principalText) finish();
		});
		if (done) unsubscribe();
	});
}

// Singleton promise so concurrent callers all wait for the same init.
let _initBackendPromise = null;

// Resolves as soon as the provisional anonymous actor is in the store.
// Stores must await THIS (not backendReady) before calling the backend:
// initializeBackendStore() itself awaits realmInfo.fetch(), so a fetch that
// awaits backendReady deadlocks the whole boot (circular await).
let _resolveActorReady;
export const backendActorReady = buildingOrTesting
	? Promise.resolve()
	: new Promise((resolve) => {
			_resolveActorReady = resolve;
		});

// Initialize the backend store after imports are loaded
function initializeBackendStore() {
	if (buildingOrTesting) return Promise.resolve();
	if (_initBackendPromise) return _initBackendPromise;

	_initBackendPromise = (async () => {
		await initializeImports();

		// Provisional anonymous actor so realmInfo.fetch() can call the backend
		// before auth init (fetch requires backendStore to be set).
		let agent = new HttpAgent({ verifyQuerySignatures: false });
		if (isLocalDevelopment()) {
			await agent.fetchRootKey().catch(() => {});
		}
		let actor = createActor(canisterId, { agent });
		backendStore.set(actor);
		_resolveActorReady();

		// Runtime flags (incl. test_mode_ii_bypass) come from backend status(), not
		// canister_ids.js. Fetch before auth init so test-mode auto-login is not
		// skipped while status is still loading (which leaves an anonymous actor).
		const { realmInfo } = await import('$lib/stores/realmInfo');
		await realmInfo.fetch();

		// Try to use an existing authenticated session so the initial actor
		// is not anonymous on page refresh (avoids race with initBackendWithIdentity).
		try {
			const client = authClient || (await initializeAuthClient());

			// In test mode, auto-login before checking auth to avoid race condition
			// where components mount and make calls before AuthButton triggers login
			if (getTestModeIIBypass() && !isEmbeddedInPortal() && !(await client.isAuthenticated())) {
				await login();
			}

			if (await client.isAuthenticated()) {
				const identity = client.getIdentity();
				agent = new HttpAgent({ identity, verifyQuerySignatures: false });
				if (isLocalDevelopment()) {
					await agent.fetchRootKey().catch(() => {});
				}
				actor = createActor(canisterId, { agent });
				backendStore.set(actor);
				backendPrincipal.set(identity.getPrincipal().toText());
				console.log('Backend store initialized with authenticated identity');
				return;
			}
		} catch (e) {
			console.warn('Could not check auth during init, falling back to anonymous:', e);
		}

		// Keep provisional anonymous actor for login page / unauthenticated users.
	})();

	return _initBackendPromise;
}

// Initialize immediately if not building/testing.
// Export the promise so components can await full init (including auth).
export const backendReady = buildingOrTesting ? Promise.resolve() : initializeBackendStore();

// Create a proxy that always uses the latest actor from the store
export const backend = new Proxy(
	{},
	{
		get: function (target, prop) {
			const currentBackend = get(backendStore);
			if (!currentBackend) {
				return async (...args) => {
					await initializeBackendStore();
					const backend = get(backendStore);
					return backend[prop](...args);
				};
			}
			return currentBackend[prop];
		}
	}
);

// Initialize backend with authenticated identity.
// An explicit identity (e.g. a portal-scoped delegation) takes precedence over
// whatever the shared AuthClient holds — in portal iframes the AuthClient is a
// plain unauthenticated client and must not win over the bridged delegation.
export async function initBackendWithIdentity(explicitIdentity = null) {
	try {
		console.log('Initializing backend with authenticated identity...');

		await initializeImports();

		// Make sure we're using the shared auth client
		const client = authClient || (await initializeAuthClient());

		if (explicitIdentity || (await client.isAuthenticated())) {
			const identity = explicitIdentity || client.getIdentity();
			console.log('Using authenticated identity:', identity.getPrincipal().toText());

			const currentActor = get(backendStore);
			if (currentActor && currentActor._agent && currentActor._agent._identity === identity) {
				console.log('Backend already initialized with current identity');
				backendPrincipal.set(identity.getPrincipal().toText());
				return currentActor;
			}

		// Create an agent with the identity
		const agent = new HttpAgent({ identity, verifyQuerySignatures: false });

		// For local development, we need to fetch the root key
		if (isLocalDevelopment()) {
				console.log('Fetching root key for local development');
				await agent.fetchRootKey().catch((e) => {
					console.warn('Error fetching root key:', e);
					console.log('Continuing anyway as this might be expected in local dev');
				});
			}

			// Create a new actor with the authenticated identity
			const authenticatedActor = createActor(canisterId, {
				agent
			});

			// Update the store with the authenticated actor
			backendStore.set(authenticatedActor);
			backendPrincipal.set(identity.getPrincipal().toText());

			// If a quarter is active, its actor was built with the previous
			// (possibly anonymous) identity — rebuild it so quarter-routed calls
			// carry the fresh identity too.
			if (_currentQuarterId) {
				try {
					const quarterActor = await createQuarterActor(_currentQuarterId, identity);
					quarterBackendStore.set(quarterActor);
					console.log(`🏘️ quarterBackend re-authenticated → quarter ${_currentQuarterId}`);
				} catch (e) {
					console.warn('Failed to re-authenticate quarter actor:', e);
				}
			}

			console.log('Backend initialized with authenticated identity');
			return authenticatedActor;
		} else {
			const recoveredIdentity = await (async () => {
				const { getEstablishedIdentity, resetAuthStoresIfDesynced } = await import('$lib/auth');
				const identity = await getEstablishedIdentity();
				if (identity) return identity;
				await resetAuthStoresIfDesynced();
				return null;
			})();

			if (recoveredIdentity) {
				return initBackendWithIdentity(recoveredIdentity);
			}

			console.log('User not authenticated, using anonymous identity');
			return backend;
		}
	} catch (error) {
		console.error('Error initializing backend with identity:', error);
		return backend;
	}
}

// Quarter-aware actor: creates an actor for a specific canister ID.
// `explicitIdentity` wins over the shared AuthClient for the same reason as in
// initBackendWithIdentity: in portal iframes the bridged delegation may not be
// reflected in the AuthClient yet, and an anonymous quarter actor gets its
// user-scoped calls rejected (AccessDenied) by the quarter.
export async function createQuarterActor(quarterCanisterId, explicitIdentity = null) {
	if (buildingOrTesting) return dummyActor();

	await initializeImports();

	let identity = explicitIdentity;
	if (!identity) {
		try {
			const { isEmbeddedInPortal, getPortalDelegationIdentity } = await import(
				'$lib/portal-bridge.ts'
			);
			if (isEmbeddedInPortal()) identity = getPortalDelegationIdentity();
		} catch {
			// portal bridge unavailable outside an embed
		}
	}
	if (!identity) {
		const client = authClient || (await initializeAuthClient());
		if (await client.isAuthenticated()) {
			identity = client.getIdentity();
		}
	}

	const agent = identity
		? new HttpAgent({ identity, verifyQuerySignatures: false })
		: new HttpAgent({ verifyQuerySignatures: false });

	if (isLocalDevelopment()) {
		await agent.fetchRootKey().catch(() => {});
	}

	return createActor(quarterCanisterId, { agent });
}

/**
 * Rebuild an actor with the join-safe IDL so ``join_realm`` can decode a
 * UserGetRecord that omits ``departments`` (or uses an unexpected type).
 * Same agent / identity / canister as ``strictActor``.
 */
export async function asJoinSafeActor(strictActor) {
	if (!strictActor) return strictActor;
	const [{ Actor }, { joinIdlFactory }] = await Promise.all([
		import('@dfinity/agent'),
		import('$lib/joinIdl.js')
	]);
	const canisterIdForJoin = Actor.canisterIdOf(strictActor);
	const agent = Actor.agentOf(strictActor);
	if (!agent || !canisterIdForJoin) return strictActor;
	return Actor.createActor(joinIdlFactory, {
		agent,
		canisterId: canisterIdForJoin
	});
}

// --- Quarter-aware backend store ---
// Holds the currently-active actor: main realm backend OR a quarter actor.
// Import activeQuarterId from quarters store and subscribe to swap actors.
export const quarterBackendStore = writable(buildingOrTesting ? dummyActor() : null);

let _currentQuarterId = null;

/**
 * Switch the quarterBackend to a specific quarter canister, or back to the
 * main realm backend when quarterId is null.
 */
export async function setActiveQuarter(quarterId) {
	if (quarterId === _currentQuarterId) return;
	_currentQuarterId = quarterId;

	if (!quarterId) {
		// Revert to main realm backend
		const main = get(backendStore);
		quarterBackendStore.set(main || backend);
		console.log('🏛️ quarterBackend → main realm');
		return;
	}

	try {
		const actor = await createQuarterActor(quarterId);
		quarterBackendStore.set(actor);
		console.log(`🏘️ quarterBackend → quarter ${quarterId}`);
	} catch (e) {
		console.error('Failed to create quarter actor, falling back to main:', e);
		quarterBackendStore.set(get(backendStore) || backend);
	}
}

// Proxy that always resolves to the current quarter (or main) backend
export const quarterBackend = new Proxy(
	{},
	{
		get: function (target, prop) {
			const current = get(quarterBackendStore);
			if (!current) {
				// Not initialised yet — delegate to main backend
				return (...args) => backend[prop](...args);
			}
			return current[prop];
		}
	}
);

// Initialise quarterBackendStore to the main backend once it is ready
backendStore.subscribe((actor) => {
	if (actor && !_currentQuarterId) {
		quarterBackendStore.set(actor);
	}
});

// Debug helper: expose backend globally in browser console
if (typeof window !== 'undefined') {
	window.__debug_backend = backend;
	window.__debug_quarterBackend = quarterBackend;
	console.log('🔍 Debug: backend available as window.__debug_backend');
}
