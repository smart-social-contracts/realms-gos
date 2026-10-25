"""A realm canister on PocketIC, for tests that need the real wasm.

The wasm is larger than one ingress message, so it is installed the way dfx does it:
gzipped, uploaded in chunks to the canister itself, then installed from the chunks.
"""

import base64
import gzip
import hashlib
import json
import os

from ic.candid import Types, decode, encode
from ic.principal import Principal
from pocket_ic import PocketIC

os.environ.setdefault("POCKET_IC_BIN", os.path.expanduser("~/.cache/dfinity/versions/0.31.0/pocket-ic"))

CHUNK = 1_000_000
CONTROLLER = "kber3-kmjak-kngoi-c3iyv-ix4le-p27yv-3fzmi-vnnab-xtjtf-ndfta-wqe"
_HASH = Types.Record({"hash": Types.Vec(Types.Nat8)})
_UPLOAD = Types.Record({"canister_id": Types.Principal, "chunk": Types.Vec(Types.Nat8)})
_INSTALL = Types.Record({
    "mode": Types.Variant({"install": Types.Null, "reinstall": Types.Null}),
    "target_canister": Types.Principal,
    "store_canister": Types.Opt(Types.Principal),
    "chunk_hashes_list": Types.Vec(_HASH),
    "wasm_module_hash": Types.Vec(Types.Nat8),
    "arg": Types.Vec(Types.Nat8),
    "sender_canister_version": Types.Opt(Types.Nat64),
})


class Canister:
    def __init__(self, wasm_path):
        self.pic = PocketIC()
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        self.id = self.pic.create_canister(settings={"controllers": [[Principal.from_str(CONTROLLER).bytes]],
                                                     "compute_allocation": [], "memory_allocation": [],
                                                     "freezing_threshold": []})
        self.pic.add_cycles(self.id, 10**15)
        self._install(gzip.compress(open(wasm_path, "rb").read()))

    def _management(self, method, typ, value):
        effective = {"CanisterId": base64.b64encode(self.id.bytes).decode()}
        return self.pic.update_call_with_effective_principal(
            None, effective, method, encode([{"type": typ, "value": value}]))

    def _install(self, wasm):
        hashes = []
        for i in range(0, len(wasm), CHUNK):
            reply = self._management("upload_chunk", _UPLOAD,
                                     {"canister_id": self.id.bytes, "chunk": wasm[i:i + CHUNK]})
            hashes.append({"hash": decode(bytes(reply), _HASH)[0]["value"]["hash"]})
        self._management("install_chunked_code", _INSTALL, {
            "mode": {"install": None}, "target_canister": self.id.bytes, "store_canister": [],
            "chunk_hashes_list": hashes, "wasm_module_hash": hashlib.sha256(wasm).digest(),
            "arg": encode([]), "sender_canister_version": [],
        })

    def call(self, method, arg="{}", sender=None, query=False):
        """Call a ``method(text) -> text`` endpoint; returns the decoded JSON."""
        self.pic.set_sender(Principal.from_str(sender) if sender else Principal.anonymous())
        payload = encode([{"type": Types.Text, "value": arg if isinstance(arg, str) else json.dumps(arg)}])
        call = self.pic.query_call if query else self.pic.update_call
        out = decode(bytes(call(self.id, method, payload)))[0]["value"]
        return json.loads(out) if isinstance(out, str) and out[:1] in "{[" else out

    def raw(self, method, payload=b"DIDL\x00\x00", query=True):
        call = self.pic.query_call if query else self.pic.update_call
        return decode(bytes(call(self.id, method, payload)))
