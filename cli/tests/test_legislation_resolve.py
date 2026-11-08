"""``realms legislation resolve`` (docs/codices/layered-codex.md §12.4): València's codex id, layer paths, flattened
view (§5.9) and generated Cedar (§5.8), from the records of the tree's own ``releases.json`` or from files in the shape
of §8. The tree is ``tests/fixtures/legislation/valencia`` in a throw-away git repository (``valencia_repo``).
"""

import json
import re
import subprocess

import pytest
from typer.testing import CliRunner

from realms.cli.commands.legislation import legislation_app
from realms.cli.commands.legislation_layout import commit_of
from realms.cli.commands.legislation_resolve import layer_at, tree_record

VALENCIA = "eu/es/vc/46250-valencia"
SYNC_SOURCES = ("--sync-source", "eu/es/vc", "--sync-source", "eu/es/vc/emshi")
RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
TAX_ENTRY = {"code": "String", "rate": RATE}
# The facts of the operations whose forbids the tree declares, in the shape the Cedar generator reads.
FACTS = {
    "tax_policy_propose": {"facts": {"policy": "String"}},
    "tax_policy_propose.taxes": {
        "facts": TAX_ENTRY,
        "also": ["tax_policy_draft.taxes"],
    },
    "tax_policy_draft.taxes": {"facts": TAX_ENTRY},
    "procedure_define": {
        "facts": {
            "decision_deadline": {"type": "Period", "optional": True},
            "longer_deadline_legislation": {"type": "String", "optional": True},
        }
    },
    "document_share": {"facts": {"basis": {"type": "String", "optional": True}}},
}

PATHS = """\
layers eu → eu/es → eu/es/vc → eu/es/vc/emshi → eu/es/vc/46250-valencia
layer paths:
  data_protection: eu → eu/es → eu/es/vc/46250-valencia
  language: eu/es → eu/es/vc
  local_regime: eu/es → eu/es/vc → eu/es/vc/46250-valencia
  procedure: eu/es → eu/es/vc/46250-valencia
  register: eu/es
  tax: eu/es → eu/es/vc/46250-valencia
  water: eu/es/vc/emshi"""

TAX_VIEW = """\
tax, as it applies in eu/es/vc/46250-valencia, path eu/es → eu/es/vc/46250-valencia:
  capital                   setting = true                                        eu/es → eu/es/vc/46250-valencia  TRLRHL Art. 72.3
  surface_public_transport  setting = true                                        eu/es → eu/es/vc/46250-valencia  TRLRHL Art. 72.3
  services_beyond_art26     setting overridable = false                           eu/es                            TRLRHL Art. 72.3
  rustic_over_80pct         setting overridable = false                           eu/es                            TRLRHL Art. 72.3
  ibi_rate_required         forbid                                                eu/es                            TRLRHL Art. 72
  ibi_urban_range           forbid                                                eu/es                            TRLRHL Art. 72.1, 72.3
  ibi_urban_cap             forbid                                                eu/es                            TRLRHL Art. 72.3
  ibi_rustic_cap            forbid                                                eu/es                            TRLRHL Art. 72.3
  ibi_bonus                 helper                                                eu/es → eu/es/vc/46250-valencia  TRLRHL Art. 74
  large_family_bonus        setting = {"general": 60000000, "special": 90000000}  eu/es/vc/46250-valencia"""

# §5.8: the cap forbid with València's settings folded in (capital and surface transport are true there).
IBI_URBAN_CAP = """\
@id("eu/es/tax/ibi_urban_cap")
@legislation("TRLRHL Art. 72.3")
forbid (principal,
        action in [Realm::Action::"tax_policy_propose.taxes",
                   Realm::Action::"tax_policy_draft.taxes"],
        resource)
  when { context.facts.code == "ibi_urban"
         && context.facts has rate
         && context.facts.rate > 1100000
              + (if true then 70000 else 0)
              + (if true then 70000 else 0)
              + (if false then 60000 else 0) };"""


@pytest.fixture
def tree(valencia_repo):
    return valencia_repo / "legislation"


def resolve(tree, *args, realm=VALENCIA):
    command = ["resolve", str(tree), "--realm", realm, *args]
    return CliRunner().invoke(legislation_app, command)


def section(output: str, title: str) -> str:
    """The block of ``output`` that starts with the line that starts with ``title``, up to the next blank line."""
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(title))
    end = next((i for i in range(start, len(lines)) if not lines[i]), len(lines))
    return "\n".join(lines[start:end])


