/**
 * Federated membership probe (issue #156).
 *
 * There is no central principal→quarter index. A returning user's quarter is
 * recovered by probing known canisters with read('me'). Shared by
 * the profiles store (login auto-route) and /join (prevent accidental
 * double-registration).
 */
import type { Reply } from '$lib/realm';

/**
 * ``read('me')`` in the shape of the old get_my_user_status reply (``data.userGet``), which
 * membership callers read. Fails when the caller is not a user here. ``departments`` is always
 * empty: ``me`` does not list seats.
 */
export function userStatusFromMe(reply: Reply): any {
	if (!reply.ok) return { success: false, data: { error: reply.error } };
	const user = reply.user;
	if (!user.profiles) return { success: false, data: { error: 'not a user of this realm' } };
	return {
		success: true,
		data: {
			userGet: {
				principal: user.id,
				nickname: user.nickname ?? '',
				avatar: user.avatar ?? '',
				profiles: user.profiles,
				departments: [],
				assigned_quarter: user.home_quarter ?? '',
				private_data: reply.private_data ?? ''
			}
		}
	};
}

export type MembershipHit = {
	canisterId: string;
	isCapital: boolean;
	response: any;
	profiles: string[];
};

export type ProbeResult = {
	/** First hit (preferred: valid cache, else first federated match). */
	primary: MembershipHit | null;
	/** All quarters where the caller has profiles. */
	hits: MembershipHit[];
	capitalId: string;
};

export function hasMembership(r: any): boolean {
	return !!(
		r &&
		r.success &&
		r.data &&
		r.data.userGet &&
		(r.data.userGet.profiles || []).length > 0
	);
}

function profilesOf(r: any): string[] {
	return (r?.data?.userGet?.profiles || []) as string[];
}

/**
 * Choose which membership to activate when several exist.
 * Prefers a still-valid localStorage cache; otherwise the first hit.
 */
export function pickPrimaryHit(
	hits: MembershipHit[],
	cachedCanisterId: string = ''
): MembershipHit | null {
	if (!hits.length) return null;
	if (cachedCanisterId) {
		const cached = hits.find((h) => h.canisterId === cachedCanisterId);
		if (cached) return cached;
	}
	return hits[0];
}

/**
 * Activate a membership hit for this browser session (actor + optional cache).
 */
export async function activateMembership(
	hit: MembershipHit,
	capitalId: string,
	options?: { cache?: boolean }
): Promise<void> {
	const cache = options?.cache !== false;
	// @ts-ignore
	const { setActiveQuarter } = await import('$lib/canisters');
	// @ts-ignore
	const { activeQuarterId } = await import('$lib/stores/quarters');

	const cid = hit.canisterId;
	if (cid && !hit.isCapital && cid !== capitalId) {
		activeQuarterId.set(cid);
		await setActiveQuarter(cid);
		if (cache && typeof localStorage !== 'undefined') {
			localStorage.setItem('home_quarter', cid);
		}
	} else {
		activeQuarterId.set(null);
		await setActiveQuarter(null);
		if (cache && typeof localStorage !== 'undefined') {
			localStorage.removeItem('home_quarter');
		}
	}
}

/**
 * Probe capital + every quarter from quarter_directory for the caller's membership.
 * Prefers a valid localStorage home_quarter cache when it still recognizes the caller.
 */
export async function probeFederatedMembership(options?: {
	activate?: boolean;
	cache?: boolean;
}): Promise<ProbeResult> {
	const activate = options?.activate !== false;
	const cache = options?.cache !== false;

	// @ts-ignore
	const { createQuarterActor } = await import('$lib/canisters');
	const { read, must } = await import('$lib/realm');

	const hits: MembershipHit[] = [];
	let capitalId = '';
	let selfId = '';
	let candidates: { canister_id: string; is_capital?: boolean }[] = [];

	try {
		const directory = await must(read('quarter_directory'));
		capitalId = directory.capital;
		selfId = directory.quarters[0].id;
		candidates = directory.quarters.map((q: { id: string; capital: boolean }) => ({
			canister_id: q.id,
			is_capital: q.capital
		}));
	} catch (e) {
		console.warn('quarter_directory failed during membership probe:', e);
		candidates = [{ canister_id: '', is_capital: true }];
	}

	const cached =
		typeof localStorage !== 'undefined' ? localStorage.getItem('home_quarter') || '' : '';

	// Prefer probing the cached quarter first so we short-circuit when possible.
	const ordered = [...candidates];
	if (cached) {
		const idx = ordered.findIndex((q) => q.canister_id === cached);
		if (idx > 0) {
			const [pref] = ordered.splice(idx, 1);
			ordered.unshift(pref);
		} else if (idx < 0) {
			ordered.unshift({ canister_id: cached, is_capital: false });
		}
	}

	for (const q of ordered) {
		const cid = q.canister_id || '';
		const isCapital = !!(q.is_capital || (capitalId && cid === capitalId) || !cid);
		try {
			const reply: Reply =
				!cid || cid === selfId
					? await read('me')
					: JSON.parse(await (await createQuarterActor(cid)).read('me', '{}'));
			const r = userStatusFromMe(reply);
			if (hasMembership(r)) {
				hits.push({
					canisterId: cid || capitalId,
					isCapital,
					response: r,
					profiles: profilesOf(r),
				});
			}
		} catch (qe) {
			console.warn('Quarter membership probe failed for', cid || '(capital)', qe);
		}
	}

	const primary = pickPrimaryHit(hits, cached);

	if (primary && activate) {
		await activateMembership(primary, capitalId, { cache });
	}

	return { primary, hits, capitalId };
}
