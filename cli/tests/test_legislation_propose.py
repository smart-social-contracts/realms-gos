"""``realms legislation propose`` (docs/codices/layered-codex.md §7, §12.5): the change list for the IBI bonus change of
§5.6 (València's tax chapter) written byte for byte, what it refuses, and that it reaches a network only when asked.
The tree is ``tests/fixtures/legislation/valencia`` in a throw-away git repository (``valencia_repo``).
"""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from realms.cli.commands import legislation_propose
from realms.cli.commands.legislation import legislation_app
from realms.legislation import LegislationError

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/legislation"

VALENCIA = "eu/es/vc/46250-valencia"
TAX = f"{VALENCIA}/tax"
URL = "https://raw.githubusercontent.com/smart-social-contracts/realms-codices/main/changes/bonus.json"
CODEX = "sha256:" + "ab" * 32
# València's tax chapter in the fixture: if the fixture is refreshed these change, and so does the expected file.
TAX_SHA256 = "65166c2f0dcadffaf39480a0f819e19e64ce6222d34140adcb57d363a2d4ba48"
TAX_SIZE = 853
SUMMARY = (
    "--summary",
    "ca-valencia=IBI 2027: bonificació per família nombrosa",
    "--summary",
    "es=IBI 2027: bonificación por familia numerosa",
)

# §7's change list for the bonus change, as canonical JSON: sorted keys, no spaces, UTF-8, no newline at the end.
# "<commit>" stands for the commit the chapter is read at. tests/ggg gives the same file to the realm's own parser.
BONUS_CHANGE = (FIXTURES / "bonus-change.json").read_bytes().decode("utf-8")


@pytest.fixture
def tree(valencia_repo):
    return valencia_repo / "legislation"


@pytest.fixture
def chapters_commit(valencia_repo, git):
    """The commit that holds the chapters, which the releases commit names."""
    return git(valencia_repo, "rev-parse", "HEAD~1")


@pytest.fixture
def no_network(monkeypatch):
    """Fails any call to a realm."""

    def refuse(*args, **kw):
        raise AssertionError(f"a realm was called: {args} {kw}")

    monkeypatch.setattr(legislation_propose, "call_op", refuse)


def propose(tree, out, *args, realm=VALENCIA):
    command = ["propose", str(tree), "--realm", realm, "--out", str(out), "--url", URL]
    return CliRunner().invoke(legislation_app, command + list(args))


def bonus(tree, out, commit, *more):
    return propose(
        tree,
        out,
        "--commit",
        commit,
        "--set",
        TAX,
        "--replaces",
        CODEX,
        *SUMMARY,
        *more,
    )


