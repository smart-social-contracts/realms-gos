// The realm's token ledger, ic-python-token in test mode (scripts/e2e_standalone_realm.py): anyone may mint.
import { Actor, HttpAgent } from '@dfinity/agent';
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
		mint: IDL.Func(
			[IDL.Record({ to: Account, amount: IDL.Nat })],
			[IDL.Record({ success: IDL.Bool, error: IDL.Opt(IDL.Text) })],
			[]
		)
	});
};

interface Ledger {
	icrc1_balance_of: (a: unknown) => Promise<bigint>;
	mint: (a: unknown) => Promise<{ success: boolean; error: [] | [string] }>;
}

async function ledger(realm: StandaloneRealm) {
	const agent = await HttpAgent.create({ host: realm.network_url, shouldFetchRootKey: true });
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
