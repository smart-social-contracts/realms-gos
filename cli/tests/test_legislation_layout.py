"""The layout rules of a ``legislation/`` tree (docs/codices/layered-codex.md §3), checked by ``realms legislation
check`` before it loads any chapter. Each rule has a tree that breaks it and one that keeps it.

The tree lives in a throw-away git repository, one folder down (``repo/legislation``), because a release's checksums
are read from git at the release's own commit.
"""

import ast
import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from realms.cli.commands import legislation
from realms.cli.commands.legislation import legislation_app
from realms.cli.commands.legislation_layout import GGG_DOMAINS, layout_problems

ROOT = Path(__file__).resolve().parents[2]
VALENCIA = "eu/es/vc/46250-valencia"
CHAPTER = "from realms.legislation import above\n\nclass {name}(above('{domain}')):\n    pass\n"
CALENDAR = {
    "weekend": ["sat", "sun"],
    "years": {"2027": [{"date": "2027-01-01", "name": {"es": "Año Nuevo"}}]},
    "legislation": "Ley 39/2015 Art. 30.7 (illustrative dates)",
}


def git(repo: Path, *args: str) -> str:
    # -c commit.gpgsign=false: a developer's signing key must not be asked for in a throw-away repository
    command = [
        "git",
        "-c",
        "user.email=t@example.org",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
        "-C",
        str(repo),
    ]
    return (
        subprocess.run(command + list(args), check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def write(tree: Path, path: str, content) -> None:
    file = tree / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps(content, ensure_ascii=False)
        if isinstance(content, (dict, list))
        else content
    )


def source_json(source: str, **changes) -> dict:
    return {
        "id": source,
        "name": {"en": source},
        "created_by": "an act",
        "publisher": {"realm": None},
        **changes,
    }


def chapter(domain: str) -> str:
    return CHAPTER.format(
        name="".join(w.capitalize() for w in domain.split("_")), domain=domain
    )


def checksum(file: Path) -> str:
    return "sha256:" + sha256(file.read_bytes()).hexdigest()


def commit(repo: Path) -> str:
    """Everything as it is now, as a commit; its hash."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "tree")
    return git(repo, "rev-parse", "HEAD")


def release(tree: Path, source: str, at: str, **changes) -> dict:
    """The release of ``source``'s files as they are now, which ``at`` (a commit) holds."""
    folder = tree / source
    made = {
        "version": "1.0.0",
        "published": "2026-01-01",
        "effective_from": "2026-02-01",
        "commit": at,
        "chapters": {
            f.stem: checksum(f)
            for f in sorted(folder.glob("*.py"))
            if f.stem != "realm"
        },
        "calendar": (
            checksum(folder / "calendar.json")
            if (folder / "calendar.json").exists()
            else None
        ),
        "notes": {"en": "a release"},
    }
    return {**made, **changes}


@pytest.fixture
def repo(tmp_path):
    """eu and eu/es publish releases (eu/es with a calendar); València is a realm's own layer, with a realm chapter."""
    git(tmp_path, "init", "-q")
    tree = tmp_path / "legislation"
    for source in ("eu", "eu/es", "eu/es/vc", VALENCIA):
        write(tree, f"{source}/source.json", source_json(source))
    write(tree, "eu/data_protection.py", chapter("data_protection"))
    write(tree, "eu/es/tax.py", chapter("tax"))
    write(tree, "eu/es/procedure.py", chapter("procedure"))
    write(tree, "eu/es/calendar.json", CALENDAR)
    write(tree, f"{VALENCIA}/realm.py", chapter("realm"))
    write(tree, f"{VALENCIA}/tax.py", chapter("tax"))
    at = commit(tmp_path)
    for source in ("eu", "eu/es"):
        write(tree, f"{source}/releases.json", [release(tree, source, at)])
    commit(tmp_path)
    return tmp_path


@pytest.fixture
def tree(repo):
    return repo / "legislation"


def problems(tree: Path) -> list:
    return layout_problems(tree)


def breaks(tree: Path, expected: str) -> None:
    """The tree has a problem that says ``expected``."""
    found = problems(tree)
    assert any(expected in p for p in found), found


def test_a_tree_that_follows_the_layout_has_no_problem(tree):
    assert problems(tree) == []


# ── Folder names ──────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["Es", "es_vc", "es vc", "ñ"])
def test_a_folder_name_of_other_characters_is_refused(tree, name):
    write(tree, f"eu/{name}/source.json", source_json(f"eu/{name}"))
    breaks(tree, f"eu/{name}: folder names are lowercase letters, digits and -")


def test_a_folder_name_of_lowercase_letters_digits_and_hyphens_is_kept(tree):
    write(
        tree,
        "eu/es/vc/46000-santa-pola/source.json",
        source_json("eu/es/vc/46000-santa-pola"),
    )
    assert problems(tree) == []


# ── The .py files of a source ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["foo.py", "Tax.py", "__init__.py", "local-regime.py"])
def test_a_py_file_that_is_not_a_ggg_domain_is_refused(tree, name):
    write(tree, f"eu/es/{name}", "")
    breaks(tree, f"eu/es/{name}: {name[:-3]} is not a GGG domain")


@pytest.mark.parametrize("domain", GGG_DOMAINS)
def test_a_py_file_named_after_a_ggg_domain_is_kept(tree, domain):
    write(tree, f"{VALENCIA}/{domain}.py", chapter(domain))
    assert problems(tree) == []


def test_a_py_file_outside_a_source_is_refused(tree):
    write(tree, "eu/es/vc/notes/tax.py", chapter("tax"))
    write(tree, "tax.py", chapter("tax"))
    breaks(
        tree,
        "eu/es/vc/notes/tax.py: not in a source: a source is a folder with a source.json",
    )
    breaks(tree, "tax.py: not in a source")


def test_the_json_files_of_a_source_outside_a_source_are_refused(tree):
    write(tree, "eu/es/vc/notes/calendar.json", CALENDAR)
    write(tree, "source.json", source_json("."))
    breaks(tree, "eu/es/vc/notes/calendar.json: not in a source")
    breaks(tree, "source.json: not in a source")


def test_a_file_that_is_not_a_chapter_or_one_of_the_three_json_files_is_left_alone(
    tree,
):
    write(tree, "eu/es/NOTES.md", "for authors")
    assert problems(tree) == []


# ── source.json ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_source_json_names_the_folder_in_id(tree):
    write(tree, "eu/es/vc/source.json", source_json("eu/es/cv"))
    breaks(tree, "eu/es/vc/source.json: id is 'eu/es/cv', but the folder is eu/es/vc")


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"name": {}}, "name is the official names by language"),
        ({"name": "Spain"}, "name is the official names by language"),
        ({"name": {"es": ""}}, "name is the official names by language"),
        ({"created_by": ""}, "created_by is the act that created the source"),
        ({"publisher": None}, "publisher is"),
        ({"publisher": {}}, "publisher is"),
        ({"publisher": {"realm": 7}}, "publisher is"),
    ],
)
def test_source_json_needs_names_an_act_and_a_publisher(tree, changes, expected):
    write(tree, "eu/es/source.json", source_json("eu/es", **changes))
    breaks(tree, f"eu/es/source.json: {expected}")


