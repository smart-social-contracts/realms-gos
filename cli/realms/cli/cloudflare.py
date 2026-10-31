"""Apply IC custom-domain DNS records to a Cloudflare zone.

``dns.py`` renders the three records an IC custom domain needs; this module puts
them in the zone. Only the ``_canister-id`` TXT record carries a canister ID, so
that is the only one that changes when a fleet is re-minted — the two CNAMEs are
derived from the domain name and stay put. Upserting all three anyway keeps a
half-configured zone from needing manual repair.

The token is read from the environment, never from a descriptor or a file in the
repo, and is never logged. It needs two permissions on the zone: ``Zone:Read``
(to resolve the zone id) and ``DNS:Edit``.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

import requests

from realms.cli.dns import DnsRecord, RecordOutcome

CF_API = "https://api.cloudflare.com/client/v4"
DEFAULT_TOKEN_ENV = "CLOUDFLARE_API_TOKEN"

# Cloudflare's floor on paid-plan-free zones. Low so a remap propagates quickly.
DEFAULT_TTL = 60

# Records must resolve to the IC gateway, so the orange cloud has to be off: a
# proxied record makes Cloudflare answer with its own edge, which both hides the
# gateway and breaks the ACME challenge the IC uses to issue the certificate.
PROXIED = False


class CloudflareError(RuntimeError):
    pass


def token_from_env(env_var: str = DEFAULT_TOKEN_ENV) -> str | None:
    token = (os.environ.get(env_var) or "").strip()
    return token or None


def zone_for_domain(domain: str) -> str:
    """Best guess at the registrable zone that holds ``domain``.

    Right for realmsgos.org and gos.earth. Wrong for multi-label suffixes such
    as .co.uk, so a descriptor may name the zone explicitly instead.
    """
    labels = domain.rstrip(".").lower().split(".")
    if len(labels) < 2:
        raise CloudflareError(f"{domain!r} is not a fully qualified domain")
    return ".".join(labels[-2:])


def cloudflare_record_type(record: DnsRecord) -> str:
    """Map a rendered record onto a Cloudflare type.

    ``dns.py`` labels the host record ``CNAME/ALIAS`` because some providers
    need ALIAS at an apex. Cloudflare does not: it flattens CNAMEs at the apex
    on its own.
    """
    return "CNAME" if record.record_type.upper().startswith("CNAME") else record.record_type.upper()


class CloudflareDns:
    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        ttl: int = DEFAULT_TTL,
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise CloudflareError("empty Cloudflare API token")
        self._token = token
        self._http = session or requests.Session()
        self._ttl = ttl
        self._timeout = timeout
        self._zone_ids: dict[str, str] = {}

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(
            method,
            f"{CF_API}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
            **kwargs,
        )
        try:
            payload = response.json()
        except ValueError:
            raise CloudflareError(
                f"{method} {path} returned HTTP {response.status_code} with a non-JSON body"
            ) from None

        if response.status_code == 403:
            raise CloudflareError(
                f"{method} {path} forbidden (HTTP 403). The token needs Zone:Read and "
                f"DNS:Edit on this zone: {_errors_text(payload)}"
            )
        if not payload.get("success"):
            raise CloudflareError(
                f"{method} {path} failed (HTTP {response.status_code}): {_errors_text(payload)}"
            )
        return payload.get("result")

    def zone_id(self, zone: str) -> str:
        cached = self._zone_ids.get(zone)
        if cached:
            return cached
        result = self._call("GET", "/zones", params={"name": zone})
        if not result:
            raise CloudflareError(
                f"no Cloudflare zone named {zone!r} is visible to this token"
            )
        zone_id = str(result[0].get("id") or "")
        if not zone_id:
            raise CloudflareError(f"Cloudflare returned no id for zone {zone!r}")
        self._zone_ids[zone] = zone_id
        return zone_id

    def _matching(self, zone_id: str, record_type: str, name: str) -> list[dict[str, Any]]:
        return list(
            self._call(
                "GET",
                f"/zones/{zone_id}/dns_records",
                params={"type": record_type, "name": name},
            )
            or []
        )

    def _delete(self, zone_id: str, record_id: str) -> None:
        self._call("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")

    def upsert(self, zone_id: str, record: DnsRecord) -> RecordOutcome:
        record_type = cloudflare_record_type(record)
        name = record.host.rstrip(".").lower()
        body = {
            "type": record_type,
            "name": name,
            "content": record.value,
            "ttl": self._ttl,
            "proxied": PROXIED,
        }

        matches = self._matching(zone_id, record_type, name)
        if not matches and record_type == "CNAME":
            # An apex may already point at the IC gateway with A/AAAA records —
            # the gateway's other supported shape. Cloudflare refuses a CNAME
            # beside them (error 81053), and replacing them is not this tool's
            # call, so keep them and move on to the records that do change.
            addresses = self._matching(zone_id, "A", name) + self._matching(
                zone_id, "AAAA", name
            )
            if addresses:
                values = ", ".join(str(a.get("content") or "") for a in addresses)
                return RecordOutcome(
                    record, "unchanged", f"kept existing A/AAAA ({values})"
                )
        if not matches:
            self._call("POST", f"/zones/{zone_id}/dns_records", json=body)
            return RecordOutcome(record, "created")

        # Each host we manage holds exactly one value, so extras are duplicates
        # to clear rather than a set to preserve. The IC rejects a domain whose
        # _canister-id has more than one TXT record, and a stale duplicate is
        # invisible until registration fails.
        existing, *duplicates = matches
        for duplicate in duplicates:
            self._delete(zone_id, str(duplicate["id"]))

        # Compare only what we manage. Cloudflare hands TXT content back
        # unquoted, so this is a plain string comparison.
        same = (
            str(existing.get("content") or "") == record.value
            and bool(existing.get("proxied")) is PROXIED
            and int(existing.get("ttl") or 0) == self._ttl
        )
        dropped = (
            f"; dropped {len(duplicates)} duplicate"
            f"{'s' if len(duplicates) > 1 else ''}"
            if duplicates
            else ""
        )
        if same:
            if not duplicates:
                return RecordOutcome(record, "unchanged")
            return RecordOutcome(record, "updated", dropped.lstrip("; "))

        was = str(existing.get("content") or "")
        self._call("PATCH", f"/zones/{zone_id}/dns_records/{existing['id']}", json=body)
        detail = f"{was} -> {record.value}" if was != record.value else "settings realigned"
        return RecordOutcome(record, "updated", detail + dropped)


def _errors_text(payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    if not errors:
        return "no error detail returned"
    parts = []
    for err in errors:
        if isinstance(err, dict):
            parts.append(f"{err.get('code', '?')}: {err.get('message', err)}")
        else:
            parts.append(str(err))
    return "; ".join(parts)


def apply_records(
    records: Iterable[DnsRecord],
    *,
    token: str,
    zone: str = "",
    domain: str = "",
    ttl: int = DEFAULT_TTL,
    session: requests.Session | None = None,
) -> list[RecordOutcome]:
    """Upsert every rendered record into the zone. Idempotent."""
    records = list(records)
    if not records:
        return []
    if not zone:
        zone = zone_for_domain(domain or records[0].host)

    client = CloudflareDns(token, session=session, ttl=ttl)
    zone_id = client.zone_id(zone)
    return [client.upsert(zone_id, record) for record in records]
