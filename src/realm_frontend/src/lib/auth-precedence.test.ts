import { describe, expect, it } from 'vitest';
import {
	resolveAuthChannel,
	resolveEffectiveAuthChannel,
	resolveTestIdentityIndex,
	shouldAttemptPortalAuth,
	shouldLoginWithTestIdentity,
	shouldPreferTestModeLogin,
	shouldRestoreTestModeSession,
	shouldUseTestModeAuth
} from './auth-precedence';

describe('auth-precedence', () => {
	it('ambient precedence prefers portal when embedded even with II bypass', () => {
		expect(resolveAuthChannel(true, true)).toBe('portal');
		expect(shouldAttemptPortalAuth(true)).toBe(true);
		expect(shouldUseTestModeAuth(true, true)).toBe(false);
	});

	it('uses test-mode auth when standalone with II bypass', () => {
		expect(resolveAuthChannel(false, true)).toBe('test');
		expect(shouldAttemptPortalAuth(false)).toBe(false);
		expect(shouldUseTestModeAuth(false, true)).toBe(true);
	});

	it('uses Internet Identity when not embedded and no bypass', () => {
		expect(resolveAuthChannel(false, false)).toBe('ii');
		expect(shouldUseTestModeAuth(false, false)).toBe(false);
	});

	it('allows join page to prefer test identities inside portal iframe', () => {
		expect(shouldPreferTestModeLogin(true, true)).toBe(true);
		expect(shouldPreferTestModeLogin(true, false)).toBe(true);
		expect(shouldPreferTestModeLogin(false, true)).toBe(false);
	});

	it('restores test sessions when II bypass is on (including portal reloads)', () => {
		expect(shouldRestoreTestModeSession(true)).toBe(true);
		expect(shouldRestoreTestModeSession(false)).toBe(false);
	});

	it('pinned test channel wins over portal embed ambient precedence', () => {
		expect(resolveEffectiveAuthChannel(true, true, 'test')).toBe('test');
		expect(resolveEffectiveAuthChannel(true, false, 'test')).toBe('test');
	});

	it('pinned portal channel wins over standalone test ambient precedence', () => {
		expect(resolveEffectiveAuthChannel(false, true, 'portal')).toBe('portal');
	});

	it('falls back to ambient precedence when no session pin is set', () => {
		expect(resolveEffectiveAuthChannel(true, true, null)).toBe('portal');
		expect(resolveEffectiveAuthChannel(false, true, null)).toBe('test');
		expect(resolveEffectiveAuthChannel(false, false, null)).toBe('ii');
	});

	it('Continue as Identity N uses test login even inside the portal iframe', () => {
		expect(
			shouldLoginWithTestIdentity({
				identityIndex: 1,
				preferTestMode: true,
				testModeIIBypass: true,
				embeddedInPortal: true
			})
		).toBe(true);
		expect(
			shouldLoginWithTestIdentity({
				identityIndex: 0,
				preferTestMode: false,
				testModeIIBypass: false,
				embeddedInPortal: true
			})
		).toBe(true);
	});

	it('does not wait on portal II when the join page opts into bypass login', () => {
		expect(
			shouldLoginWithTestIdentity({
				preferTestMode: true,
				testModeIIBypass: false,
				embeddedInPortal: true
			})
		).toBe(true);
	});

	it('keeps portal II for a normal embed with no picker index', () => {
		expect(
			shouldLoginWithTestIdentity({
				preferTestMode: false,
				testModeIIBypass: true,
				embeddedInPortal: true
			})
		).toBe(false);
	});

	it('a reload keeps the test identity the tab signed in as', () => {
		// Asked for: the picker or ?ti= in the address.
		expect(resolveTestIdentityIndex(2, 1)).toBe(2);
		expect(resolveTestIdentityIndex(0, 1)).toBe(0);
		// Nothing asked: the tab's own identity, not Identity 1.
		expect(resolveTestIdentityIndex(null, 1)).toBe(1);
		expect(resolveTestIdentityIndex(undefined, null)).toBe(0);
		// A random identity is fresh unless one was asked for.
		expect(resolveTestIdentityIndex(null, 1, true)).toBeNull();
		expect(resolveTestIdentityIndex(3, 1, true)).toBe(3);
	});
});

