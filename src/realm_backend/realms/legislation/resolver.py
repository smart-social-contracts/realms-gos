"""The resolver: a ``Plan`` and the bytes it names become a loaded codex and its flattened view
(layered-codex.md §3.3, §4.4, §9.2 step 5).

``resolve`` loads every chapter of the plan top to bottom, which applies the marks and the extension
merge (``chapter.py``), runs every setting's ``check=``, and then checks the ``uses`` between subject matters:

- a chapter that uses a subject matter the realm has no chapter in, or its own subject matter, is refused;
- subject matters that use each other, directly or through others, are refused as a cycle;
- a ``uses`` chain deeper than ``MAX_DEPTH`` is refused, because a call down it would be stopped.

Every refusal is a ``LegislationError`` (``Refused`` when it is one file's) that says what and where.
The bytes are not checked against their checksums here: the install task does that when it fetches them
(§9.2 steps 3 and 4).
"""

from realms.legislation.codex import Codex
from realms.legislation.context import MAX_DEPTH
from realms.legislation.errors import LegislationError, Refused
from realms.legislation.paths import ARROW
from realms.legislation.view import flatten


class Resolution:
    """A realm's resolved legislation: the ``plan``, the loaded ``codex`` (run its chapters with
    ``codex.instance(subject_matter, codex.context())``) and the flattened view of each subject matter.
    """

    def __init__(self, plan, codex):
        self.plan, self.codex = plan, codex

    def view(self, subject_matter):
        """The flattened view of ``subject_matter`` as it applies in this realm (§5.9)."""
        return flatten(self.codex, subject_matter)

    def views(self):
        return {
            subject_matter: self.view(subject_matter)
            for subject_matter in self.plan.paths
        }


def resolve(plan, chapters, calendars=None, operations=None, modules=None, facts=None):
    """Load ``plan`` into a codex. ``chapters`` maps a checksum to a chapter's source text, ``calendars``
    a checksum to a parsed ``calendar.json``; ``operations``, ``modules`` and ``facts`` are the ``Codex``'s.
    """
    codex = Codex(
        plan.layers,
        realm=plan.realm,
        operations=operations,
        modules=modules,
        facts=facts,
    )
    for entry in plan.calendars:
        codex.calendar_layer(
            entry["source"],
            _bytes(calendars, entry, f"{entry['source']}/calendar.json"),
        )
    for entry in plan.chapters:
        file = f"{entry['source']}/{entry['subject_matter']}.py"
        codex.load(
            entry["source"], entry["subject_matter"], _bytes(chapters, entry, file)
        )
    codex.check()
    check_uses(codex)
    return Resolution(plan, codex)


def _bytes(found, entry, file):
    if not found or entry["sha256"] not in found:
        raise Refused(f"the bytes of {entry['sha256']} are missing", None, file)
    return found[entry["sha256"]]


def check_uses(codex):
    """The ``uses`` between subject matters of a loaded ``codex``, seen from the subject matters: an edge from X to Y
    when any chapter of X declares Y. Refuses an undeclared or missing subject matter, a cycle and a chain deeper than
    ``MAX_DEPTH``. ``resolve`` runs it; so does the realm, which loads a codex chapter by chapter.
    """
    edges = {}
    for subject_matter, chain in codex.chapters.items():
        for cls in chain:
            for used in cls.__uses__:
                _check_used(codex, cls, used)
                edges.setdefault(subject_matter, {}).setdefault(used, cls.__file__)
    longest = {}
    for subject_matter in sorted(edges):
        _longest_chain(edges, subject_matter, [subject_matter], longest)
    deepest = max(longest.values(), key=len, default=[])
    if len(deepest) - 1 > MAX_DEPTH:
        raise LegislationError(
            f"uses chain {ARROW.join(deepest)} is {len(deepest) - 1} calls deep: calls between "
            f"subject matters stop at depth {MAX_DEPTH} ({_declared(edges, deepest)})"
        )


def _check_used(codex, cls, used):
    if used == cls.__subject_matter__:
        raise Refused(f"uses its own subject matter, {used}", None, cls.__file__)
    if used not in codex.chapters:
        raise Refused(
            f"uses {used}, but this realm has no {used} chapter", None, cls.__file__
        )


def _longest_chain(edges, subject_matter, stack, longest):
    """The longest chain of subject matters that starts at ``subject_matter`` (kept in ``longest``, so each subject
    matter is walked once), refusing a cycle. ``stack`` is the chain that led here."""
    if subject_matter not in longest:
        best = [subject_matter]
        for used in sorted(edges.get(subject_matter, {})):
            if used in stack:
                loop = stack[stack.index(used) :] + [used]
                raise LegislationError(
                    f"uses cycle: {ARROW.join(loop)} ({_declared(edges, loop)})"
                )
            below = _longest_chain(edges, used, stack + [used], longest)
            if len(below) + 1 > len(best):
                best = [subject_matter] + below
        longest[subject_matter] = best
    return longest[subject_matter]


def _declared(edges, chain):
    """Which file declares each step of ``chain``."""
    return "; ".join(f"{edges[a][b]} uses {b}" for a, b in zip(chain, chain[1:]))