def test_source_json_leaves_out_the_act_when_none_created_the_source(tree):
    """A municipality older than any statute has no act to name; one that is named must be text."""
    without = source_json("eu/es")
    del without["created_by"]
    write(tree, "eu/es/source.json", without)
    assert problems(tree) == []
    write(tree, "eu/es/source.json", source_json("eu/es", created_by=None))
    breaks(
        tree,
        "eu/es/source.json: created_by is the act that created the source; leave it out when none did",
    )


def test_source_json_keeps_a_publisher_with_a_realm_of_its_own(tree):
    write(
        tree, "eu/es/source.json", source_json("eu/es", publisher={"realm": "aaaaa-aa"})
    )
    assert problems(tree) == []


@pytest.mark.parametrize("text", ["{not json", "[]", '"eu"'])
def test_source_json_is_a_json_object(tree, text):
    write(tree, "eu/es/source.json", text)
    assert any(p.startswith("eu/es/source.json: ") for p in problems(tree))


# ── calendar.json ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_a_calendar_names_the_provision_its_dates_come_from(tree):
    write(
        tree,
        "eu/es/calendar.json",
        {k: v for k, v in CALENDAR.items() if k != "legislation"},
    )
    breaks(
        tree, "eu/es/calendar.json: legislation is the provision the dates come from"
    )


