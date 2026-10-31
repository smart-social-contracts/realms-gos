"""DNS record rendering for IC custom domains.

Used by ``realms domains`` for every sheet with a ``domains`` block — the
Realms marketplace and the GaaS portal alike.
"""

from __future__ import annotations

from dataclasses import dataclass

ICP_GATEWAY = "icp1.io"
ICP_ACME_GATEWAY = "icp2.io"


@dataclass(frozen=True)
class DnsRecord:
    record_type: str
    host: str
    value: str
    notes: str = ""


@dataclass(frozen=True)
class RecordOutcome:
    """What a DNS provider did (or, in a dry run, would do) for one record."""

    record: DnsRecord
    action: str  # created | updated | unchanged
    detail: str = ""


def render_dns_records(domain: str, frontend_canister_id: str) -> list[DnsRecord]:
    """Render the exact DNS records required for an IC custom domain."""
    domain = domain.rstrip(".").lower()
    acme_target = f"_acme-challenge.{domain}.{ICP_ACME_GATEWAY}"

    return [
        DnsRecord(
            record_type="CNAME/ALIAS",
            host=domain,
            value=f"{domain}.{ICP_GATEWAY}",
            notes="apex/subdomain host → IC gateway",
        ),
        DnsRecord(
            record_type="TXT",
            host=f"_canister-id.{domain}",
            value=frontend_canister_id,
            notes="canister ownership proof",
        ),
        DnsRecord(
            record_type="CNAME",
            host=f"_acme-challenge.{domain}",
            value=acme_target,
            notes="ACME certificate challenge delegation",
        ),
    ]
