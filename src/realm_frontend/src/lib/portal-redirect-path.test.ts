import { describe, expect, it, vi } from 'vitest';
import {
  consumePortalSigningIn,
  consumePortalSigningOut,
  markPortalSigningIn,
  markPortalSigningOut,
  normalizePortalRedirectPath,
  portalLoginHref,
  portalLoginReturnPath,
  portalSharePathFromUrl,
  resolvePortalNavSyncHref,
  shouldPortalEnterPush,
} from './portal-redirect-path';

describe('normalizePortalRedirectPath', () => {
  it('returns empty string for root', () => {
    expect(normalizePortalRedirectPath('/')).toBe('');
    expect(normalizePortalRedirectPath('')).toBe('');
  });

  it('passes through normal SPA routes unchanged', () => {
    expect(normalizePortalRedirectPath('/admin/manifest')).toBe('/admin/manifest');
    expect(normalizePortalRedirectPath('/extensions/member_dashboard')).toBe(
      '/extensions/member_dashboard',
    );
  });

  it('maps internal extension bundle paths to /extensions/{id}', () => {
    expect(
      normalizePortalRedirectPath('/ext/member_dashboard/1.1.2/frontend/dist/index.html'),
    ).toBe('/extensions/member_dashboard');
    expect(normalizePortalRedirectPath('/ext/voting/0.2.0/frontend/dist/index.js')).toBe(
      '/extensions/voting',
    );
  });
});

describe('resolvePortalNavSyncHref', () => {
  it('does not navigate when the host path matches except iframe-only params', () => {
    expect(resolvePortalNavSyncHref('/join?portal=1&slug=realmtest4', '/join')).toBeNull();
    expect(
      resolvePortalNavSyncHref('/join?portal=1&slug=realmtest4', '/join?portal=1&slug=realmtest4'),
    ).toBeNull();
  });

  it('preserves portal embed params when the host path actually changes', () => {
    expect(resolvePortalNavSyncHref('/join?portal=1&slug=realmtest4', '/extensions/member_dashboard')).toBe(
      '/extensions/member_dashboard?portal=1&slug=realmtest4',
    );
  });

  it('keeps invite query params from the sync path', () => {
    expect(resolvePortalNavSyncHref('/join?portal=1&slug=x', '/join?invite=abc')).toBe(
      '/join?invite=abc&portal=1&slug=x',
    );
  });

  it('does not drop an invite already on the iframe URL when the host syncs pathname only', () => {
    expect(
      resolvePortalNavSyncHref('/join?portal=1&slug=x&invite=abc', '/join'),
    ).toBeNull();
  });

  it('does not drop ?ti= when the host syncs pathname only', () => {
    expect(
      resolvePortalNavSyncHref('/join?portal=1&slug=realmtest6&ti=1', '/join'),
    ).toBeNull();
    expect(
      resolvePortalNavSyncHref('/join?portal=1&slug=realmtest6&ti=0', '/join'),
    ).toBeNull();
  });

  it('copies host ?ti= onto the iframe when the embed src omitted it', () => {
    expect(resolvePortalNavSyncHref('/join?portal=1&slug=realmtest6', '/join?ti=1')).toBe(
      '/join?ti=1&portal=1&slug=realmtest6',
    );
    expect(resolvePortalNavSyncHref('/join?portal=1&slug=realmtest6', '/join?ti=0')).toBe(
      '/join?ti=0&portal=1&slug=realmtest6',
    );
  });

  it('keeps skip_ii and test_mode across a real path change', () => {
    expect(
      resolvePortalNavSyncHref(
        '/join?portal=1&slug=x&ti=1&skip_ii=true&test_mode=true',
        '/extensions/public_dashboard',
      ),
    ).toBe('/extensions/public_dashboard?portal=1&slug=x&ti=1&skip_ii=true&test_mode=true');
  });
});

