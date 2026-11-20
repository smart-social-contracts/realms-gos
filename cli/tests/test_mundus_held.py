"""``realms mundus``: the PocketIC control's top-up, against a fake server."""

import base64

from realms.cli.commands import mundus_held
from realms.cli.commands.mundus_held import FLOOR, Network, principal_text


def raw(index: int) -> bytes:
    return (0x7FFFFFFFFFA00000 + index).to_bytes(8, "big") + b"\x01\x01"


class FakeServer:
    """A PocketIC instance with one application subnet whose canisters hold ``balances`` (index -> cycles; None
    leaves a gap, as a deleted canister does), and a system subnet the top-up must leave alone."""

    def __init__(self, balances: dict):
        self.balances = {raw(i): c for i, c in balances.items() if c is not None}
        self.system = (7).to_bytes(8, "big") + b"\x01\x01"
        self.balances[self.system] = 5

    def __call__(self, method: str, endpoint: str, body=None):
        if endpoint == "_/topology":
            def span(first: bytes, last: bytes) -> dict:
                return {"start": {"canister_id": base64.b64encode(first).decode()},
                        "end": {"canister_id": base64.b64encode(last).decode()}}

            return {"subnet_configs": {
                "app": {"subnet_kind": "Application", "canister_ranges": [span(raw(0), raw(0xFFFFF))]},
                "ii": {"subnet_kind": "II", "canister_ranges": [span(self.system, self.system)]}}}
        found = base64.b64decode(body["canister_id"])
        if found not in self.balances:
            raise RuntimeError("PocketIC answered 400 to read/get_cycles: no such canister")
        if endpoint == "read/get_cycles":
            return {"cycles": self.balances[found]}
        assert endpoint == "update/add_cycles"
        self.balances[found] += body["amount"]
        return {"cycles": self.balances[found]}


def network(server: FakeServer) -> Network:
    net = Network.__new__(Network)
    net._rest = server
    return net


def test_every_application_canister_low_on_cycles_is_topped_up_to_the_floor():
    nearly = FLOOR - FLOOR // 20  # a canister that has used a little since its last top-up is left alone
    server = FakeServer({0: 9 * 10**12, 1: FLOOR + 1, 2: 78 * 10**9, 3: None, 4: 0, 5: nearly})
    found = network(server).top_up()
    assert [added for _, _, added in found] == [FLOOR - 9 * 10**12, 0, FLOOR - 78 * 10**9, FLOOR, 0]
    assert [server.balances[raw(i)] for i in (0, 2, 4)] == [FLOOR] * 3
    assert (server.balances[raw(1)], server.balances[raw(5)]) == (FLOOR + 1, nearly)
    assert server.balances[server.system] == 5  # not on an application subnet


def test_the_search_stops_after_a_gap_of_missing_ids(monkeypatch):
    monkeypatch.setattr(mundus_held, "GAP", 3)
    server = FakeServer({0: 1, 1: None, 2: None, 3: 1, 4: None, 5: None, 6: None, 7: 1})
    assert network(server).canisters() == [raw(0), raw(3)]


def test_a_canister_id_is_written_as_the_ic_writes_it():
    assert principal_text(raw(0x25)) == "3wbuo-6d777-77775-aaasq-cai"  # València's capital, on the mundus
    assert principal_text(b"\x00" * 8 + b"\x01\x01") == "rwlgt-iiaaa-aaaaa-aaaaa-cai"  # the NNS registry
