"""GET /version — build provenance over the IC HTTP interface.

Contract: gos-as-a-service#39 — every platform canister serves build
provenance at /version for the "estado de los entornos" command.
Values are stamped at build/release time (release.yml / deploy.py sed
on the placeholders below). A field still holding its placeholder
(local/dev builds) is omitted honestly — never invented at query time.
"""

import json

_CANISTER_NAME = "realm_backend"
_SHA_STAMP = "SHA_PLACEHOLDER"
_BUILT_AT_STAMP = "BUILT_AT_ISO_PLACEHOLDER"
_VERSION_STAMP = "VERSION_PLACEHOLDER"


def get_version_payload() -> dict:
    payload = {"canister": _CANISTER_NAME}
    if "PLACEHOLDER" not in _SHA_STAMP:
        payload["sha"] = _SHA_STAMP
    if "PLACEHOLDER" not in _BUILT_AT_STAMP:
        payload["built_at"] = _BUILT_AT_STAMP
    if "PLACEHOLDER" not in _VERSION_STAMP:
        payload["version"] = _VERSION_STAMP
    return payload


def _http_response(status: int, body: bytes, content_type: str, extra_headers=None) -> dict:
    headers = [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return {
        "status_code": status,
        "headers": headers,
        "body": body,
        "streaming_strategy": None,
        "upgrade": None,
    }


def is_version_path(url: str) -> bool:
    path = (url or "").split("?")[0].split("#")[0].strip()
    return path.rstrip("/") == "/version" or path.strip("/") == "version"


def version_http_response(req: dict) -> dict:
    """Route an IC http_request_update call.

    GET /version → contract JSON. OPTIONS → 204. Anything else → 404
    ``{"error": "not found"}``. Existing query routes (/status, /extensions)
    stay on ``http_request`` and are not re-served here.
    """
    method = (req.get("method") or "GET").upper()
    if method == "OPTIONS":
        return _http_response(204, b"", "text/plain")
    if is_version_path(req.get("url") or ""):
        body = json.dumps(get_version_payload()).encode("utf-8")
        return _http_response(
            200,
            body,
            "application/json",
            [("Cache-Control", "no-cache, must-revalidate")],
        )
    body = json.dumps({"error": "not found"}).encode("utf-8")
    return _http_response(404, body, "application/json")


def http_request_upgrade_signal() -> dict:
    """Query-side signal: gateway must call http_request_update."""
    return {
        "status_code": 200,
        "headers": [],
        "body": b"",
        "streaming_strategy": None,
        "upgrade": True,
    }