def test_the_fixture_is_the_chapter_the_expected_file_hashes():
    data = (FIXTURES / "valencia" / f"eu/{TAX[3:]}.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == TAX_SHA256 and len(data) == TAX_SIZE


def test_it_writes_the_bonus_change_list_byte_for_byte(
    tree, tmp_path, chapters_commit, no_network
):
    out = bonus(tree, tmp_path / "bonus.json", chapters_commit)
    assert out.exit_code == 0, out.output
    expected = BONUS_CHANGE.replace("<commit>", chapters_commit)
    assert (tmp_path / "bonus.json").read_bytes() == expected.encode("utf-8")


def test_it_prints_the_code_to_submit_with_the_checksum_of_the_file(
    tree, tmp_path, chapters_commit, no_network
):
    out = bonus(tree, tmp_path / "bonus.json", chapters_commit)
    data = (tmp_path / "bonus.json").read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    assert f"wrote {tmp_path / 'bonus.json'} ({len(data)} bytes)" in out.output
    assert f'Code {{ url = "{URL}"; checksum = "sha256:{checksum}" }}' in out.output


def test_the_url_names_the_commit_in_full_whatever_name_it_was_given_by(
    tree, tmp_path, chapters_commit, no_network
):
    out = bonus(tree, tmp_path / "bonus.json", "HEAD~1")
    assert out.exit_code == 0, out.output
    entry = json.loads((tmp_path / "bonus.json").read_text())["set"][0]
    assert f"/realms-codices/{chapters_commit}/legislation/{TAX}.py" in entry["url"]


def test_the_checksum_and_size_are_those_of_the_chapter_at_the_commit(
    valencia_copy, tmp_path, git, no_network
):
    chapter = valencia_copy / "legislation" / f"{TAX}.py"
    chapter.write_text(
        chapter.read_text() + "# later\n"
    )  # not committed: not what is read
    out = bonus(valencia_copy / "legislation", tmp_path / "bonus.json", "HEAD")
    entry = json.loads((tmp_path / "bonus.json").read_text())["set"][0]
    assert entry["sha256"] == TAX_SHA256 and entry["size"] == TAX_SIZE, out.output
    git(valencia_copy, "commit", "-q", "-a", "--no-verify", "-m", "later")
    bonus(valencia_copy / "legislation", tmp_path / "later.json", "HEAD")
    later = json.loads((tmp_path / "later.json").read_text())["set"][0]
    assert later["sha256"] != TAX_SHA256 and later["size"] == TAX_SIZE + len(
        "# later\n"
    )


def test_a_calendar_is_set_and_a_chapter_removed_by_their_ids(
    tree, tmp_path, chapters_commit, no_network
):
    out = propose(
        tree,
        tmp_path / "x.json",
        "--commit",
        chapters_commit,
        "--set",
        f"{VALENCIA}/calendar",
        "--remove",
        f"{VALENCIA}/procedure",
        "--replaces",
        "none",
    )
    assert out.exit_code == 0, out.output
    change = json.loads((tmp_path / "x.json").read_text())
    assert [e["chapter"] for e in change["set"]] == [f"{VALENCIA}/calendar"]
    assert change["set"][0]["url"].endswith(f"/legislation/{VALENCIA}/calendar.json")
    assert change["remove"] == [f"{VALENCIA}/procedure"] and change["replaces"] is None


def test_a_report_is_attached_with_its_address_and_the_checksum_of_its_bytes(
    tree, tmp_path, chapters_commit, no_network
):
    report = tmp_path / "report.md"
    report.write_text("newly allowed: nothing\n")
    out = bonus(
        tree,
        tmp_path / "x.json",
        chapters_commit,
        "--report",
        str(report),
        "--report-url",
        "https://example.org/report.md",
    )
    assert out.exit_code == 0, out.output
    assert json.loads((tmp_path / "x.json").read_text())["report"] == {
        "url": "https://example.org/report.md",
        "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    }


def test_without_a_report_the_change_list_has_none(
    tree, tmp_path, chapters_commit, no_network
):
    bonus(tree, tmp_path / "x.json", chapters_commit)
    assert "report" not in json.loads((tmp_path / "x.json").read_text())


# ---- replaces


def active_codex(monkeypatch, reply):
    """A realm that answers ``reply`` to a read, and the calls it got."""
    calls = []

    def call_op(canister, name, args=None, network=None, identity=None, read=False):
        calls.append((canister, name, network, identity, read))
        return reply

    monkeypatch.setattr(legislation_propose, "call_op", call_op)
    return calls


def replaces_from_realm(tree, tmp_path, commit, *more):
    out = propose(
        tree,
        tmp_path / "x.json",
        "--commit",
        commit,
        "--set",
        TAX,
        "--network",
        "local",
        "--canister",
        "a-realm",
        *more,
    )
    return out, (
        json.loads((tmp_path / "x.json").read_text()) if out.exit_code == 0 else None
    )


def test_replaces_is_read_from_the_realms_codex_status(
    tree, tmp_path, chapters_commit, monkeypatch
):
    calls = active_codex(monkeypatch, {"ok": True, "codex": {"id": CODEX}})
    out, change = replaces_from_realm(
        tree, tmp_path, chapters_commit, "--identity", "reader"
    )
    assert out.exit_code == 0, out.output
    assert change["replaces"] == CODEX
    assert calls == [("a-realm", "codex_status", "local", "reader", True)]


def test_a_realm_with_no_active_codex_is_replaced_by_none(
    tree, tmp_path, chapters_commit, monkeypatch
):
    active_codex(monkeypatch, {"ok": True, "codex": None})
    _, change = replaces_from_realm(tree, tmp_path, chapters_commit)
    assert change["replaces"] is None


def test_a_realm_that_refuses_the_read_stops_the_command(
    tree, tmp_path, chapters_commit, monkeypatch
):
    active_codex(monkeypatch, {"ok": False, "error": "not allowed"})
    out, _ = replaces_from_realm(tree, tmp_path, chapters_commit)
    assert (
        out.exit_code == 1 and "a-realm refused codex_status: not allowed" in out.output
    )
    assert not (tmp_path / "x.json").exists()


@pytest.mark.parametrize(
    "args, why",
    [
        ([], "say which codex this replaces"),
        (["--network", "local"], "say which codex this replaces"),
        (["--canister", "a-realm"], "say which codex this replaces"),
        (
            ["--replaces", CODEX, "--network", "local", "--canister", "a-realm"],
            "not both",
        ),
        (["--replaces", CODEX, "--network", "local"], "not both"),
    ],
)
def test_no_realm_is_called_unless_it_is_named_with_a_network(
    tree, tmp_path, chapters_commit, no_network, args, why
):
    out = propose(
        tree, tmp_path / "x.json", "--commit", chapters_commit, "--set", TAX, *args
    )
    assert out.exit_code == 1 and why in out.output
    assert not (tmp_path / "x.json").exists()


# ---- refusals


@pytest.mark.parametrize(
    "args, why",
    [
        (["--set", "eu/es/tax"], "only the realm's own layer"),
        (["--remove", "eu/es/tax"], "only the realm's own layer"),
        (["--set", f"{VALENCIA}/parking"], "parking is not a GGG domain"),
        (["--set", "tax"], "is not a source id"),
        (["--set", f"{VALENCIA}/water"], "water.py"),
        (["--set", TAX, "--set", TAX], "named twice"),
        (["--set", TAX, "--remove", TAX], "named twice"),
        ([], "changes nothing"),
        (["--set", TAX, "--summary", "no-equals"], "LANG=TEXT"),
        (["--set", TAX, "--report", "report.md"], "go together"),
        (["--set", TAX, "--report-url", "https://example.org/r.md"], "go together"),
    ],
)
def test_what_it_refuses(tree, tmp_path, chapters_commit, no_network, args, why):
    report = tmp_path / "report.md"
    report.write_text("a report\n")
    args = [str(report) if a == "report.md" else a for a in args]
    out = propose(
        tree,
        tmp_path / "x.json",
        "--commit",
        chapters_commit,
        "--replaces",
        CODEX,
        *args,
    )
    assert out.exit_code == 1 and why in out.output
    assert not (tmp_path / "x.json").exists()


def test_a_url_that_is_not_https_is_refused(
    tree, tmp_path, chapters_commit, no_network
):
    command = [
        "propose",
        str(tree),
        "--realm",
        VALENCIA,
        "--out",
        str(tmp_path / "x"),
        "--url",
        "http://x.test/c.json",
    ]
    command += ["--commit", chapters_commit, "--set", TAX, "--replaces", "none"]
    out = CliRunner().invoke(legislation_app, command)
    assert out.exit_code == 1 and "a realm fetches over https" in out.output
    report = tmp_path / "report.md"
    report.write_text("a report\n")
    out = bonus(
        tree,
        tmp_path / "x.json",
        chapters_commit,
        "--report",
        str(report),
        "--report-url",
        "http://x.test/r.md",
    )
    assert out.exit_code == 1 and "a realm fetches over https" in out.output


def test_a_commit_git_does_not_know_is_refused(tree, tmp_path, no_network):
    out = bonus(tree, tmp_path / "x.json", "no-such-commit")
    assert out.exit_code == 1 and not (tmp_path / "x.json").exists()


@pytest.mark.parametrize(
    "text, why",
    [
        pytest.param("", "is 0 bytes", id="empty"),
        pytest.param("#" * (256 * 1024 + 1), "is 262145 bytes", id="over-256-KB"),
    ],
)
def test_a_chapter_the_realm_would_not_store_is_refused(
    valencia_copy, tmp_path, git, no_network, text, why
):
    (valencia_copy / "legislation" / f"{TAX}.py").write_text(text)
    git(valencia_copy, "commit", "-q", "-a", "--no-verify", "-m", "a chapter")
    out = bonus(valencia_copy / "legislation", tmp_path / "x.json", "HEAD")
    assert out.exit_code == 1 and why in out.output


def test_more_chapters_than_a_change_list_may_set_are_refused():
    entries = [{"chapter": f"{VALENCIA}/domain{n}"} for n in range(51)]
    with pytest.raises(LegislationError, match="at most 50 chapters, not 51"):
        legislation_propose.change_list(VALENCIA, None, entries, [], None, {})


def test_the_output_folder_is_made(tree, tmp_path, chapters_commit, no_network):
    out = bonus(tree, tmp_path / "changes" / "x" / "bonus.json", chapters_commit)
    assert out.exit_code == 0 and (tmp_path / "changes/x/bonus.json").exists()