describe('shouldPortalEnterPush', () => {
  it('does not push /join when the host has not reported a path (keeps ?ti=)', () => {
    expect(shouldPortalEnterPush('/join', null)).toBe(false);
    expect(shouldPortalEnterPush('/join', undefined)).toBe(false);
    expect(shouldPortalEnterPush('/setup', '')).toBe(false);
  });

  it('pushes extension and ME chrome routes when the host has not reported a path (old portal)', () => {
    expect(shouldPortalEnterPush('/extensions/import_export', null)).toBe(true);
    expect(shouldPortalEnterPush('/extensions/member_dashboard', undefined)).toBe(true);
    expect(shouldPortalEnterPush('/messages', null)).toBe(true);
    expect(shouldPortalEnterPush('/identities', undefined)).toBe(true);
    expect(shouldPortalEnterPush('/settings', '')).toBe(true);
  });

  it('does not push when iframe and host already agree (keeps ?ti= on first paint)', () => {
    expect(
      shouldPortalEnterPush('/extensions/member_dashboard', '/extensions/member_dashboard'),
    ).toBe(false);
    expect(shouldPortalEnterPush('/extensions/member_dashboard/', '/extensions/member_dashboard')).toBe(
      false,
    );
  });

  it('pushes when a full iframe reload landed on a different extension than the host bar', () => {
    expect(
      shouldPortalEnterPush('/extensions/import_export', '/extensions/member_dashboard'),
    ).toBe(true);
  });

  it('pushes when Messages is on screen but the host bar is still Account', () => {
    expect(shouldPortalEnterPush('/messages', '/identities')).toBe(true);
    expect(shouldPortalEnterPush('/identities', '/messages')).toBe(true);
  });
});

describe('portalSharePathFromUrl', () => {
  it('drops iframe-only portal/slug params and keeps the extension path', () => {
    expect(
      portalSharePathFromUrl({
        pathname: '/extensions/import_export',
        search: '?portal=1&slug=initargdemo',
        hash: '',
      }),
    ).toBe('/extensions/import_export');
  });

  it('keeps test-identity query params', () => {
    expect(
      portalSharePathFromUrl({
        pathname: '/extensions/import_export',
        search: '?portal=1&slug=x&ti=1',
        hash: '',
      }),
    ).toBe('/extensions/import_export?ti=1');
  });
});

describe('portalLoginReturnPath', () => {
  it('sends a public page back to /join', () => {
    expect(
      portalLoginReturnPath('/extensions/public_dashboard', '?portal=1&slug=staging-realmsgos-11'),
    ).toBe('/join');
  });

  it('keeps an invite already on /join and drops iframe params', () => {
    expect(portalLoginReturnPath('/join', '?portal=1&slug=x&invite=abc')).toBe('/join?invite=abc');
  });
});

describe('portalLoginHref', () => {
  it('builds the portal login page with a realm return path', () => {
    expect(
      portalLoginHref({
        origin: 'https://staging.gos.earth',
        slug: 'staging-realmsgos-11',
        returnPath: '/join',
      }),
    ).toBe(
      'https://staging.gos.earth/join?returnTo=' +
        encodeURIComponent('/r/staging-realmsgos-11/join'),
    );
  });

  it('rejects a protocol-relative return path', () => {
    expect(
      portalLoginHref({
        origin: 'https://staging.gos.earth/',
        slug: 'agora',
        returnPath: '//evil.example',
      }),
    ).toBe('https://staging.gos.earth/join?returnTo=' + encodeURIComponent('/r/agora/join'));
  });
});

describe('consumePortalSigningOut', () => {
  it('is true only for the page load after logout', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    expect(consumePortalSigningOut()).toBe(false);
    markPortalSigningOut();
    expect(consumePortalSigningOut()).toBe(true);
    expect(consumePortalSigningOut()).toBe(false);
    vi.unstubAllGlobals();
  });
});

describe('consumePortalSigningIn', () => {
  it('is true once for the page load after a recent portal login', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, value),
      removeItem: (key: string) => store.delete(key),
    });
    expect(consumePortalSigningIn()).toBe(false);
    markPortalSigningIn(1_000);
    expect(consumePortalSigningIn(61_000)).toBe(true);
    expect(consumePortalSigningIn(61_000)).toBe(false);
    markPortalSigningIn(1_000);
    expect(consumePortalSigningIn(1_000 + 11 * 60 * 1000)).toBe(false);
    vi.unstubAllGlobals();
  });
});
