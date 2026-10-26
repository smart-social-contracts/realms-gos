import { Actor, HttpAgent } from '@dfinity/agent';
import { IDL } from '@dfinity/candid';

const icrc1IdlFactory: IDL.InterfaceFactory = ({ IDL: idl }) =>
	idl.Service({
		icrc1_symbol: idl.Func([], [idl.Text], ['query'])
	});

/** Read `icrc1_symbol` from a ledger the founder typed. */
export async function lookupLedgerSymbol(ledgerId: string): Promise<string> {
	const ledger = ledgerId.trim();
	if (!ledger) throw new Error('Enter a ledger canister id');
	// Anonymous: a portal-scoped delegation only signs for the realm's own canisters.
	const local = ['localhost', '127.0.0.1'].some((h) => globalThis.location?.hostname?.includes(h));
	const agent = await HttpAgent.create({ verifyQuerySignatures: false, shouldFetchRootKey: local });
	const service = Actor.createActor(icrc1IdlFactory, { agent, canisterId: ledger });
	const symbol = await service.icrc1_symbol();
	const text = String(symbol || '').trim();
	if (!text) throw new Error('The ledger did not return a symbol');
	return text;
}
