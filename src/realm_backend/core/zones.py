"""The IANA time zones this release bundles (realms-gos#511): the package ``zonedata``, which scripts/build_zones.py
generates from the tz database, one module per zone file.

A realm reads the zone file of its own ``Realm.timezone`` and no other: the rest takes room in the wasm and a little
at install, nothing more. Every upgrade brings the release's tz database along, and with it the current rules.
"""
import sys

import zonedata
import zonedata._bundle  # noqa: F401  # never run: the bundler follows its imports, and so ships every zone


def known(name):
    """Whether ``name`` is ``UTC``, which needs no data, or a zone of the tz database this release bundles."""
    return isinstance(name, str) and (name == "UTC" or name in zonedata.MODULE)


def zone_file(name):
    """The raw bytes of the TZif file of the bundled zone ``name``; its module is read now, not before."""
    module = f"zonedata.{zonedata.MODULE[name]}"
    __import__(module)
    return sys.modules[module].DATA
