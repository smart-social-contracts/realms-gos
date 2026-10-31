"""Apply IC custom-domain DNS records to an Infomaniak zone.

``dns.py`` renders the three records an IC custom domain needs; this module puts
them in the zone through the Infomaniak API (``/2/zones/{zone}/records``). The
token is read from the environment, never from a descriptor or a file in the
repo, and is never logged. It needs the ``dns:read`` and ``dns:write`` scopes.

Two things differ from Cloudflare:

* An apex cannot be a CNAME, so it gets the A records of the ``icp1.io``
  gateway instead.
* This module never deletes. Infomaniak records carry no owner, so there is no
  telling which ones this tool made; a record that holds a stale value is
  updated in place, and any other record on the same name is left alone and
  reported.

With ``dry_run`` the zone is read but nothing is written.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

import requests

from realms.cli.dns import DnsRecord, RecordOutcome

INFOMANIAK_API = "https://api.infomaniak.com"
DEFAULT_TOKEN_ENV = "INFOMANIAK_API_TOKEN"

# The API refuses a TTL below 60 seconds.
DEFAULT_TTL = 60

# The addresses behind icp1.io, for a host that cannot be a CNAME.
APEX_ADDRESSES = ("195.5.171.242", "212.71.124.188")

APEX_SOURCE = "."

_AUTH_HINTS = {
    401: f"token rejected: check ${DEFAULT_TOKEN_ENV} is current",
    403: "the token needs the dns:read and dns:write scopes on this zone",
}


class InfomaniakError(RuntimeError):
    pass


def _record_type(record: DnsRecord) -> str:
    """``dns.py`` labels the host record ``CNAME/ALIAS``; Infomaniak knows CNAME."""
    return (
        "CNAME"
        if record.record_type.upper().startswith("CNAME")
        else record.record_type.upper()
    )


def _source(host: str, zone: str) -> str:
    """``host`` relative to ``zone``, the way the API names a record (``.`` for the apex)."""
    host = host.rstrip(".").lower()
    zone = zone.rstrip(".").lower()
    if host == zone:
        return APEX_SOURCE
    if not host.endswith(f".{zone}"):
        raise InfomaniakError(f"{host} is not inside the zone {zone}")
    return host[: -len(zone) - 1]


def _same_source(source: str) -> str:
    return source.rstrip(".").lower() or APEX_SOURCE


def _same_target(target: str) -> str:
    """A target as comparable text: the zone editor may quote a TXT value or end a name with a dot."""
    return target.strip().strip('"').rstrip(".").lower()


def _write_target(record: DnsRecord) -> str:
    """A CNAME target is sent as an absolute name so it is not read relative to the zone."""
    if _record_type(record) == "CNAME":
        return f"{record.value.rstrip('.')}."
    return record.value


def wanted_records(records: Iterable[DnsRecord], zone: str) -> list[DnsRecord]:
    """The rendered records, with the apex host's CNAME swapped for A records."""
    wanted: list[DnsRecord] = []
    for record in records:
        if (
            _record_type(record) == "CNAME"
            and _source(record.host, zone) == APEX_SOURCE
        ):
            wanted += [
                DnsRecord("A", record.host, address, record.notes)
                for address in APEX_ADDRESSES
            ]
        else:
            wanted.append(record)
    return wanted


