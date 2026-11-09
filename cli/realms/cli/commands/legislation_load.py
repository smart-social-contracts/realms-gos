"""A realm's codex loaded from a ``legislation/`` tree (docs/codices/layered-codex.md §3, §12.1), from the folder as it
is (``WorkingFiles``) or from a commit (``CommitFiles``, read with git, never from the working copy).

The layers of a source are its ancestor folders that are sources, top to bottom: ``eu/es/vc/46250-valencia`` runs
``eu``, ``eu/es``, ``eu/es/vc`` and itself. The links between realms (§3.3) are not read here, so a body beside the line,
such as EMSHI, is loaded as a realm of its own.
"""

import json
from pathlib import Path
from typing import List

from realms.cli.commands.legislation_layout import bytes_at, names_at, run_git
from realms.legislation import Codex, LegislationError, Refused, subset_ast
from realms.legislation.paths import REALM


class WorkingFiles:
    """The tree as it is in the folder."""

    def __init__(self, tree: Path):
        self.tree = tree

    def sources(self) -> List[str]:
        """Every source id: a folder with a source.json, as ``eu/es/vc``."""
        return sorted(
            p.parent.relative_to(self.tree).as_posix()
            for p in self.tree.rglob("source.json")
        )

    def names(self, source: str) -> List[str]:
        """The names of the files in the source's folder."""
        return sorted(p.name for p in (self.tree / source).iterdir() if p.is_file())

    def text(self, source: str, name: str) -> str:
        return (self.tree / source / name).read_text()


class CommitFiles:
    """The tree at a commit."""

    def __init__(self, tree: Path, commit: str):
        self.tree, self.commit = tree, commit

    def sources(self) -> List[str]:
        listing = run_git(self.tree, "ls-tree", "-r", "--name-only", self.commit, "./")
        if listing.returncode:
            raise LegislationError(
                f"commit {self.commit[:7]} cannot be read: {listing.stderr.decode().strip()}"
            )
        return sorted(
            line[: -len("/source.json")]
            for line in listing.stdout.decode().splitlines()
            if line.endswith("/source.json")
        )

    def names(self, source: str) -> List[str]:
        return sorted(names_at(self.tree, self.commit, source))

    def text(self, source: str, name: str) -> str:
        return bytes_at(self.tree, self.commit, source, name).decode("utf-8")


def layers_of(source: str, known: List[str]) -> List[str]:
    """The source's ancestor sources and itself, top to bottom."""
    parts = source.split("/")
    path = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    return [s for s in path if s in known]


def load_realm(files, realm: str, operations, facts: dict, ggg):
    """The realm's codex with its calendars and chapters loaded from ``files``, and the refusals met on the way, as the
    checker prints them. ``ggg`` is the module a chapter imports ``ggg`` from."""
    layers = layers_of(realm, files.sources())
    codex = Codex(
        layers,
        realm=realm,
        operations=operations,
        modules={"ggg": ggg},
        facts=facts,
    )
    problems = []
    for source in layers:
        if "calendar.json" in files.names(source):  # its shape passed the layout rules
            codex.calendar_layer(
                source, json.loads(files.text(source, "calendar.json"))
            )
    domains = []
    for source in layers:
        for name in files.names(source):
            if name.endswith(".py") and name[:-3] not in domains:
                domains.append(name[:-3])
    for domain in sorted(domains):
        for source in layers:
            if domain + ".py" not in files.names(source) or (
                domain == REALM and source != realm
            ):
                continue  # a realm chapter is the realm's own; the layers above hold theirs for themselves (§3)
            text = files.text(source, domain + ".py")
            try:
                subset_ast.check(text, f"{source}/{domain}.py")
                codex.load(source, domain, text)
            except Refused as e:
                problems.append(str(e))
                break  # the layers below build on this one
    return codex, problems
