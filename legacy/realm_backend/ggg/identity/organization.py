from ic_python_db import Entity, OneToMany, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.organization")


class Organization(Entity, TimestampedMixin):
    """External party the realm trades with (company, NGO, vendor, ...).

    Owns land, appears in the ledger, and can hold a license. Internal
    governance teams are ``Department``, not ``Organization``.
    """

    __alias__ = "name"
    name = String(min_length=2, max_length=256)
    owned_lands = OneToMany("Land", "owner_organization")
    ledger_entries = OneToMany("LedgerEntry", "organization")
    license = OneToOne("License", "organization")
