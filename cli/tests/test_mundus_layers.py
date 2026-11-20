"""``realms mundus``: what it decides from the mundus file and the realms-codices tree, without a network."""

import hashlib
import json

import pytest

from realms.cli.commands import mundus_layers as layers
from realms.cli.commands.mundus_layers import Tree


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def tree(tmp_path, git):
    """A realms-codices tree with one released source (``eu``, two releases) and one without releases (a town)."""
    root = tmp_path / "codices"
    eu, town = root / "legislation" / "eu", root / "legislation" / "eu" / "town"
    town.mkdir(parents=True)
    git(root, "init", "-q")
    (eu / "data_protection.py").write_text("# 1.0.0\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "eu 1.0.0")
    first = git(root, "rev-parse", "HEAD")
    (eu / "data_protection.py").write_text("# 1.0.1\n")
    (town / "procedure.py").write_text("# the town\n")
    (town / "calendar.json").write_text("{}\n")
    releases = [{"version": v, "published": p, "effective_from": "2027-01-01", "commit": c,
                 "chapters": {"data_protection": sha(f"# {v}\n".encode())}, "calendar": None, "notes": {}}
                for v, p, c in (("1.0.0", "2026-09-01", first), ("1.0.1", "2026-11-13", "later"))]
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "eu 1.0.1, the town")
    releases[1]["commit"] = git(root, "rev-parse", "HEAD")
    (eu / "releases.json").write_text(json.dumps(releases))
    return Tree(root)


def test_the_realms_are_set_up_each_after_those_it_follows():
    entries = [{"name": "town", "layer": "eu/town", "follows": ["region", "water"]},
               {"name": "water", "layer": "eu/water", "follows": ["region"]},
               {"name": "plain", "codex": "agora"},
               {"name": "region", "layer": "eu"}]
    assert [e["name"] for e in layers.in_order(entries)] == ["region", "water", "town"]


def test_realms_that_follow_each_other_in_a_circle_are_refused():
    with pytest.raises(SystemExit, match="circle"):
        layers.in_order([{"name": "a", "layer": "a", "follows": ["b"]}, {"name": "b", "layer": "b", "follows": ["a"]}])


def test_a_release_is_its_files_at_its_commit_and_the_newest_is_the_default(tree):
    assert tree.release("eu")["version"] == "1.0.1"
    old = tree.release("eu", "1.0.0")
    files = layers.release_files(tree, {**old, "source": "eu"})
    assert files == {("eu", "data_protection.py"): (b"# 1.0.0\n", old["commit"])}
    with pytest.raises(SystemExit, match="no release 9.9.9"):
        tree.release("eu", "9.9.9")


def test_a_release_whose_files_differ_from_its_checksums_is_refused(tree):
    wrong = {**tree.release("eu", "1.0.0"), "source": "eu", "chapters": {"data_protection": sha(b"other")}}
    with pytest.raises(SystemExit, match="is not the file release 1.0.0 lists"):
        layers.release_files(tree, wrong)


def test_a_source_without_releases_is_its_folders_chapters_and_calendar(tree):
    assert tree.release("eu/town") is None
    files = layers.own_files(tree, "eu/town")
    assert set(files) == {("eu/town", "procedure.py"), ("eu/town", "calendar.json")}


def test_a_file_is_fetched_from_github_at_its_commit_unless_it_was_changed_here(tree):
    head = tree.head()
    body = tree.file("eu/town", "procedure.py")
    assert tree.url("eu/town", "procedure.py", body) == f"{layers.RAW}/{head}/legislation/eu/town/procedure.py"
    (tree.root / "legislation" / "eu" / "town" / "procedure.py").write_text("# edited, not committed\n")
    edited = tree.file("eu/town", "procedure.py")
    assert tree.url("eu/town", "procedure.py", edited) == f"{layers.SERVED}/legislation/eu/town/procedure.py"


class Served:
    """The network's outcalls as the mundus answers them while it is held."""

    def __init__(self):
        self.served = {}

    def serve(self, url, body):
        self.served[url] = body


class Town:
    name, source = "town", "eu/town"

    def active_id(self):
        return "sha256:before"


def test_a_change_list_names_the_realms_layer_the_codex_it_replaces_and_every_file_it_serves(tree):
    net = Served()
    args = layers.change_list(net, tree, Town(), layers.own_files(tree, "eu/town"))
    listed = json.loads(net.served[args["url"]])
    assert args["url"].startswith(layers.SERVED) and args["checksum"] == sha(net.served[args["url"]])
    assert (listed["realm"], listed["replaces"], listed["release"]) == ("eu/town", "sha256:before", None)
    assert {e["chapter"] for e in listed["set"]} == {"eu/town/procedure", "eu/town/calendar"}
    for e in listed["set"]:
        assert sha(net.served[e["url"]]) == "sha256:" + e["sha256"] and len(net.served[e["url"]]) == e["size"]
