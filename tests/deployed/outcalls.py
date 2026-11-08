"""The HTTPS outcalls of canisters on a PocketIC instance, answered from a table of URLs.

PocketIC holds each outcall until the test answers it, and the Python package has no wrapper for that, so this talks
to the server's REST API (``read/get_canister_http`` and ``update/mock_canister_http``) itself.
"""

import base64


def reply(status, body):
    return {"CanisterHttpReply": {"status": status, "headers": [], "body": base64.b64encode(body).decode()}}


NOT_FOUND = reply(404, b"not found")


class Web:
    """The internet as seen by the canisters of ``pic``: ``serve`` a URL, and ``answer`` what they ask for.

    A URL nobody serves is a 404, as on a real server. A server that changes its bytes is ``serve`` called again for
    the same URL, and one that is down or silent is ``unreachable`` or ``no_answer``.
    """

    def __init__(self, pic):
        self.pic = pic
        self.responses = {}  # url -> the CanisterHttpResponse to send, or None to leave the request pending
        self.asked = []  # (method, url) of each request seen, in order
        self._seen = set()

    def serve(self, url, body, status=200):
        self.responses[url] = reply(status, body)

    def unreachable(self, url, message="connection refused"):
        """The outcall fails: the canister is told the request could not be made (a transient reject)."""
        self.responses[url] = {"CanisterHttpReject": {"reject_code": 2, "message": message}}

    def no_answer(self, url):
        """The outcall is never answered: it stays pending."""
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
            response = self.responses.get(request["url"], NOT_FOUND)
            if response is not None:
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
