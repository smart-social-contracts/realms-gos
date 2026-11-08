"""The HTTPS outcalls of canisters on a PocketIC instance, answered from a table of URLs.

PocketIC holds each outcall until the test answers it, and the Python package has no wrapper for that, so this talks
to the server's REST API (``read/get_canister_http`` and ``update/mock_canister_http``) itself.
"""

import base64
from collections import namedtuple

Reply = namedtuple("Reply", "status body")
Reject = namedtuple("Reject", "code message")
NOT_FOUND = Reply(404, b"not found")
MAX_RESPONSE_BYTES = 2_000_000  # what a request that sets no max_response_bytes may get


class Web:
    """The internet as seen by the canisters of ``pic``: ``serve`` a URL, and ``answer`` what they ask for.

    A URL nobody serves is a 404, as on a real server. A server that changes its bytes is ``serve`` called again for
    the same URL, and one that is down or silent is ``unreachable`` or ``no_answer``. A body larger than the
    request's ``max_response_bytes`` (2 MB if it sets none) fails the outcall, as on mainnet.

    Outcalls are answered between rounds, so a canister must make them from a timer or a heartbeat, never inside an
    update call a test is waiting for: that call only returns once the answer is in, and nothing answers while the
    test waits (``pic.update_call`` gives up after 100 rounds).

    A request's ``transform`` function is run by the subnet before the replicas compare the answers. It is not tested
    here: the install task adds ``http_transform`` and its test.
    """

    def __init__(self, pic):
        self.pic = pic
        self.responses = {}  # url -> a Reply or a Reject to send, or None to leave the request pending
        self.asked = []  # (method, url) of each request seen, in order
        self._seen = set()

    def serve(self, url, body, status=200):
        self.responses[url] = Reply(status, body)

    def unreachable(self, url, message="connection refused"):
        """The outcall fails: the canister is told the request could not be made (a transient reject)."""
        self.responses[url] = Reject(2, message)

    def no_answer(self, url):
        """The outcall is never answered. It stays pending, and after 60 seconds of instance time PocketIC rejects it
        with code 2 (SysTransient), as mainnet does for a server that does not answer."""
        self.responses[url] = None

    def pending(self):
        """The requests the canisters have made and nobody has answered."""
        return self._rest("get", "read/get_canister_http")

    def answer(self):
        """Answer every pending request the table has an answer for. The canister gets the reply in the next round."""
        for request in self.pending():
            key = (request["subnet_id"]["subnet_id"], request["request_id"])
            if key not in self._seen:
                self._seen.add(key)
                self.asked.append((request["http_method"], request["url"]))
            answer = self.responses.get(request["url"], NOT_FOUND)
            if answer is not None:
                self._mock(request, self._within_limit(request, answer))

    def _within_limit(self, request, answer):
        limit = request["max_response_bytes"] or MAX_RESPONSE_BYTES
        if isinstance(answer, Reply) and len(answer.body) > limit:
            return Reject(1, f"Http body exceeds size limit of {limit} bytes.")  # SysFatal: asking again fails the same
        return answer

    def _mock(self, request, answer):
        if isinstance(answer, Reply):
            response = {"CanisterHttpReply": {
                "status": answer.status, "headers": [], "body": base64.b64encode(answer.body).decode()}}
        else:
            response = {"CanisterHttpReject": {"reject_code": answer.code, "message": answer.message}}
        self._rest("post", "update/mock_canister_http", {
            "subnet_id": request["subnet_id"], "request_id": request["request_id"],
            "response": response, "additional_responses": []})

    def _rest(self, method, endpoint, body=None):
        server = self.pic.server
        url = f"{server.url}/instances/{self.pic.instance_id}/{endpoint}"
        response = getattr(server.request_client, method)(url, **({"json": body} if body is not None else {}))
        if not response.ok:
            raise ConnectionError(f"PocketIC server answered {response.status_code} to {endpoint}: {response.text}")
        return response.json()
