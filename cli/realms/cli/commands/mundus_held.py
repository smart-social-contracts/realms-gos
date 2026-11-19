"""The local network of a mundus, held still while a step needs what only a test harness has.

A mundus runs on icp-cli's managed local network, which is a PocketIC instance in live mode: it makes rounds on its
own, on the wall clock, and makes the canisters' HTTPS outcalls to the internet. A realm installs legislation from a
change list it fetches over HTTPS, and a change list names the codex it replaces, so it is written for the realm's
state of the moment and lives nowhere on the internet. So ``held`` stops the instance, the mundus makes the rounds
itself and answers the realm's outcalls (from what it ``serve``s, else from the internet), and then lets the instance
go live again, also on an error.

The clock is never moved ahead: every signed call (a browser's, the swarm's) carries an expiry by the wall clock,
and a network ahead of it refuses them all ("Invalid request expiry"). While held, the network's time stands still;
it is set level with the wall clock again before it goes live.

While held, nothing else runs: a call through the gateway waits. The mundus calls the realms through PocketIC's own
API, which takes any sender, as the realms' founders.
"""

import base64
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import requests

NANOS = 10**9
ROUNDS = 500  # rounds an update call may take before the mundus gives up on it
PATIENCE = 900  # seconds of rounds a step may take before the mundus gives up on it


def descriptor_path(casals_home: Path) -> Path:
    """Where icp-cli describes the network Casals started for this mundus: its PocketIC server and instance."""
    return casals_home / ".replica" / ".icp" / "cache" / "networks" / "local" / "descriptor.json"


class Network:
    """The PocketIC instance under a mundus, through its server's REST API."""

    def __init__(self, casals_home: Path):
        found = json.loads(descriptor_path(casals_home).read_text())
        self.server = f"http://127.0.0.1:{found['pocketic-config-port']}"
        self.instance = f"{self.server}/instances/{found['pocketic-instance-id']}"
        self.session = requests.Session()
        self.served: dict = {}  # url -> bytes the mundus answers while the network is held
        self.asked: list = []  # the urls the canisters fetched while held, in order

    # -- the server

    def _rest(self, method: str, endpoint: str, body=None):
        response = self.session.request(method, f"{self.instance}/{endpoint}", json=body, timeout=600)
        # A live instance in the middle of its own round answers 409: ask again.
        for _ in range(200):
            if response.status_code != 409:
                break
            time.sleep(0.05)
            response = self.session.request(method, f"{self.instance}/{endpoint}", json=body, timeout=600)
        # A busy instance answers where its result will be (202, or 200 on a read): ask there until it is.
        while response.ok and self._busy(response):
            busy = response.json()
            time.sleep(0.05)
            response = self.session.get(f"{self.server}/read_graph/{busy['state_label']}/{busy['op_id']}",
                                        timeout=600)
        if not response.ok:
            raise RuntimeError(f"PocketIC answered {response.status_code} to {endpoint}: {response.text[:300]}")
        return response.json() if response.content else None

    @staticmethod
    def _busy(response) -> bool:
        if response.status_code == 202:
            return True
        try:
            found = response.json()
        except ValueError:
            return False
        return isinstance(found, dict) and set(found) == {"state_label", "op_id"}

    def now(self) -> int:
        """The network's time, in nanoseconds."""
        return self._rest("get", "read/get_time")["nanos_since_epoch"]

    def tick(self) -> None:
        self._rest("post", "update/tick", {})
        self.answer()

    @contextmanager
    def held(self):
        """Stop the instance's own rounds; the caller makes them. On leaving, error or not, the clock is set level with
        the wall clock (it stood still meanwhile) and the instance goes live again."""
        self._rest("post", "stop_progress")
        try:
            yield self
        finally:
            wall = time.time_ns()
            if self.now() < wall:
                self._rest("post", "update/set_time", {"nanos_since_epoch": wall})
            self._rest("post", "auto_progress", {"artificial_delay_ms": None})

    def run_until(self, reached, limit: float = PATIENCE) -> None:
        """Make rounds until ``reached()``: timers due now, the calls they make, the outcalls those make."""
        started = time.time()
        while not reached():
            if time.time() - started > limit:
                raise RuntimeError(f"not reached in {limit:.0f} s of rounds")
            for _ in range(5):
                self.tick()

    # -- outcalls

    def serve(self, url: str, body: bytes) -> None:
        self.served[url] = body

    def answer(self) -> None:
        """Answer every outcall the canisters are waiting on: what is served, else the internet's answer."""
        for request in self._rest("get", "read/get_canister_http") or []:
            url = request["url"]
            self.asked.append(url)
            if url in self.served:
                reply = {"CanisterHttpReply": {"status": 200, "headers": [],
                                               "body": base64.b64encode(self.served[url]).decode()}}
            else:
                reply = self._fetched(url, request)
            self._rest("post", "update/mock_canister_http", {
                "subnet_id": request["subnet_id"], "request_id": request["request_id"], "response": reply,
                "additional_responses": []})

    def _fetched(self, url: str, request: dict) -> dict:
        try:
            got = self.session.get(url, timeout=60)
        except requests.RequestException as exc:
            return {"CanisterHttpReject": {"reject_code": 2, "message": f"{type(exc).__name__}: {exc}"[:200]}}
        limit = request.get("max_response_bytes") or 2_000_000
        if len(got.content) > limit:
            return {"CanisterHttpReject": {"reject_code": 1, "message": f"Http body exceeds size limit of {limit} bytes."}}
        return {"CanisterHttpReply": {"status": got.status_code, "headers": [],
                                      "body": base64.b64encode(got.content).decode()}}

    # -- calls

    def call(self, canister: str, sender: str, op: str, args: Optional[dict] = None, read: bool = False) -> dict:
        """Run the realm operation ``op`` on ``canister`` as ``sender`` and return its JSON reply. Only while held."""
        from ic.candid import Types, decode, encode
        from ic.principal import Principal

        body = {
            "sender": base64.b64encode(Principal.from_str(sender).bytes).decode(),
            "effective_principal": "None",
            "canister_id": base64.b64encode(Principal.from_str(canister).bytes).decode(),
            "method": "read" if read else "run",
            "payload": base64.b64encode(encode([{"type": Types.Text, "value": op},
                                                {"type": Types.Text, "value": json.dumps(args or {})}])).decode(),
        }
        if read:
            result = self._rest("post", "read/query", body)
        else:
            message = self._ok(self._rest("post", "update/submit_ingress_message", body), op)
            for _ in range(ROUNDS):
                self.tick()
                result = self._rest("post", "read/ingress_status", {"raw_message_id": message, "raw_caller": None})
                if result:
                    break
            else:
                raise RuntimeError(f"{op} on {canister} did not complete in {ROUNDS} rounds")
        return json.loads(decode(base64.b64decode(self._ok(result, op)))[0]["value"])

    @staticmethod
    def _ok(result: dict, op: str):
        if "Ok" in result:
            return result["Ok"]
        err = result.get("Err") or result
        raise RuntimeError(f"{op} rejected: {err.get('reject_message', err) if isinstance(err, dict) else err}")
