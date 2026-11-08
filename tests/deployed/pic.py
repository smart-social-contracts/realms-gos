"""A realm canister on PocketIC, for tests that need the real wasm.

The wasm is larger than one ingress message, so it is installed the way dfx does it:
gzipped, uploaded in chunks to the canister itself, then installed from the chunks.
"""

import base64
import gzip
import hashlib
import json
import os
import time
from pathlib import Path

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


# ggg.did's GggError, to decode the reply of a GGG method.
GGG_ERROR = Types.Variant({
    "NotFound": Types.Null, "Unauthenticated": Types.Null,
    "Forbidden": Types.Record({"permission": Types.Opt(Types.Text), "message": Types.Text}),
    "InvalidArgument": Types.Text, "Conflict": Types.Text, "Unavailable": Types.Text,
    "GenericError": Types.Record({"error_code": Types.Nat, "message": Types.Text})})


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

    def upgrade(self, wasm_path=None):
        """Upgrade the canister to ``wasm_path`` (the wasm it has by default), as its controller: its stable memory stays,
        its timers are gone until post_upgrade."""
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        if wasm_path:
            self.wasm = gzip.compress(Path(wasm_path).read_bytes())
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
        return self._update(None, effective, method, encode([{"type": typ, "value": value}]))

    def _update(self, canister_id, effective_principal, method, payload):
        """An update call that waits for its reply however long its round takes. The client's own loop reads the
        message's status right after a tick, and while a heavy round (installing this wasm, a codex loading) is still
        running the server answers 409, busy, which the client turns into an error: here a busy answer is waited
        out. The body and the status reads are the client's own (pocket_ic.PocketIC.update_call_with_effective_principal)."""
        pic = self.pic
        canister = canister_id if canister_id else Principal.management_canister()
        body = {"sender": base64.b64encode(pic.sender.bytes).decode(),
                "effective_principal": effective_principal if effective_principal else "None",
                "canister_id": base64.b64encode(canister.bytes).decode(), "method": method,
                "payload": base64.b64encode(payload).decode()}
        msg_id = pic._get_ok(pic._instance_post("update/submit_ingress_message", body))
        for _ in range(200):
            try:
                pic.tick()
                result = pic._ingress_status(msg_id)
            except (ConnectionError, KeyError):  # the server is busy with the round: ask again
                time.sleep(0.5)
                continue
            if result:
                return pic._get_ok_data(result)
        raise ValueError(f"PocketIC did not complete {method} within 200 rounds")

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
        reply = self.pic.query_call(self.id, "read", payload) if query else self._update(self.id, None, "run", payload)
        return json.loads(decode(bytes(reply))[0]["value"])

    def shell(self, code):
        """Run Python in the canister as its controller (basilisk's ``__shell__``); returns what it printed."""
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        reply = self._update(self.id, None, "__shell__", encode([{"type": Types.Text, "value": code}]))
        return decode(bytes(reply))[0]["value"]

    def ggg(self, method, arg_type, arg, ok_type, sender, query=False):
        """A GGG method called with Candid, as a GGG caller does: ``arg`` of ``arg_type``, replied as
        ``variant { Ok : ok_type; Err : GggError }``. Returns the decoded ``{"Ok": ...}`` or ``{"Err": ...}``."""
        self.pic.set_sender(Principal.from_str(sender))
        call = self.pic.query_call if query else self.pic.update_call
        reply = call(self.id, method, encode([{"type": arg_type, "value": arg}]))
        return decode(bytes(reply), [Types.Variant({"Ok": ok_type, "Err": GGG_ERROR})])[0]["value"]

    def raw(self, method, payload=b"DIDL\x00\x00", query=True):
        call = self.pic.query_call if query else self.pic.update_call
        return decode(bytes(call(self.id, method, payload)))
