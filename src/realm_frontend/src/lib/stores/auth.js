// src/lib/stores/auth.js
import { writable } from 'svelte/store';
import { isEmbeddedInPortal } from '$lib/portal-bridge.ts';

function storageAvailable() {
    if (typeof window === 'undefined') return false;
    try {
        const key = '__realms_storage_test__';
        sessionStorage.setItem(key, '1');
        sessionStorage.removeItem(key);
        return true;
    } catch {
        return false;
    }
}

function localStorageAvailable() {
    if (typeof window === 'undefined') return false;
    try {
        const key = '__realms_storage_test__';
        localStorage.setItem(key, '1');
        localStorage.removeItem(key);
        return true;
    } catch {
        return false;
    }
}

// Portal iframes: sessionStorage (same tab). Standalone: localStorage (cross-tab).
const useSessionStorage = isEmbeddedInPortal() && storageAvailable();
const canPersist = useSessionStorage || localStorageAvailable();

function createPersistentStore(key, defaultValue) {
    if (!canPersist) {
        return writable(defaultValue);
    }

    const storage = useSessionStorage ? sessionStorage : localStorage;
    const initialValue = storage.getItem(key)
        ? JSON.parse(storage.getItem(key))
        : defaultValue;

    const store = writable(initialValue);

    store.subscribe(value => {
        try {
            storage.setItem(key, JSON.stringify(value));
        } catch {
            // sandbox or private mode
        }
    });

    if (!useSessionStorage) {
        window.addEventListener('storage', (event) => {
            if (event.key !== key || event.newValue === null) return;
            try {
                store.set(JSON.parse(event.newValue));
            } catch {
                // ignore malformed cross-tab payloads
            }
        });
    }

    return store;
}

export const isAuthenticated = createPersistentStore('auth_isAuthenticated', false);
export const userIdentity = createPersistentStore('auth_userIdentity', null);
export const principal = createPersistentStore('auth_principal', '');

export const universe = writable('');
export const snapshots = writable('');

// Test builds: the index of the test identity this tab is signed in as (0 is Identity 1), or null.
export const testIdentityIndex = writable(null);
