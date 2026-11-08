"""València, loaded from the repository's own tree (docs/codices/layered-codex.md §3, §5): the library's ``plan`` and
``resolve`` on the releases of ``tests/fixtures/legislation/valencia``, a copy of realms-codices ``legislation/``, and
the chapters those releases name. What a sync source relays is read from its ``releases.json``; the realm's own
layer, València's, is its folder."""

import hashlib
import json
from pathlib import Path

import pytest

from realms.cli.commands.legislation import check_view
from realms.legislation import months, plan, resolve

TREE = Path(__file__).resolve().parents[2] / "tests/fixtures/legislation/valencia"
VALENCIA = "eu/es/vc/46250-valencia"
OPERATIONS = (
    "codex_install",
    "document_share",
    "notification_send",
    "procedure_define",
    "proposal_create",
    "tax_policy_propose",
    "user_add",
)


def checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def newest_release(source: str) -> dict:
    """A source's newest release, as an entry of a sync source's ``layers`` (§8)."""
    return {
        "source": source,
        **json.loads((TREE / source / "releases.json").read_text())[-1],
    }


def relayed_by(source: str) -> dict:
    """What a sync source relays: the newest release of itself and of each source above it, top to bottom."""
    parts = source.split("/")
    above = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return {"source": source, "layers": [newest_release(s) for s in above]}


def own_layer(source: str) -> dict:
    """The realm's own layer, from its folder: no release, so each file's checksum is read from the file."""
    folder = TREE / source
    calendar = folder / "calendar.json"
    return {
        "chapters": {f.stem: checksum(f) for f in sorted(folder.glob("*.py"))},
        "calendar": checksum(calendar) if calendar.exists() else None,
    }


def files_by_checksum() -> tuple:
    """Every chapter's text and every calendar's data in the tree, by checksum: what the realm fetches."""
    chapters = {checksum(f): f.read_text() for f in TREE.rglob("*.py")}
    calendars = {
        checksum(f): json.loads(f.read_text()) for f in TREE.rglob("calendar.json")
    }
    return chapters, calendars


@pytest.fixture(scope="module")
def valencia():
    chapters, calendars = files_by_checksum()
    relays = [relayed_by("eu/es/vc"), relayed_by("eu/es/vc/emshi")]
    return resolve(
        plan(VALENCIA, relays, own_layer(VALENCIA)),
        chapters,
        calendars,
        operations=OPERATIONS,
        modules={"ggg": check_view()},
    )


def test_every_checksum_in_the_trees_releases_names_a_file_of_the_tree():
    chapters, calendars = files_by_checksum()
    for release_file in TREE.rglob("releases.json"):
        for release in json.loads(release_file.read_text()):
            assert set(release["chapters"].values()) <= set(chapters)
            assert release["calendar"] is None or release["calendar"] in calendars


def test_valencias_layer_paths_are_what_its_sync_sources_relay_then_its_own_layer(
    valencia,
):
    assert valencia.plan.paths == {
        "data_protection": ["eu", "eu/es", VALENCIA],
        "language": ["eu/es", "eu/es/vc"],
        "local_regime": ["eu/es", "eu/es/vc", VALENCIA],
        "procedure": ["eu/es", VALENCIA],
        "register": ["eu/es"],
        "tax": ["eu/es", VALENCIA],
        "water": ["eu/es/vc/emshi"],
    }
    assert valencia.plan.layers == [
        "eu",
        "eu/es",
        "eu/es/vc",
        "eu/es/vc/emshi",
        VALENCIA,
    ]
    assert valencia.plan.skipped == []


def test_the_layers_calendars_are_the_union_of_spains_the_regions_and_valencias(
    valencia,
):
    assert [c["source"] for c in valencia.plan.calendars] == [
        "eu/es",
        "eu/es/vc",
        VALENCIA,
    ]
    calendar = valencia.codex.calendar
    assert calendar.has_year(2026) and calendar.has_year(2027)
    assert {"2027-01-01", "2027-10-09", "2027-01-22"} <= calendar.holidays[2027]


def test_valencia_decides_in_two_months_and_counts_working_days_on_all_three_calendars(
    valencia,
):
    codex = valencia.codex
    procedure = codex.instance("procedure", codex.context())
    assert procedure.default_decision_deadline == months(2)
    # 15 working days from 31 December 2026: Spain's 1 and 6 January and València's 22 January are not working days
    assert procedure.working_days_after("2026-12-31", 15) == "2027-01-26"


def test_a_realm_that_does_not_sync_from_emshi_has_no_water_chapter():
    chapters, calendars = files_by_checksum()
    town = plan("eu/es/vc/46000-town", [relayed_by("eu/es/vc")])
    assert "water" not in town.paths
    resolved = resolve(
        town, chapters, calendars, operations=OPERATIONS, modules={"ggg": check_view()}
    )
    assert resolved.codex.instance(
        "procedure", resolved.codex.context()
    ).default_decision_deadline == months(3)


def test_the_flattened_view_of_tax_shows_which_layer_sets_what(valencia):
    members = {
        m["name"]: [layer["source"] for layer in m["layers"]]
        for m in valencia.view("tax")["members"]
    }
    assert members["ibi_urban_cap"] == ["eu/es"]
    assert members["capital"] == ["eu/es", VALENCIA]
    assert members["ibi_bonus"] == ["eu/es", VALENCIA]
