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
_UPGRADE_FLAGS = Types.Record({
    "skip_pre_upgrade": Types.Opt(Types.Bool),
    "wasm_memory_persistence": Types.Opt(Types.Variant({"keep": Types.Null, "replace": Types.Null})),
})
_INSTALL = Types.Record({
    "mode": Types.Variant({"install": Types.Null, "reinstall": Types.Null, "upgrade": Types.Opt(_UPGRADE_FLAGS)}),
    "target_canister": Types.Principal,
    "store_canister": Types.Opt(Types.Principal),
    "chunk_hashes_list": Types.Vec(_HASH),
    "wasm_module_hash": Types.Vec(Types.Nat8),
    "arg": Types.Vec(Types.Nat8),
    "sender_canister_version": Types.Opt(Types.Nat64),
})


class Canister:
    def __init__(self, wasm_path, installer=""):
        self.pic = PocketIC()
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        self.id = self.pic.create_canister(settings={"controllers": [[Principal.from_str(CONTROLLER).bytes]],
                                                     "compute_allocation": [], "memory_allocation": [],
                                                     "freezing_threshold": []})
        self.pic.add_cycles(self.id, 10**15)
        self.wasm = gzip.compress(open(wasm_path, "rb").read())
        self._install({"install": None}, encode([{"type": Types.Text, "value": installer}]))

    def upgrade(self):
        """Upgrade the canister to the same wasm: its stable memory stays, its timers are gone until post_upgrade."""
        self._install({"upgrade": []}, encode([]))

    def wait(self, seconds):
        """Let ``seconds`` pass on the replica's clock and run what falls due, timers and the calls they make."""
        self.pic.advance_time(seconds * 10**9)
        for _ in range(10):
            self.pic.tick()

    def now(self):
        """The replica's clock, in seconds."""
        return self.pic.get_time()["nanos_since_epoch"] // 10**9

    def _management(self, method, typ, value):
        effective = {"CanisterId": base64.b64encode(self.id.bytes).decode()}
        return self.pic.update_call_with_effective_principal(
            None, effective, method, encode([{"type": typ, "value": value}]))

    def _install(self, mode, arg):
        hashes = []
        for i in range(0, len(self.wasm), CHUNK):
            reply = self._management("upload_chunk", _UPLOAD,
                                     {"canister_id": self.id.bytes, "chunk": self.wasm[i:i + CHUNK]})
            hashes.append({"hash": decode(bytes(reply), _HASH)[0]["value"]["hash"]})
        self._management("install_chunked_code", _INSTALL, {
            "mode": mode, "target_canister": self.id.bytes, "store_canister": [],
            "chunk_hashes_list": hashes, "wasm_module_hash": hashlib.sha256(self.wasm).digest(),
            "arg": arg, "sender_canister_version": [],
        })

    def call(self, name, args=None, sender=None, query=False):
        """Run operation ``name`` through ``read`` (query) or ``run``; returns the decoded JSON reply."""
        self.pic.set_sender(Principal.from_str(sender) if sender else Principal.anonymous())
        payload = encode([{"type": Types.Text, "value": name},
                          {"type": Types.Text, "value": json.dumps(args or {})}])
        call = self.pic.query_call if query else self.pic.update_call
        return json.loads(decode(bytes(call(self.id, "read" if query else "run", payload)))[0]["value"])

    def shell(self, code):
        """Run Python in the canister as its controller (basilisk's ``__shell__``); returns what it printed."""
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        reply = self.pic.update_call(self.id, "__shell__", encode([{"type": Types.Text, "value": code}]))
        return decode(bytes(reply))[0]["value"]

    def raw(self, method, payload=b"DIDL\x00\x00", query=True):
        call = self.pic.query_call if query else self.pic.update_call
        return decode(bytes(call(self.id, method, payload)))
