"""A realm canister on PocketIC, for tests that need the real wasm.

The wasm is larger than one ingress message, so it is installed the way dfx does it:
gzipped, uploaded in chunks to a canister's chunk store, then installed from the chunks.
Realms of one PocketIC instance (tests/deployed/network.py) upload it once and install from the same store.
"""

import base64
import gzip
import hashlib
import json
import os
from pathlib import Path

from ic.candid import Types, decode, encode
from ic.principal import Principal
from pocket_ic import PocketIC
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

os.environ.setdefault("POCKET_IC_BIN", os.path.expanduser("~/.cache/dfinity/versions/0.31.0/pocket-ic"))

CHUNK = 1_000_000
UPGRADE_ATTEMPTS = 10
CONTROLLER = "kber3-kmjak-kngoi-c3iyv-ix4le-p27yv-3fzmi-vnnab-xtjtf-ndfta-wqe"
_HASH = Types.Record({"hash": Types.Vec(Types.Nat8)})
_UPLOAD = Types.Record({"canister_id": Types.Principal, "chunk": Types.Vec(Types.Nat8)})
_CANISTER = Types.Record({"canister_id": Types.Principal})
_CONTROLLERS = Types.Record({"canister_id": Types.Principal,
                             "settings": Types.Record({"controllers": Types.Opt(Types.Vec(Types.Principal))})})
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


def new_pocket_ic(subnet_config=None):
    """A PocketIC instance whose requests wait while the server is busy with a round. A round that takes long, such as
    the install of an 11 MB wasm on a loaded machine, makes it answer 409 to anything else, which the client takes as
    an error. Only a 409 is tried again, for up to ten minutes: a server that is gone, or a request it refuses, fails
    at once, and one that stays busy fails with a ``RetryError``. ``subnet_config`` is a ``pocket_ic.SubnetConfig``;
    the default is one application subnet, which holds no threshold keys."""
    pic = PocketIC(subnet_config)
    busy = Retry(total=None, connect=0, read=0, redirect=0, other=0, status=600, status_forcelist=(409,),
                 allowed_methods=None, backoff_factor=0.05, backoff_max=1, raise_on_status=True)
    pic.server.request_client.mount("http://", HTTPAdapter(max_retries=busy))
    return pic


def _management(pic, canister, method, typ, value):
    """A call to the management canister about ``canister``, from the sender of ``pic`` (the controller)."""
    effective = {"CanisterId": base64.b64encode(canister.bytes).decode()}
    return pic.update_call_with_effective_principal(None, effective, method, encode([{"type": typ, "value": value}]))


def _new_canister(pic):
    """An empty canister of the controller, with cycles for a lifetime of tests."""
    pic.set_sender(Principal.from_str(CONTROLLER))
    canister = pic.create_canister(settings={"controllers": [[Principal.from_str(CONTROLLER).bytes]],
                                             "compute_allocation": [], "memory_allocation": [],
                                             "freezing_threshold": []})
    pic.add_cycles(canister, 10**15)
    return canister


class Wasm:
    """A wasm gzipped and uploaded, in chunks, to the chunk store of a canister of its own on ``pic``. Any canister
    of that PocketIC installs it from there, so a test that needs several uploads it once."""

    def __init__(self, pic, path):
        self.holder = _new_canister(pic)
        data = gzip.compress(Path(path).read_bytes())
        self.digest = hashlib.sha256(data).digest()
        self.hashes = []
        for i in range(0, len(data), CHUNK):
            reply = _management(pic, self.holder, "upload_chunk", _UPLOAD,
                                {"canister_id": self.holder.bytes, "chunk": data[i:i + CHUNK]})
            self.hashes.append({"hash": decode(bytes(reply), _HASH)[0]["value"]["hash"]})


