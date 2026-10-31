/**
 * Legacy API surface expected by the market_place runtime extension bundle.
 *
 * The extension calls ctx.backend.* marketplace methods directly, but the host
 * normally passes the realm_backend actor. This adapter wraps the shared
 * marketplace_backend canister (resolved from realm status) and maps the current
 * Candid API to the older method names the extension UI was written against.
 */

import { Actor, HttpAgent } from '@dfinity/agent';
import { get } from 'svelte/store';
import { backendReady, backendStore } from '$lib/canisters';
import { principal } from '$lib/stores/auth';

function toNumber(v: unknown): number {
	if (typeof v === 'bigint') return Number(v);
	if (typeof v === 'number') return v;
	if (v == null) return 0;
	const n = Number(v);
	return Number.isFinite(n) ? n : 0;
}

function unwrapOk<T>(variant: { Ok?: T; Err?: string } | T): T {
	if (variant && typeof variant === 'object' && 'Ok' in variant) {
		if (variant.Err) throw new Error(String(variant.Err));
		return variant.Ok as T;
	}
	return variant as T;
}

function normListing(raw: Record<string, unknown>) {
	return {
		...raw,
		extension_id: String(raw.extension_id ?? ''),
		price_e8s: toNumber(raw.price_e8s),
		downloads: toNumber(raw.installs ?? raw.downloads ?? 0),
		installs: toNumber(raw.installs ?? raw.downloads ?? 0),
		created_at: toNumber(raw.created_at),
		updated_at: toNumber(raw.updated_at),
	};
}

function normPurchase(raw: Record<string, unknown>) {
	const itemId = String(raw.item_id ?? raw.extension_id ?? '');
	return {
		...raw,
		extension_id: itemId,
		id: itemId,
		price_paid_e8s: toNumber(raw.price_paid_e8s),
		purchased_at: toNumber(raw.purchased_at),
	};
}

async function getRealmAgent(): Promise<HttpAgent> {
	await backendReady;
	const realmActor = get(backendStore) as { _agent?: HttpAgent } | null;
	if (realmActor?._agent) return realmActor._agent;
	return new HttpAgent({ verifyQuerySignatures: false });
}

// Calls a paged marketplace endpoint until it has `count` rows or reaches the
// end (next_cursor 0): each call reads a bounded number of rows and can come
// back short while more remain.
type RawPage = { listings: Record<string, unknown>[]; next_cursor: bigint };

async function collectPages(
	fetch: (cursor: bigint, count: bigint) => Promise<unknown>,
	count: number,
): Promise<Record<string, unknown>[]> {
	const rows: Record<string, unknown>[] = [];
	let cursor = 0n;
	for (let call = 0; call < 10; call++) {
		const r = (await fetch(cursor, BigInt(count - rows.length))) as RawPage;
		rows.push(...(r.listings ?? []));
		cursor = r.next_cursor;
		if (cursor === 0n || rows.length >= count) break;
	}
	return rows;
}

async function createMarketplaceActor(canisterId: string) {
	const { idlFactory } = await import('declarations/marketplace_backend');
	const agent = await getRealmAgent();
	return Actor.createActor(idlFactory, { agent, canisterId });
}

export type MarketplaceExtensionBackend = {
	list_extensions: () => Promise<{ listings: ReturnType<typeof normListing>[] }>;
	marketplace_stats: () => Promise<{
		total_extensions: number;
		total_developers: number;
		total_purchases: number;
		total_revenue_e8s?: number;
		platform_earnings_e8s?: number;
	}>;
	search_marketplace: (query: string) => Promise<{ listings: ReturnType<typeof normListing>[] }>;
	get_my_purchases: () => Promise<{ data: ReturnType<typeof normPurchase>[] }>;
	buy_extension: (extensionId: string) => Promise<{ Ok?: string; Err?: string }>;
	check_developer_license: () => Promise<{ Ok?: Record<string, unknown>; Err?: string }>;
	purchase_developer_license: () => Promise<{ Ok?: string; Err?: string }>;
	get_my_developer_stats: () => Promise<{ Ok?: Record<string, unknown>; Err?: string }>;
	publish_extension: (payloadJson: string) => Promise<{ Ok?: string; Err?: string }>;
	unpublish_extension: (extensionId: string) => Promise<{ Ok?: string; Err?: string }>;
	request_payout: () => Promise<{ Ok?: string; Err?: string }>;
};

/**
 * Build the object to pass as ctx.backend for the market_place extension.
 */
