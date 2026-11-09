"""The report of ``realms legislation diff`` (docs/codices/layered-codex.md §12.3): what a change to ``legislation/`` does
to every realm it reaches, between two commits of the tree.

``Comparison`` loads each affected realm's codex at both commits (``legislation_load``), asks the questions of
``legislation_diff`` and runs the realm's test cases; ``render`` writes the result as Markdown for a pull request. A
realm is affected when a source on its layer path changed a chapter or a calendar; a domain, when it changed or calls
(``uses``), however far, one that changed (point 6). Every failure is listed; a warning (an overlap) does not fail.
"""

import difflib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Set, Tuple

from realms.cli.commands.legislation_cases import case_results
from realms.cli.commands.legislation_diff import (
    ActionResult,
    analyse_action,
    folded_line,
)
from realms.cli.commands.legislation_layout import git
from realms.cli.commands.legislation_load import CommitFiles, layers_of, load_realm
from realms.cli.commands.legislation_proof import Solver
from realms.cli.commands.legislation_resolve import view_lines
from realms.legislation import LegislationError
from realms.legislation.view import flatten

MARKER = "<!-- realms-legislation-diff -->"  # a comment on the pull request that holds a report is replaced, not added to


@dataclass
class RealmDiff:
    realm: str
    problems: List[str] = field(
        default_factory=list
    )  # a codex that does not load: nothing else is asked
    domains: List[str] = field(default_factory=list)  # the domains the change reaches
    cedar: List[str] = field(default_factory=list)  # the diff of the generated Cedar
    folded: List[ActionResult] = field(default_factory=list)
    open: List[ActionResult] = field(
        default_factory=list
    )  # the settings left open, for a realm that is a changed source
    views: Dict[str, Tuple[List[str], List[str]]] = field(
        default_factory=dict
    )  # domain: lines before and after
    cases: List[tuple] = field(default_factory=list)

    def failures(self) -> List[str]:
        out = [f"{self.realm}: {p}" for p in self.problems]
        out += [
            f"{self.realm}: {f}" for r in self.folded + self.open for f in r.failures()
        ]
        return out + [
            f"{self.realm}: {which}: {why}" for which, why in self.cases if why
        ]

    def warnings(self) -> List[str]:
        return [
            f"{self.realm}: {r.action}: {f} is covered by the other forbids"
            for r in self.folded
            for f in r.redundant
        ]


def change_list_commit(data: dict) -> str:
    """The one commit the chapters of a change list (§7) are served from, named in their URLs; "" where it sets none."""
    commits = {
        m.group(1)
        for c in data.get("set", [])
        for m in [re.search(r"/([0-9a-f]{40})/", c.get("url", ""))]
        if m
    }
    if len(commits) > 1:
        raise LegislationError(
            f"the chapters of the change list come from {len(commits)} commits: {', '.join(sorted(commits))}"
        )
    return commits.pop() if commits else ""


def changed_files(tree, base: str, head: str) -> Dict[str, Set[str]]:
    """The chapters (``tax``) and calendars (``calendar``) that differ between two commits, by source."""
    out = git(
        tree, "diff", "--name-only", "--relative", base, head, "--", "./"
    ).decode()
    changed: Dict[str, Set[str]] = {}
    for line in out.splitlines():
        path = PurePosixPath(line)
        if path.suffix == ".py" and not path.name.endswith(".cases.json"):
            changed.setdefault(path.parent.as_posix(), set()).add(path.stem)
        elif path.name == "calendar.json":
            changed.setdefault(path.parent.as_posix(), set()).add("calendar")
    return changed


