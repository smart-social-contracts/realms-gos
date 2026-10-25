"""
Codex Entity — Realms extension of the Basilisk Toolkit Codex.

Base Codex (name, url, checksum, code property, calls) comes from ic-basilisk-toolkit.
This module adds realms-specific relationships: courts and federation.

See: https://github.com/smart-social-contracts/realms-gos/issues/153
"""

from ic_python_db import OneToMany, OneToOne
from ic_basilisk_toolkit.entities import Codex as _BaseCodex


class Codex(_BaseCodex):
    # Realms-specific relationships (not part of Basilisk OS)
    courts = OneToMany("Court", "codex")
    federation = OneToOne("Realm", "federation_codex")