export async function createMarketplaceExtensionBackend(
	marketplaceCanisterId: string,
	fileRegistryCanisterId = '',
): Promise<MarketplaceExtensionBackend> {
	if (!marketplaceCanisterId) {
		throw new Error(
			'Marketplace canister is not configured for this realm (missing marketplace in status).',
		);
	}

	const mp = await createMarketplaceActor(marketplaceCanisterId);

	return {
		async list_extensions() {
			// Up to 100 newest. The paged endpoint reads a bounded number of rows
			// per call; a marketplace canister without it gets the old call.
			let rows: Record<string, unknown>[];
			try {
				rows = await collectPages((c, n) => mp.list_marketplace_extensions_page(c, n, false), 100);
			} catch {
				const r = (await mp.list_marketplace_extensions(BigInt(1), BigInt(100), false)) as Partial<RawPage>;
				rows = r.listings ?? [];
			}
			return { listings: rows.map((e) => normListing(e)) };
		},

		async marketplace_stats() {
			const s = unwrapOk(await mp.status());
			return {
				total_extensions: toNumber(s.extensions_count),
				total_developers: toNumber(s.licenses_count),
				total_purchases: toNumber(s.purchases_count),
				total_revenue_e8s: 0,
				platform_earnings_e8s: 0,
			};
		},

		async search_marketplace(query: string) {
			let rows: Record<string, unknown>[];
			try {
				rows = await collectPages((c, n) => mp.search_extensions_page(query, c, n, false), 100);
			} catch {
				rows = ((await mp.search_extensions(query, false)) as Record<string, unknown>[]) ?? [];
			}
			return { listings: rows.map((e) => normListing(e)) };
		},

		async get_my_purchases() {
			const rows = await mp.get_my_purchases();
			const extPurchases = (rows ?? [])
				.filter((p: Record<string, unknown>) => (p.item_kind ?? 'ext') === 'ext')
				.map((p: Record<string, unknown>) => normPurchase(p));
			return { data: extPurchases };
		},

		async buy_extension(extensionId: string) {
			return mp.buy_extension(extensionId);
		},

		async check_developer_license() {
			return mp.get_license_status();
		},

		async purchase_developer_license() {
			const me = get(principal);
			if (!me) return { Err: 'Sign in to purchase a developer license' };
			try {
				const pricing = await mp.get_license_pricing_q();
				return mp.record_license_payment({
					principal: me,
					stripe_session_id: `realm-ui-${Date.now()}`,
					amount_usd_cents: toNumber(pricing.license_price_usd_cents),
					duration_seconds: BigInt(toNumber(pricing.license_duration_seconds)),
					payment_method: 'realm_extension_ui',
					note: 'Requested from realm market_place extension',
				});
			} catch (e: unknown) {
				const msg = e instanceof Error ? e.message : String(e);
				return {
					Err:
						msg.includes('Unauthorized') || msg.includes('billing service')
							? 'Developer licenses are purchased via the billing service. In test mode, ask a controller to call grant_manual_license on the marketplace canister.'
							: msg,
				};
			}
		},

		async get_my_developer_stats() {
			try {
				const rows = await mp.get_my_extensions();
				const listings = rows ?? [];
				const totalDownloads = listings.reduce(
					(sum: number, e: Record<string, unknown>) =>
						sum + toNumber(e.installs ?? e.downloads),
					0,
				);
				return {
					Ok: {
						total_extensions: listings.length,
						total_downloads: totalDownloads,
						total_sales: 0,
						total_revenue_e8s: 0,
						pending_payout_e8s: 0,
					},
				};
			} catch (e: unknown) {
				return { Err: e instanceof Error ? e.message : String(e) };
			}
		},

		async publish_extension(payloadJson: string) {
			try {
				const data = JSON.parse(payloadJson) as Record<string, unknown>;
				const cats = data.categories;
				const categories =
					typeof cats === 'string'
						? cats
						: JSON.stringify(Array.isArray(cats) ? cats : []);
				const extId = String(data.extension_id ?? '');
				const input = {
					extension_id: extId,
					name: String(data.name ?? extId),
					description: String(data.description ?? ''),
					version: String(data.version ?? '1.0.0'),
					price_e8s: BigInt(toNumber(data.price_e8s)),
					icon: String(data.icon ?? 'layers'),
					categories,
					languages: '[]',
					file_registry_canister_id: fileRegistryCanisterId,
					file_registry_namespace: `ext/${extId}/${String(data.version ?? '1.0.0')}`,
					download_url: String(data.download_url ?? ''),
				};
				return mp.create_extension(input);
			} catch (e: unknown) {
				return { Err: e instanceof Error ? e.message : String(e) };
			}
		},

		async unpublish_extension(extensionId: string) {
			return mp.delist_extension(extensionId);
		},

		async request_payout() {
			return {
				Err: 'Payout requests are not available in this marketplace version yet.',
			};
		},
	};
}