class Comparison:
    """The tree at two commits, the realms the change reaches, and what happens to each."""

    def __init__(self, tree, base, head, operations, facts, ggg, tools):
        self.tree, self.operations, self.facts, self.ggg, self.tools = (
            tree,
            operations,
            facts,
            ggg,
            tools,
        )
        self.old, self.new = CommitFiles(tree, base), CommitFiles(tree, head)
        self.changed = changed_files(tree, base, head)
        self.errors: dict = (
            {}
        )  # which forbids are proved never to error, by Cedar text and action

    def affected(self, only: Optional[List[str]] = None) -> List[str]:
        """The realms (sources at the head commit) with a changed source among their layers, or ``only``."""
        known = self.new.sources()
        if only:
            return [r for r in only if r in known]
        return [r for r in known if any(s in self.changed for s in layers_of(r, known))]

    def realm(self, realm: str) -> RealmDiff:
        result = RealmDiff(realm)
        old, new = self.load(self.old, realm), self.load(self.new, realm)
        result.problems = [
            f"does not load at the base commit: {p}" for p in (old[1] if old else [])
        ]
        result.problems += [f"does not load: {p}" for p in new[1]]
        if result.problems:
            return result
        old, new = old[0] if old else None, new[0]
        result.domains = self.reached(realm, new)
        result.cedar = cedar_diff(old, new)
        actions = sorted(
            {f.actions[0] for codex in (old, new) if codex for f in codex.forbids()}
        )
        if actions:
            result.folded = self.analyse(realm, old, new, actions, generic=False)
            if (
                realm in self.changed
            ):  # the source itself: once more, for every realm that runs it
                result.open = self.analyse(realm, old, new, actions, generic=True)
        result.views = self.views(realm, old, new, result.domains, result.folded)
        result.cases = case_results(
            self.new, new, realm, lambda ggg: self.load(self.new, realm, ggg)[0]
        )
        return result

    def load(self, files, realm: str, ggg=None):
        """The codex of ``realm`` and the refusals met loading it; None where the realm is not a source at that commit."""
        if realm not in files.sources():
            return None
        return load_realm(files, realm, self.operations, self.facts, ggg or self.ggg)

    def reached(self, realm: str, new) -> List[str]:
        """The domains the change reaches in ``realm``: those that changed, and those that call one (``uses``), however
        far."""
        reached = {
            d
            for s in layers_of(realm, self.new.sources())
            for d in self.changed.get(s, ())
        } & set(new.chapters)
        while True:
            more = {
                d
                for d, chain in new.chapters.items()
                if any(set(cls.__uses__) & reached for cls in chain)
            }
            if more <= reached:
                return sorted(reached)
            reached |= more

    def analyse(self, realm, old, new, actions, generic) -> List[ActionResult]:
        cli, cvc5 = self.tools
        solver = Solver(
            cli, cvc5, new.cedar_schema(generic=generic, context={"op": "String"})
        )
        return [
            analyse_action(solver, action, old, new, generic, self.errors)
            for action in actions
        ]

    def views(
        self, realm, old, new, domains, folded
    ) -> Dict[str, Tuple[List[str], List[str]]]:
        views = {}
        for domain in domains:
            views[domain] = (
                self.view(old, domain),
                self.view(new, domain, with_maxima=True),
            )
        return views

    def view(self, codex, domain: str, with_maxima: bool = False) -> List[str]:
        """The flattened view of ``domain`` (§5.9) in ``codex``; with the folded line under each forbid that has one."""
        if codex is None or domain not in codex.chapters:
            return []
        view = flatten(codex, domain)
        lines = view_lines(view["members"])
        if not with_maxima:
            return lines
        cli, cvc5 = self.tools
        solver = Solver(cli, cvc5, codex.cedar_schema(context={"op": "String"}))
        bound = {
            f.id.rsplit("/", 1)[-1]: f for f in codex.forbids() if f.domain == domain
        }
        out = []
        for member, line in zip(view["members"], lines):
            out.append(line)
            forbid = bound.get(member["name"])
            maximum = (
                folded_line(solver, codex, forbid, forbid.actions[0])
                if forbid and member["kind"] == "forbid"
                else None
            )
            if maximum:
                out.append("    " + maximum)
        return out


def cedar_diff(old, new) -> List[str]:
    before = old.cedar().splitlines() if old else []
    return list(
        difflib.unified_diff(
            before, new.cedar().splitlines(), "before", "after", lineterm=""
        )
    )


# ── The Markdown ──────────────────────────────────────────────────────────────────────────────────────────────────