def test_a_holiday_is_named_by_language(tree):
    write(
        tree,
        "eu/es/calendar.json",
        {**CALENDAR, "years": {"2027": [{"date": "2027-01-01"}]}},
    )
    breaks(tree, "eu/es/calendar.json: holiday 2027-01-01 has no name by language")


@pytest.mark.parametrize(
    "calendar, expected",
    [
        ({**CALENDAR, "weekend": ["saturday"]}, "'saturday' is not a day of the week"),
        (
            {**CALENDAR, "years": {"27": []}},
            "a calendar year is written as four digits",
        ),
        (
            {
                **CALENDAR,
                "years": {"2027": [{"date": "2026-01-01", "name": {"es": "x"}}]},
            },
            "is not a date of 2027",
        ),
        ([], "an object"),
    ],
)
def test_a_calendar_the_library_would_refuse_is_refused(tree, calendar, expected):
    write(tree, "eu/es/calendar.json", calendar)
    found = problems(tree)
    assert any(
        p.startswith("eu/es/calendar.json: ") and expected in p for p in found
    ), found


def test_a_calendar_needs_no_weekend_and_a_holiday_free_year_is_declared(tree):
    write(
        tree,
        "eu/es/vc/calendar.json",
        {"years": {"2027": []}, "legislation": "illustrative"},
    )
    assert problems(tree) == []


# ── releases.json: the shape ──────────────────────────────────────────────────────────────────────────────────────


def one_release(tree, **changes):
    write(
        tree,
        "eu/releases.json",
        [release(tree, "eu", git(tree, "rev-parse", "HEAD"), **changes)],
    )


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"version": ""}, "version is text"),
        ({"published": "2026-13-01"}, "published is a day"),
        ({"effective_from": "soon"}, "effective_from is a day"),
        ({"commit": "abc123"}, "commit is the 40 hex digits"),
        ({"commit": "A" * 40}, "commit is the 40 hex digits"),
        ({"chapters": {}}, "chapters is {domain: sha256:<hex>}"),
        (
            {"chapters": {"data_protection": "sha256:xyz"}},
            "chapters.data_protection is sha256:<64 hex digits>",
        ),
        (
            {"chapters": {"mining": "sha256:" + "0" * 64}},
            "chapters lists mining, which is not a GGG domain",
        ),
        ({"calendar": "sha256:short"}, "calendar is sha256:<64 hex digits>, or null"),
        ({"notes": "a release"}, "notes are text by language"),
    ],
)
def test_a_release_has_the_fields_of_the_design(tree, changes, expected):
    one_release(tree, **changes)
    breaks(
        tree, f"eu/releases.json: release {changes.get('version', '1.0.0')}: {expected}"
    )


def test_a_stand_in_release_published_after_its_legislation_took_effect_is_kept(tree):
    one_release(tree, published="2026-06-01", effective_from="2026-01-01")
    assert problems(tree) == []


def test_a_release_without_notes_or_a_calendar_is_kept(tree):
    one_release(tree)
    release_ = json.loads((tree / "eu/releases.json").read_text())
    del release_[0]["notes"], release_[0]["calendar"]
    write(tree, "eu/releases.json", release_)
    assert problems(tree) == []


@pytest.mark.parametrize("text", ["{}", "[]", "[1]", "not json"])
def test_releases_json_is_a_list_of_releases(tree, text):
    write(tree, "eu/releases.json", text)
    assert any(p.startswith("eu/releases.json: ") for p in problems(tree))