class InfomaniakDns:
    def __init__(
        self,
        token: str,
        *,
        session: requests.Session | None = None,
        ttl: int = DEFAULT_TTL,
        timeout: float = 30.0,
        dry_run: bool = False,
    ) -> None:
        if not token:
            raise InfomaniakError("empty Infomaniak API token")
        if ttl < DEFAULT_TTL:
            raise InfomaniakError(
                f"Infomaniak needs a TTL of at least {DEFAULT_TTL} seconds, got {ttl}"
            )
        self._token = token
        self._http = session or requests.Session()
        self._ttl = ttl
        self._timeout = timeout
        self._dry_run = dry_run

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._http.request(
            method,
            f"{INFOMANIAK_API}{path}",
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
            raise InfomaniakError(
                f"{method} {path} returned HTTP {response.status_code} with a non-JSON body"
            ) from None

        if payload.get("result") != "success":
            hint = _AUTH_HINTS.get(response.status_code, "")
            raise InfomaniakError(
                f"{method} {path} failed (HTTP {response.status_code}): {_error_text(payload)}"
                f"{f' ({hint})' if hint else ''}"
            )
        return payload

    def list_records(self, zone: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._call(
                "GET", f"/2/zones/{zone}/records", params={"page": page}
            )
            records += payload.get("data") or []
            if page >= int(payload.get("pages") or 1):
                return records
            page += 1

    def _create(self, zone: str, source: str, record: DnsRecord) -> None:
        if self._dry_run:
            return
        body = {
            "type": _record_type(record),
            "source": source,
            "target": _write_target(record),
            "ttl": self._ttl,
        }
        self._call("POST", f"/2/zones/{zone}/records", json=body)

    def _update(self, zone: str, record_id: int, record: DnsRecord) -> None:
        if self._dry_run:
            return
        body = {"target": _write_target(record), "ttl": self._ttl}
        self._call("PUT", f"/2/zones/{zone}/records/{record_id}", json=body)

    def sync_name(
        self,
        zone: str,
        source: str,
        wanted: list[DnsRecord],
        current: list[dict[str, Any]],
    ) -> list[RecordOutcome]:
        """Make ``current`` (the zone's records of one type on one name) hold every wanted value.

        A wanted value that is already there is kept, a missing one takes over a
        record holding an unwanted value, and only then is a new record created."""
        holders = {_same_target(r["target"]): r for r in current}
        wanted_targets = {_same_target(w.value) for w in wanted}
        strays = [r for r in current if _same_target(r["target"]) not in wanted_targets]

        outcomes = []
        for record in wanted:
            holder = holders.get(_same_target(record.value))
            if holder is not None:
                outcomes.append(self._align_ttl(zone, record, holder))
            elif strays:
                stray = strays.pop(0)
                was = stray["target"]
                self._update(zone, stray["id"], record)
                outcomes.append(
                    RecordOutcome(record, "updated", f"{was} -> {record.value}")
                )
            else:
                self._create(zone, source, record)
                outcomes.append(RecordOutcome(record, "created"))

        if strays:
            left = ", ".join(str(r["target"]) for r in strays)
            outcomes[0] = _with_warning(
                outcomes[0], f"left other value(s) alone: {left}"
            )
        return outcomes

    def _align_ttl(
        self, zone: str, record: DnsRecord, holder: dict[str, Any]
    ) -> RecordOutcome:
        was = int(holder.get("ttl") or 0)
        if was == self._ttl:
            return RecordOutcome(record, "unchanged")
        self._update(zone, holder["id"], record)
        return RecordOutcome(record, "updated", f"ttl {was} -> {self._ttl}")


def _with_warning(outcome: RecordOutcome, warning: str) -> RecordOutcome:
    both = f"{outcome.warning}; {warning}" if outcome.warning else warning
    return replace(outcome, warning=both)


def _error_text(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return f"{error.get('code', '?')}: {error.get('description', error)}"
    return str(error) if error else "no error detail returned"


def apply_records(
    records: Iterable[DnsRecord],
    *,
    token: str,
    zone: str,
    ttl: int = DEFAULT_TTL,
    dry_run: bool = False,
    session: requests.Session | None = None,
) -> list[RecordOutcome]:
    """Bring every rendered record into the zone. Idempotent; never deletes."""
    client = InfomaniakDns(token, session=session, ttl=ttl, dry_run=dry_run)

    names: dict[tuple[str, str], list[DnsRecord]] = {}
    for record in wanted_records(records, zone):
        names.setdefault((_record_type(record), _source(record.host, zone)), []).append(
            record
        )
    if not names:
        return []

    existing = client.list_records(zone)
    outcomes: list[RecordOutcome] = []
    for (record_type, source), wanted in names.items():
        current = [
            r
            for r in existing
            if r["type"] == record_type and _same_source(r["source"]) == source
        ]
        name_outcomes = client.sync_name(zone, source, wanted, current)
        # IPv6 clients would follow an AAAA record past the gateway; not ours to remove.
        ipv6 = [
            r["target"]
            for r in existing
            if r["type"] == "AAAA" and _same_source(r["source"]) == source
        ]
        if ipv6 and record_type in ("A", "CNAME"):
            name_outcomes[0] = _with_warning(
                name_outcomes[0],
                f"AAAA record(s) left alone, IPv6 clients will not reach the gateway: {', '.join(ipv6)}",
            )
        outcomes += name_outcomes
    return outcomes