def codex(output: str) -> str:
    return re.match(r"codex (sha256:[0-9a-f]{64})\n", output).group(1)


def test_it_prints_valencias_layer_paths_from_the_trees_records(tree):
    out = resolve(tree, *SYNC_SOURCES)
    assert out.exit_code == 0, out.output
    assert PATHS in out.output


def test_it_prints_the_flattened_view_of_tax_as_valencia_runs_it(tree):
    out = resolve(tree, *SYNC_SOURCES)
    assert section(out.output, "tax, as it applies") == TAX_VIEW


def test_a_domain_only_a_sync_source_fills_still_shows_in_the_view(tree):
    out = resolve(tree, *SYNC_SOURCES)
    assert (
        section(out.output, "water, as it applies")
        .splitlines()[0]
        .endswith("path eu/es/vc/emshi:")
    )
    assert "on_tax_policy_propose  hook fixed" in out.output


def test_the_codex_id_does_not_depend_on_the_order_the_sync_sources_are_given_in(tree):
    emshi_first = ("--sync-source", "eu/es/vc/emshi", "--sync-source", "eu/es/vc")
    first = resolve(tree, *SYNC_SOURCES)
    again = resolve(tree, *emshi_first)
    assert codex(first.output) == codex(again.output)
    without = resolve(tree, "--sync-source", "eu/es/vc")
    assert codex(without.output) != codex(first.output)


def test_records_in_files_give_the_same_result_as_the_trees_releases(tree, tmp_path):
    at = commit_of(tree, "HEAD")
    records = []
    for source in ("eu/es/vc", "eu/es/vc/emshi"):
        file = tmp_path / (source.replace("/", "-") + ".json")
        file.write_text(json.dumps(tree_record(tree, at, source)))
        records += ["--record", str(file)]
    from_files = resolve(tree, *records)
    assert from_files.exit_code == 0, from_files.output
    assert from_files.output == resolve(tree, *SYNC_SOURCES).output


def test_a_record_without_commits_reads_every_file_at_the_given_commit(tree, tmp_path):
    at = commit_of(tree, "HEAD")
    layers = tree_record(tree, at, "eu/es/vc")["layers"]
    file = tmp_path / "cv.json"
    file.write_text(
        json.dumps(
            {
                "source": "eu/es/vc",
                "layers": [
                    {k: v for k, v in layer.items() if k != "commit"}
                    for layer in layers
                ],
            }
        )
    )
    out = resolve(tree, "--record", str(file), "--commit", at)
    assert out.exit_code == 0, out.output
    assert "tax: eu/es → eu/es/vc/46250-valencia" in out.output


def test_the_realms_own_layer_is_its_folder_at_the_commit_unless_a_file_gives_it(
    tree, tmp_path
):
    at = commit_of(tree, "HEAD")
    own = layer_at(tree, at, VALENCIA)
    del own["chapters"]["tax"]
    file = tmp_path / "own.json"
    file.write_text(json.dumps(own))
    out = resolve(tree, *SYNC_SOURCES, "--own", str(file))
    assert out.exit_code == 0, out.output
    assert "  tax: eu/es\n" in out.output
    assert "large_family_bonus" not in out.output
    assert codex(out.output) != codex(resolve(tree, *SYNC_SOURCES).output)


def test_with_a_fact_schema_it_prints_the_cedar_of_5_8(tree, tmp_path):
    file = tmp_path / "facts.json"
    file.write_text(json.dumps(FACTS))
    out = resolve(tree, *SYNC_SOURCES, "--facts", str(file))
    assert out.exit_code == 0, out.output
    cedar = out.output.split("\nCedar:\n")[1]
    assert section(cedar, '@id("eu/es/tax/ibi_urban_cap")') == IBI_URBAN_CAP


def test_without_a_fact_schema_it_says_why_there_is_no_cedar(tree):
    out = resolve(tree, *SYNC_SOURCES)
    assert out.output.endswith(
        "Cedar:\nnot generated: --facts FILE gives the fact schema the generator reads\n"
    )


def test_a_record_whose_checksum_the_files_do_not_match_is_refused(tree, tmp_path):
    at = commit_of(tree, "HEAD")
    record = tree_record(tree, at, "eu/es/vc")
    record["layers"][1]["chapters"]["tax"] = "sha256:" + "0" * 64
    file = tmp_path / "cv.json"
    file.write_text(json.dumps(record))
    out = resolve(tree, "--record", str(file))
    assert out.exit_code == 1
    assert "eu/es/tax.py at commit" in out.output and "not the sha256:000" in out.output