def test_releases_go_newest_last_and_a_version_is_released_once(tree):
    at = git(tree, "rev-parse", "HEAD")
    first, second = release(tree, "eu", at), release(
        tree,
        "eu",
        at,
        version="1.1.0",
        published="2026-03-01",
        effective_from="2026-04-01",
    )
    write(tree, "eu/releases.json", [first, second])
    assert problems(tree) == []
    write(tree, "eu/releases.json", [second, first])
    breaks(tree, "eu/releases.json: releases are newest last: published goes up")
    write(tree, "eu/releases.json", [first, dict(second, version="1.0.0")])
    breaks(tree, "eu/releases.json: a version is released twice")


def test_published_days_are_compared_as_days_not_as_text(tree):
    at = git(tree, "rev-parse", "HEAD")
    january = release(tree, "eu", at, published="2026-1-10", effective_from="2026-2-1")
    february = release(
        tree,
        "eu",
        at,
        version="1.1.0",
        published="2026-02-01",
        effective_from="2026-3-1",
    )
    write(
        tree, "eu/releases.json", [january, february]
    )  # as text, "2026-1-10" sorts after "2026-02-01"
    assert problems(tree) == []
    write(tree, "eu/releases.json", [february, january])
    breaks(tree, "eu/releases.json: releases are newest last: published goes up")


# ── releases.json against the files at its commit ─────────────────────────────────────────────────────────────────


def test_a_release_lists_all_of_the_sources_chapters(tree):
    release_ = json.loads((tree / "eu/es/releases.json").read_text())
    del release_[0]["chapters"]["procedure"]
    write(tree, "eu/es/releases.json", release_)
    breaks(
        tree,
        "release 1.0.0: does not list procedure, but eu/es/procedure.py is in commit",
    )


def test_a_release_lists_only_chapters_its_commit_holds(tree):
    release_ = json.loads((tree / "eu/es/releases.json").read_text())
    release_[0]["chapters"]["water"] = release_[0]["chapters"]["tax"]
    write(tree, "eu/es/releases.json", release_)
    breaks(tree, "release 1.0.0: lists water, but eu/es/water.py is not in commit")


def test_a_release_lists_the_calendar_if_the_source_has_one(tree):
    release_ = json.loads((tree / "eu/es/releases.json").read_text())
    release_[0]["calendar"] = None
    write(tree, "eu/es/releases.json", release_)
    breaks(
        tree,
        "release 1.0.0: does not list the calendar, but eu/es/calendar.json is in commit",
    )


def test_a_release_lists_no_calendar_the_source_does_not_have(tree):
    release_ = json.loads((tree / "eu/releases.json").read_text())
    release_[0]["calendar"] = checksum(tree / "eu/es/calendar.json")
    write(tree, "eu/releases.json", release_)
    breaks(
        tree, "release 1.0.0: lists a calendar, but eu/calendar.json is not in commit"
    )


def test_a_release_never_lists_the_realm_domain(tree):
    one_release(tree)
    release_ = json.loads((tree / "eu/releases.json").read_text())
    release_[0]["chapters"]["realm"] = release_[0]["chapters"]["data_protection"]
    write(tree, "eu/releases.json", release_)
    breaks(
        tree, "chapters lists the realm domain, which only the realm's own layer fills"
    )


def test_a_realm_chapter_beside_releases_is_the_sources_own_and_no_release_carries_it(
    tree,
):
    # Spain publishes releases and is a realm too: its realm.py is not a missing chapter, and it is not listed
    write(tree, "eu/es/realm.py", chapter("realm"))
    at = commit(tree.parent)
    write(tree, "eu/es/releases.json", [release(tree, "eu/es", at)])
    assert (
        "realm"
        not in json.loads((tree / "eu/es/releases.json").read_text())[0]["chapters"]
    )
    assert problems(tree) == []


