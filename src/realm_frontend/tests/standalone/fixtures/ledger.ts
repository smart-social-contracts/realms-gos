// The realm's token ledger, ic-python-token in test mode (scripts/e2e_standalone_realm.py): anyone may mint.
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
	const TransferError = IDL.Variant({
		GenericError: IDL.Record({ message: IDL.Text, error_code: IDL.Nat }),
		TemporarilyUnavailable: IDL.Null,
		BadBurn: IDL.Record({ min_burn_amount: IDL.Nat }),
		Duplicate: IDL.Record({ duplicate_of: IDL.Nat }),
		BadFee: IDL.Record({ expected_fee: IDL.Nat }),
		CreatedInFuture: IDL.Record({ ledger_time: IDL.Nat64 }),
		TooOld: IDL.Null,
		InsufficientFunds: IDL.Record({ balance: IDL.Nat })
	});
	return IDL.Service({
		icrc1_balance_of: IDL.Func([Account], [IDL.Nat], ['query']),
		icrc1_transfer: IDL.Func(
			[
				IDL.Record({
					to: Account,
					fee: IDL.Opt(IDL.Nat),
					memo: IDL.Opt(IDL.Vec(IDL.Nat8)),
					from_subaccount: IDL.Opt(IDL.Vec(IDL.Nat8)),
					created_at_time: IDL.Opt(IDL.Nat64),
					amount: IDL.Nat
				})
			],
			[IDL.Variant({ Ok: IDL.Nat, Err: TransferError })],
			[]
		),
		mint: IDL.Func(
			[IDL.Record({ to: Account, amount: IDL.Nat })],
			[IDL.Record({ success: IDL.Bool, error: IDL.Opt(IDL.Text) })],
			[]
		)
	});
};

interface Ledger {
	icrc1_balance_of: (a: unknown) => Promise<bigint>;
	icrc1_transfer: (a: unknown) => Promise<{ Ok: bigint } | { Err: Record<string, unknown> }>;
	mint: (a: unknown) => Promise<{ success: boolean; error: [] | [string] }>;
}

async function ledger(realm: StandaloneRealm, identity?: Ed25519KeyIdentity) {
	const agent = await HttpAgent.create({
		host: realm.network_url,
		identity,
		shouldFetchRootKey: true
	});
	return Actor.createActor<Ledger>(ledgerIdl as never, { agent, canisterId: realm.token_ledger });
}

function toAccount({ owner, subaccount }: Account) {
	return {
		owner: Principal.fromText(owner),
		subaccount: subaccount ? [Uint8Array.from(Buffer.from(subaccount, 'hex'))] : []
	};
}

export async function mint(realm: StandaloneRealm, to: Account, amount: bigint) {
	const reply = await (await ledger(realm)).mint({ to: toAccount(to), amount });
	if (!reply.success) throw new Error(`the ledger refused the mint: ${reply.error[0]}`);
}

export async function balanceOf(realm: StandaloneRealm, to: Account): Promise<bigint> {
	return (await ledger(realm)).icrc1_balance_of(toAccount(to));
}

/** `from` pays `amount` to `to` with the ledger's default fee, as a wallet would. */
export async function transfer(
	realm: StandaloneRealm,
	from: Ed25519KeyIdentity,
	to: Account,
	amount: bigint
) {
	const reply = await (
		await ledger(realm, from)
	).icrc1_transfer({
		to: toAccount(to),
		amount,
		fee: [],
		memo: [],
		from_subaccount: [],
		created_at_time: []
	});
	if ('Err' in reply)
		throw new Error(
			`the ledger refused the transfer: ${JSON.stringify(reply.Err, (_, v) => (typeof v === 'bigint' ? String(v) : v))}`
		);
}
