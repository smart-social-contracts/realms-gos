// The signed-in principal's marketplace roles, refetched whenever the
// principal changes. Only decides what the UI shows: every admin call is
// checked again by the canister.

import { writable, type Readable } from 'svelte/store';
import { principalStore } from './auth';
import { marketplaceClient, type MyRoles } from './marketplace-client';

const NO_ROLES: MyRoles = { is_controller: false, is_admin: false, is_reviewer: false };

const store = writable<MyRoles>(NO_ROLES);
export const roles: Readable<MyRoles> = store;

let currentPrincipal = '';

export async function refreshRoles(): Promise<void> {
  const principal = currentPrincipal;
  if (!principal) {
    store.set(NO_ROLES);
    return;
  }
  const r = await marketplaceClient.getMyRoles();
  if (principal === currentPrincipal) store.set(r);
}

principalStore.subscribe((p) => {
  const next = p ? p.toText() : '';
  if (next === currentPrincipal) return;
  currentPrincipal = next;
  void refreshRoles();
});
