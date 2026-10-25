// @ts-ignore
import { backend } from '$lib/canisters';
import { singleFlight } from './singleFlight';

export interface InfraConfig {
	fileRegistryCanisterId?: string;
	marketplaceCanisterId?: string;
}

export function parseInfraConfig(raw: unknown): InfraConfig {
	const resp = typeof raw === 'string' ? JSON.parse(raw) : raw;
	const canisters: { canister_id: string; canister_type: string }[] =
		(resp as any)?.data?.status?.canisters ?? [];
	const fr = canisters.find((c) => c.canister_type === 'file_registry');
	const mp = canisters.find((c) => c.canister_type === 'marketplace');
	return {
		fileRegistryCanisterId: fr?.canister_id ?? '',
		marketplaceCanisterId: mp?.canister_id ?? '',
	};
}

// Infra canister ids do not change during a session, but status() is one of
// the slowest queries; extension mounts used to wait on it every navigation.
const sharedInfraConfig = singleFlight(async () => parseInfraConfig(await backend.status()), {
	ttlMs: Number.POSITIVE_INFINITY,
	cacheIf: (c) => Boolean(c.fileRegistryCanisterId || c.marketplaceCanisterId),
});

export async function getInfraConfig(): Promise<InfraConfig> {
	try {
		return await sharedInfraConfig();
	} catch {
		return {};
	}
}
