/**
 * Auth channel precedence: portal embed wins over test-mode II bypass.
 * Standalone test realms keep the deterministic mock identities.
 */
export type AuthChannel = 'portal' | 'test' | 'ii';

export function resolveAuthChannel(
	embeddedInPortal: boolean,
	testModeIIBypass: boolean
): AuthChannel {
	if (embeddedInPortal) return 'portal';
	if (__REALMS_TEST_BUILD__ && testModeIIBypass) return 'test';
	return 'ii';
}

/** Established session pin overrides ambient embed/bypass precedence. */
export function resolveEffectiveAuthChannel(
	embeddedInPortal: boolean,
	testModeIIBypass: boolean,
	pinnedChannel: AuthChannel | null = null
): AuthChannel {
	if (pinnedChannel) return pinnedChannel;
	return resolveAuthChannel(embeddedInPortal, testModeIIBypass);
}

/** True when portal delegation restore/login should run (even if II bypass is on). */
export function shouldAttemptPortalAuth(embeddedInPortal: boolean): boolean {
	return embeddedInPortal;
}

/** True when the test-mode mock should run (standalone test realms only). */
export function shouldUseTestModeAuth(
	embeddedInPortal: boolean,
	testModeIIBypass: boolean
): boolean {
	return __REALMS_TEST_BUILD__ && testModeIIBypass && !embeddedInPortal;
}

/** Join page may opt into test identities even inside the portal iframe. */
export function shouldPreferTestModeLogin(
	preferTestMode: boolean,
	_testModeIIBypass?: boolean
): boolean {
	if (!__REALMS_TEST_BUILD__) return false;
	// The picker is already gated on the live bypass flag. Requiring a second
	// canister_ids hint dropped Continue clicks in the portal iframe.
	return !!preferTestMode;
}

/**
 * True when login() must mint a deterministic test identity instead of
 * waiting on Internet Identity or a portal delegation.
 *
 * An explicit picker index (including 0) always wins — that is the Continue
 * as Identity N path. `preferTestMode` is the join page opt-in for portal
 * embeds. Ambient test auth still applies only to standalone bypass realms.
 */
export function shouldLoginWithTestIdentity(options: {
	identityIndex?: number | null;
	preferTestMode?: boolean;
	testModeIIBypass?: boolean;
	embeddedInPortal?: boolean;
} = {}): boolean {
	if (!__REALMS_TEST_BUILD__) return false;
	const { identityIndex = null, preferTestMode = false, testModeIIBypass = false, embeddedInPortal = false } =
		options;
	if (identityIndex != null && Number.isFinite(identityIndex)) return true;
	if (preferTestMode) return true;
	return shouldUseTestModeAuth(!!embeddedInPortal, !!testModeIIBypass);
}

/**
 * Which test identity a sign-in uses: the one asked for (the picker, or `?ti=` in the address), else the one this tab
 * is already signed in as, else the first. A page load signs in by itself before the session is restored, so without
 * the tab's own identity a reload turned every test member into Identity 1. `random` asks for a fresh identity.
 */
export function resolveTestIdentityIndex(
	requested: number | null | undefined,
	persisted: number | null | undefined,
	random = false
): number | null {
	const index = (value: number | null | undefined) =>
		value != null && Number.isFinite(Number(value)) ? Number(value) : null;
	if (index(requested) != null) return index(requested);
	if (random) return null;
	return index(persisted) ?? 0;
}

/**
 * Test sessions are restored after a reload when II bypass is on, and also when this tab already signed in as a test
 * identity (the pinned channel): the tab's own session does not end because the realm's flag reads off. Without the pin,
 * a reload counted as signed in (`isAuthenticated`) while nothing restored the identity, and every screen failed
 * reading its principal.
 */
export function shouldRestoreTestModeSession(
	testModeIIBypass: boolean,
	pinnedChannel: AuthChannel | null = null
): boolean {
	return __REALMS_TEST_BUILD__ && (testModeIIBypass || pinnedChannel === 'test');
}