def render(
    base: str, head: str, realms: List[RealmDiff], changed: Dict[str, Set[str]]
) -> str:
    failures = [f for r in realms for f in r.failures()]
    warnings = [w for r in realms for w in r.warnings()]
    out = [MARKER, f"## Legislation analysis: `{base[:7]}` → `{head[:7]}`", ""]
    out.append(
        f"**{len(failures)} failure(s)**, {len(warnings)} warning(s), {len(realms)} realm(s) reached."
        if failures or warnings
        else f"**No failure**, {len(realms)} realm(s) reached."
    )
    out += [
        "",
        *[f"- FAILED {f}" for f in failures],
        *[f"- warning: {w}" for w in warnings],
        "",
    ]
    out += dependencies(realms, changed)
    for realm in realms:
        out += section(realm)
    return "\n".join(out).rstrip() + "\n"


def dependencies(realms: List[RealmDiff], changed: Dict[str, Set[str]]) -> List[str]:
    out = [
        "### Dependencies",
        "",
        "Changed: "
        + (
            ", ".join(
                f"`{s}/{d}`" for s, ds in sorted(changed.items()) for d in sorted(ds)
            )
            or "nothing"
        ),
        "",
    ]
    out += [
        "| realm | domains reached (changed, or calling one that changed) |",
        "|---|---|",
    ]
    out += [
        f"| `{r.realm}` | {', '.join(f'`{d}`' for d in r.domains) or '-'} |"
        for r in realms
    ]
    return out + [""]


def section(realm: RealmDiff) -> List[str]:
    out = [f"### `{realm.realm}`", ""]
    if realm.problems:
        return out + [f"- FAILED {p}" for p in realm.problems] + [""]
    out += action_lines(realm.folded, "settings folded in as this realm has them")
    out += action_lines(
        realm.open, "settings left open: for every realm that runs this source"
    )
    out += cases_lines(realm.cases)
    out += [
        "<details><summary>Flattened view of the changed domains, before and after</summary>",
        "",
    ]
    for domain, (before, after) in realm.views.items():
        out += [
            f"**{domain}**",
            "",
            "Before:",
            "```",
            *before,
            "```",
            "After:",
            "```",
            *after,
            "```",
            "",
        ]
    out += [
        "</details>",
        "",
        "<details><summary>Generated Cedar, diff</summary>",
        "",
        "```diff",
        *(realm.cedar or ["(no change)"]),
        "```",
        "",
        "</details>",
        "",
    ]
    return out


def action_lines(results: List[ActionResult], title: str) -> List[str]:
    if not results:
        return []
    out = [f"#### {title}", ""]
    quiet = [r.action for r in results if r.nothing_to_say()]
    for r in results:
        if r.nothing_to_say():
            continue
        out.append(f"**{r.action}**")
        out += [f"- {s} (proved)" for s in r.statements]
        out += difference("newly forbidden", r.forbidden, bool(r.statements))
        out += difference("newly allowed", r.allowed, bool(r.statements))
        if r.dead.proved:
            out.append("- nothing permitted: YES, no request can be allowed")
        out += [f"- never fires: `{f}`" for f in r.never_fires]
        out += [
            f"- overlap: `{f}` is covered by the other forbids" for f in r.redundant
        ]
        out += [f"- can error: {e}" for e in r.can_error]
        out += [f"- undecided: {u}" for u in r.undecided]
        out.append("")
    if quiet:
        out += [
            "Nothing newly forbidden or allowed, nothing dead, no forbid that never fires, can error or overlaps: "
            + ", ".join(f"`{a}`" for a in quiet)
            + ".",
            "",
        ]
    return out


def difference(kind: str, answer, explained: bool) -> List[str]:
    """The line for newly forbidden (or allowed): none, undecided, or the request that breaks it, unless the statements
    above it already say what the difference is."""
    if answer.proved:
        return [f"- {kind}: none (proved)"]
    if answer.proved is None:
        return [f"- {kind}: undecided"]
    return [] if explained else [f"- {kind}: yes, e.g. `{answer.example}`"]


def cases_lines(cases: List[tuple]) -> List[str]:
    failed = [(which, why) for which, why in cases if why]
    return [
        "#### Test cases",
        "",
        f"{len(cases) - len(failed)} of {len(cases)} pass.",
        *[f"- FAILED {which}: {why}" for which, why in failed],
        "",
    ]