def test_a_chapter_the_realm_writes_badly_is_refused_with_its_file(valencia_copy, git):
    (valencia_copy / "legislation" / VALENCIA / "tax.py").write_text("import os\n")
    git(valencia_copy, "commit", "-q", "-a", "--no-verify", "-m", "a bad chapter")
    out = resolve(valencia_copy / "legislation", *SYNC_SOURCES)
    assert out.exit_code == 1
    assert f"{VALENCIA}/tax.py" in out.output and "1 refused" in out.output


def test_a_sync_source_with_no_releases_is_refused(tree):
    out = resolve(tree, "--sync-source", "nowhere")
    assert out.exit_code == 1
    assert "have no releases.json at commit" in out.output


def test_a_realm_the_tree_has_no_folder_for_is_refused(tree):
    out = resolve(tree, *SYNC_SOURCES, realm="eu/es/vc/46250-valencai")
    assert out.exit_code == 1
    assert "eu/es/vc/46250-valencai has no files at commit" in out.output


@pytest.mark.parametrize("option", ["--own", "--facts"])
def test_a_file_that_is_not_json_is_refused_with_its_name(tree, tmp_path, option):
    file = tmp_path / "broken.json"
    file.write_text("{")
    out = resolve(tree, *SYNC_SOURCES, option, str(file))
    assert out.exit_code == 1 and f"{file} is not valid JSON" in out.output


def test_an_empty_list_of_releases_is_refused(valencia_copy, git):
    emshi = valencia_copy / "legislation/eu/es/vc/emshi/releases.json"
    emshi.write_text("[]")
    git(valencia_copy, "commit", "-q", "-a", "--no-verify", "-m", "no releases")
    out = resolve(valencia_copy / "legislation", *SYNC_SOURCES)
    assert out.exit_code == 1
    assert "eu/es/vc/emshi/releases.json is a list of releases" in out.output


@pytest.mark.parametrize(
    "commit", ["--output=/dev/null", "HEAD", "main", "398ca63"], ids=str
)
def test_a_record_may_name_a_commit_only_by_its_40_hex_digits(tree, tmp_path, commit):
    record = tree_record(tree, commit_of(tree, "HEAD"), "eu/es/vc")
    record["layers"][1]["commit"] = commit
    file = tmp_path / "cv.json"
    file.write_text(json.dumps(record))
    out = resolve(tree, "--record", str(file))
    assert out.exit_code == 1
    assert f"commit {commit!r} is not the 40 hex digits of a git commit" in out.output


def test_a_release_in_the_tree_is_held_to_the_layout_of_the_releases(
    valencia_copy, git
):
    file = valencia_copy / "legislation/eu/es/vc/emshi/releases.json"
    releases = json.loads(file.read_text())
    releases[-1]["commit"] = "HEAD"
    file.write_text(json.dumps(releases))
    git(valencia_copy, "commit", "-q", "-a", "--no-verify", "-m", "a release by name")
    out = resolve(valencia_copy / "legislation", *SYNC_SOURCES)
    assert out.exit_code == 1
    assert (
        "eu/es/vc/emshi/releases.json, newest release: commit is the 40 hex digits"
        in out.output
    )


def test_a_chapter_that_is_not_utf8_is_refused_with_its_name(valencia_copy, git):
    (valencia_copy / "legislation" / VALENCIA / "tax.py").write_bytes(b"# \xff\n")
    git(valencia_copy, "commit", "-q", "-a", "--no-verify", "-m", "not UTF-8")
    out = resolve(valencia_copy / "legislation", *SYNC_SOURCES)
    assert out.exit_code == 1 and f"{VALENCIA}/tax.py is not UTF-8" in out.output


def test_a_commit_git_does_not_know_is_refused(tree):
    out = resolve(tree, *SYNC_SOURCES, "--commit", "no-such-branch")
    assert out.exit_code == 1


def test_the_tree_is_read_at_the_commit_and_not_from_the_working_copy(valencia_copy):
    tree = valencia_copy / "legislation"
    (tree / VALENCIA / "tax.py").write_text("not a chapter\n")
    out = resolve(tree, *SYNC_SOURCES)
    assert out.exit_code == 0, out.output
    assert TAX_VIEW in out.output


def test_it_runs_git_and_nothing_else(tree, monkeypatch):
    ran = []
    real = subprocess.run

    def spy(command, *args, **kw):
        ran.append(command[0])
        return real(command, *args, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    assert resolve(tree, *SYNC_SOURCES).exit_code == 0
    assert set(ran) == {"git"}