def test_every_checksum_is_the_sha256_of_the_file_at_the_releases_commit(tree):
    release_ = json.loads((tree / "eu/es/releases.json").read_text())
    release_[0]["chapters"]["tax"] = "sha256:" + "0" * 64
    write(tree, "eu/es/releases.json", release_)
    breaks(
        tree,
        f"eu/es/tax.py at commit {release_[0]['commit'][:7]} is {checksum(tree / 'eu/es/tax.py')}, not the sha256:{'0' * 64}",
    )


def test_the_calendars_checksum_is_checked_too(tree):
    release_ = json.loads((tree / "eu/es/releases.json").read_text())
    release_[0]["calendar"] = "sha256:" + "1" * 64
    write(tree, "eu/es/releases.json", release_)
    breaks(tree, "eu/es/calendar.json at commit")


def test_a_chapter_edited_after_its_release_is_a_proposal_in_progress_not_a_broken_release(
    tree,
):
    (tree / "eu/es/tax.py").write_text(chapter("tax") + "\n# a proposed change\n")
    (tree / "eu/es/calendar.json").write_text(
        json.dumps({**CALENDAR, "legislation": "changed"})
    )
    commit(tree.parent)
    assert problems(tree) == []


def test_a_commit_outside_the_history_of_head_is_refused(tree):
    """A side branch, and a commit left dangling when its branch was deleted, hold bytes the tree's history never had."""
    repo = tree.parent
    git(repo, "checkout", "-q", "-b", "side")
    (tree / "eu/data_protection.py").write_text(
        chapter("data_protection") + "\n# a side branch\n"
    )
    side = commit(repo)
    git(repo, "checkout", "-q", "-")
    release_ = json.loads((tree / "eu/releases.json").read_text())
    release_[0]["commit"] = side
    release_[0]["chapters"]["data_protection"] = (
        "sha256:"
        + sha256(
            (chapter("data_protection") + "\n# a side branch\n").encode()
        ).hexdigest()
    )
    write(tree, "eu/releases.json", release_)
    breaks(tree, f"release 1.0.0: commit {side[:7]} is not in the history of HEAD")
    git(repo, "branch", "-D", "side")
    breaks(tree, f"release 1.0.0: commit {side[:7]} is not in the history of HEAD")


def test_a_commit_the_checkout_does_not_have_is_reported(tree):
    release_ = json.loads((tree / "eu/releases.json").read_text())
    release_[0]["commit"] = "f" * 40
    write(tree, "eu/releases.json", release_)
    breaks(tree, "release 1.0.0: commit fffffff cannot be read")
    breaks(tree, "check out the full history")


def test_releases_in_a_folder_that_is_not_a_git_checkout_are_reported(tmp_path, tree):
    copy = tmp_path.parent / (tmp_path.name + "-copy")
    subprocess.run(["cp", "-r", str(tree), str(copy)], check=True)
    breaks(copy, "cannot be read")


# ── The command ───────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def check(monkeypatch):
    monkeypatch.setattr(
        legislation,
        "ggg_operations",
        lambda: frozenset({"user_add"}) | legislation.INTERIM_OPERATIONS,
    )

    def run(tree):
        return CliRunner().invoke(legislation_app, ["check", str(tree)])

    return run


def test_check_stops_at_the_layout_before_it_loads_a_chapter(tree, check):
    write(tree, "eu/es/foo.py", "this is not python (")
    out = check(tree)
    assert out.exit_code == 1
    assert "eu/es/foo.py: foo is not a GGG domain" in out.output
    assert (
        "1 refused" in out.output
    )  # the syntax error of foo.py is not loaded, so it is not counted


def test_check_loads_the_chapters_once_the_layout_holds(tree, check):
    out = check(tree)
    assert out.exit_code == 0, out.output
    assert "ok: 4 realm(s) checked" in out.output


def test_the_domain_list_is_the_canisters():
    source = (ROOT / "src/realm_backend/core/change_list.py").read_text()
    listed = next(
        ast.literal_eval(node.value)
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "DOMAINS"
    )
    assert GGG_DOMAINS == listed
