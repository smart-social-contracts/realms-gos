// The realm's token ledger, a standard ICRC-1 ledger whose minting account is the founder
// (scripts/e2e_standalone_realm.py): what the founder transfers is minted.
import { Actor, HttpAgent } from '@dfinity/agent';
import type { Ed25519KeyIdentity } from '@dfinity/identity';
import { Principal } from '@dfinity/principal';
import type { StandaloneRealm } from './realm';

interface Account {
	owner: string;
	/** Hex, as the realm gives the account an invoice is paid into. */
	subaccount?: string;
}

const ledgerIdl = ({ IDL }: { IDL: typeof import('@dfinity/candid').IDL }) => {
	const Account = IDL.Record({ owner: IDL.Principal, subaccount: IDL.Opt(IDL.Vec(IDL.Nat8)) });
	return IDL.Service({
		icrc1_balance_of: IDL.Func([Account], [IDL.Nat], ['query']),
		icrc1_transfer: IDL.Func(
			[
				IDL.Record({
					to: Account,
					amount: IDL.Nat,
					fee: IDL.Opt(IDL.Nat),
					memo: IDL.Opt(IDL.Vec(IDL.Nat8)),
					from_subaccount: IDL.Opt(IDL.Vec(IDL.Nat8)),
					created_at_time: IDL.Opt(IDL.Nat64)
				})
			],
			[IDL.Variant({ Ok: IDL.Nat, Err: IDL.Reserved })],
			[]
		)
	});
};

interface Ledger {
	icrc1_balance_of: (a: unknown) => Promise<bigint>;
	icrc1_transfer: (a: unknown) => Promise<{ Ok: bigint } | { Err: unknown }>;
}

async function ledgerAs(realm: StandaloneRealm, identity?: Ed25519KeyIdentity) {
	const agent = await HttpAgent.create({
		host: realm.network_url,
		identity,
		shouldFetchRootKey: true
	});
	return Actor.createActor<Ledger>(ledgerIdl as never, {
		agent,
		canisterId: realm.token_ledger
	});
}

function toAccount({ owner, subaccount }: Account) {
	return {
		owner: Principal.fromText(owner),
		subaccount: subaccount ? [Uint8Array.from(Buffer.from(subaccount, 'hex'))] : []
	};
}

/** The founder, the ledger's minting account, mints `amount` into `to`. */
export async function mint(
	realm: StandaloneRealm,
	founder: Ed25519KeyIdentity,
	to: Account,
	amount: bigint
) {
	const reply = await (
		await ledgerAs(realm, founder)
	).icrc1_transfer({
		to: toAccount(to),
		amount,
		fee: [],
		memo: [],
		from_subaccount: [],
		created_at_time: []
	});
	if ('Err' in reply) throw new Error(`the ledger refused the mint: ${JSON.stringify(reply.Err)}`);
}

export async function balanceOf(realm: StandaloneRealm, to: Account): Promise<bigint> {
	return (await ledgerAs(realm)).icrc1_balance_of(toAccount(to));
}