class Canister:
    def __init__(self, wasm_path, installer="", pic=None, wasm=None):
        """A canister running ``wasm_path`` on a PocketIC instance of its own, or on ``pic`` when given, installing
        the already uploaded ``wasm`` if there is one."""
        self.pic = pic or new_pocket_ic()
        self.id = _new_canister(self.pic)
        self.wasm = wasm or Wasm(self.pic, wasm_path)
        self._install({"install": None}, encode([{"type": Types.Text, "value": installer}]))

    def upgrade(self, wasm_path=None):
        """Upgrade the canister to ``wasm_path`` (the wasm it has by default), as its controller: its stable memory stays,
        its timers are gone until post_upgrade.

        The replica refuses an install soon after another of an 11 MB wasm (``CanisterInstallCodeRateLimited``), and
        what lets it through is rounds run, which an upgrade right after the install has not had: run some and ask
        again. The clock stays where it is, so that no timer fires and no outcall times out meanwhile."""
        if wasm_path:
            self.wasm = Wasm(self.pic, wasm_path)
        for attempt in range(UPGRADE_ATTEMPTS):
            try:
                return self._install({"upgrade": []}, encode([]))
            except ValueError as refused:
                if "CanisterInstallCodeRateLimited" not in str(refused) or attempt == UPGRADE_ATTEMPTS - 1:
                    raise
                for _ in range(10):
                    self.pic.tick()

    def stop(self):
        """Stop the canister, as its controller: it rejects the calls it is sent until ``start``."""
        self._lifecycle("stop_canister")

    def start(self):
        self._lifecycle("start_canister")

    def add_controller(self, principal):
        """Make ``principal`` a controller of the canister besides the test's, as Casals makes a capital a controller of
        the quarters it builds for it."""
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        controllers = [Principal.from_str(CONTROLLER).bytes, Principal.from_str(principal).bytes]
        _management(self.pic, self.id, "update_settings", _CONTROLLERS,
                    {"canister_id": self.id.bytes, "settings": {"controllers": [controllers]}})

    def _lifecycle(self, method):
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        _management(self.pic, self.id, method, _CANISTER, {"canister_id": self.id.bytes})

    def wait(self, seconds):
        """Let ``seconds`` pass on the replica's clock and run what falls due, timers and the calls they make."""
        self.pic.advance_time(seconds * 10**9)
        for _ in range(10):
            self.pic.tick()

    def now(self):
        """The replica's clock, in seconds."""
        return self.pic.get_time()["nanos_since_epoch"] // 10**9

    def _install(self, mode, arg):
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        _management(self.pic, self.id, "install_chunked_code", _INSTALL, {
            "mode": mode, "target_canister": self.id.bytes, "store_canister": [self.wasm.holder.bytes],
            "chunk_hashes_list": self.wasm.hashes, "wasm_module_hash": self.wasm.digest,
            "arg": arg, "sender_canister_version": [],
        })

    def call(self, name, args=None, sender=None, query=False):
        """Run operation ``name`` through ``read`` (query) or ``run``; returns the decoded JSON reply."""
        self.pic.set_sender(Principal.from_str(sender) if sender else Principal.anonymous())
        payload = encode([{"type": Types.Text, "value": name},
                          {"type": Types.Text, "value": json.dumps(args or {})}])
        call = self.pic.query_call if query else self.pic.update_call
        return json.loads(decode(bytes(call(self.id, "read" if query else "run", payload)))[0]["value"])

    def submit(self, name, args=None, sender=None):
        """Send the update call ``name`` through ``run`` without waiting for its reply, and return its message id: ``finish``
        collects the replies. Calls sent together run in the same round, in order, so a test can send a second call
        while the first waits on another canister."""
        self.pic.set_sender(Principal.from_str(sender) if sender else Principal.anonymous())
        payload = encode([{"type": Types.Text, "value": name}, {"type": Types.Text, "value": json.dumps(args or {})}])
        body = {"sender": base64.b64encode(self.pic.sender.bytes).decode(), "effective_principal": "None",
                "canister_id": base64.b64encode(self.id.bytes).decode(), "method": "run",
                "payload": base64.b64encode(payload).decode()}
        return self.pic._get_ok(self.pic._instance_post("update/submit_ingress_message", body))

    def finish(self, *messages):
        """The decoded JSON replies of the calls ``submit`` sent, once every one of them has completed."""
        for _ in range(100):
            self.pic.tick()
            statuses = [self.pic._ingress_status(m) for m in messages]
            if all(statuses):
                return [json.loads(decode(bytes(self.pic._get_ok_data(s)))[0]["value"]) for s in statuses]
        raise ValueError("the calls did not complete within 100 rounds")

    def shell(self, code):
        """Run Python in the canister as its controller (basilisk's ``__shell__``); returns what it printed."""
        self.pic.set_sender(Principal.from_str(CONTROLLER))
        reply = self.pic.update_call(self.id, "__shell__", encode([{"type": Types.Text, "value": code}]))
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
