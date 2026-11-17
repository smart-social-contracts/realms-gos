"""``core.db.page`` after rows were deleted. ``load_some`` skips a deleted id and takes the next row in its place, so a
page ends at its last row's id and not at ``from + count - 1``: ``next`` must follow that row, or the next page repeats
rows (realms-gos#574). ``realm_export`` of ``InstallJob`` shows it; every list the realm pages goes through ``page``."""

import pytest
import world
from ggg import InstallJob
from kit import ok

ROWS = 6


def make_jobs(deleted):
    world.build()
    for n in range(1, ROWS + 1):
        InstallJob(id=f"J-{n}", status="done")
    for n in deleted:
        InstallJob[f"J-{n}"].delete()


def export_page(start, count):
    return ok("realm_export", "ctl", entity="InstallJob", count=count, **{"from": start})


def export_all(count):
    """Every id the export lists, following ``next``; a page that repeats or goes back is refused."""
    ids, start, calls = [], 1, 0
    while start is not None:
        out = export_page(start, count)
        assert out["next"] is None or out["next"] > start
        ids += [row["id"] for row in out["rows"]]
        start, calls = out["next"], calls + 1
        assert calls <= ROWS + 1
    return ids


@pytest.mark.parametrize("count", [1, 2, 4])
@pytest.mark.parametrize("deleted", [(), (1,), (2,), (3,), (6,), (5, 6), (1, 3, 6), (1, 2, 3, 4, 5, 6)],
                         ids=lambda d: "deleted-" + ("-".join(map(str, d)) or "none"))
def test_every_row_that_is_left_is_listed_once_in_order(deleted, count):
    make_jobs(deleted)
    assert export_all(count) == [f"J-{n}" for n in range(1, ROWS + 1) if n not in deleted]


def test_a_deleted_id_inside_a_page_moves_next_past_the_row_that_took_its_place():
    make_jobs((2,))
    out = export_page(1, 2)
    assert [row["id"] for row in out["rows"]] == ["J-1", "J-3"] and out["next"] == 4
    assert [row["id"] for row in export_page(4, 2)["rows"]] == ["J-4", "J-5"]


def test_the_last_full_page_has_no_next_when_nothing_follows_it():
    make_jobs(())
    assert export_page(1, 3)["next"] == 4
    out = export_page(4, 3)
    assert [row["id"] for row in out["rows"]] == ["J-4", "J-5", "J-6"] and out["next"] is None
