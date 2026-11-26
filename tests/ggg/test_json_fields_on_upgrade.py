"""A Json field a row was stored without still reads as a list after its entity's version changes.

When a row of an older version is read, ic-python-db fills each field the row lacks with that field's default. A
Json field keeps its default as JSON text (``"[]"``) and the row is built through its ``__set__``, which encodes it
again: up to ic-python-db 0.16 the field then reads, and is stored as, the string ``"[]"`` for good. ``migrate()``
setting the field itself avoids it, as ``Notification.migrate`` does for ``translations``. That works under 0.14 and
0.17 alike (0.17 no longer fills such a field).

The upgrade from the 8 Nov build to main bumps ``Realm`` (every realm has its row) and ``Proposal``, whose rows of
that build have no ``languages`` and no ``links``.
"""

import world
from ggg import Proposal, Realm
from ic_python_db import Database
from kit import ok
from test_ratios import saved, stored_as_version_1


def test_a_realm_stored_without_languages_reads_a_list():
    world.build()
    stored_as_version_1(Realm, "1", drop=("languages",))
    assert Realm["1"].languages == [] and type(Realm["1"].languages) is list
    assert saved(Realm, "1")["languages"] == []
    Database.get_instance().clear_registry()  # a later message reads what the migration stored
    assert type(Realm["1"].languages) is list


def test_a_proposal_stored_without_links_reads_a_list():
    world.build()
    p = ok("proposal_create", "alice", title="x")["proposal"]
    stored_as_version_1(Proposal, p, drop=("links",))
    assert Proposal[p].links == [] and type(Proposal[p].links) is list
    assert saved(Proposal, p)["links"] == []
    Database.get_instance().clear_registry()
    assert type(Proposal[p].links) is list
