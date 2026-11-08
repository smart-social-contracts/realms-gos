"""The resolver: a ``Plan`` and the bytes it names become a loaded codex and its flattened view
(layered-codex.md §3.3, §4.4, §9.2 step 5).

``resolve`` loads every chapter of the plan top to bottom, which applies the marks and the extension
merge (``chapter.py``), runs every setting's ``check=``, and then checks the ``uses`` between domains:

- a chapter that uses a domain the realm has no chapter in, or its own domain, is refused;
- domains that use each other, directly or through others, are refused as a cycle;
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
    ``codex.instance(domain, codex.context())``) and the flattened view of each domain.
    """

    def __init__(self, plan, codex):
        self.plan, self.codex = plan, codex

    def view(self, domain):
        """The flattened view of ``domain`` as it applies in this realm (§5.9)."""
        return flatten(self.codex, domain)

    def views(self):
        return {domain: self.view(domain) for domain in self.plan.paths}


def resolve(plan, chapters, calendars=None, operations=None, modules=None):
    """Load ``plan`` into a codex. ``chapters`` maps a checksum to a chapter's source text, ``calendars``
    a checksum to a parsed ``calendar.json``; ``operations`` and ``modules`` are the ``Codex``'s.
    """
    codex = Codex(plan.layers, realm=plan.realm, operations=operations, modules=modules)
    for entry in plan.calendars:
        codex.calendar_layer(
            entry["source"],
            _bytes(calendars, entry, f"{entry['source']}/calendar.json"),
        )
    for entry in plan.chapters:
        file = f"{entry['source']}/{entry['domain']}.py"
        codex.load(entry["source"], entry["domain"], _bytes(chapters, entry, file))
    codex.check()
    check_uses(codex)
    return Resolution(plan, codex)


def _bytes(found, entry, file):
    if not found or entry["sha256"] not in found:
        raise Refused(f"the bytes of {entry['sha256']} are missing", None, file)
    return found[entry["sha256"]]


def check_uses(codex):
    """The ``uses`` between domains of a loaded ``codex``, seen from the domains: an edge from X to Y when any
    chapter of X declares Y. Refuses an undeclared or missing domain, a cycle and a chain deeper than
    ``MAX_DEPTH``. ``resolve`` runs it; so does the realm, which loads a codex chapter by chapter.
    """
    edges = {}
    for domain, chain in codex.chapters.items():
        for cls in chain:
            for used in cls.__uses__:
                _check_used(codex, cls, used)
                edges.setdefault(domain, {}).setdefault(used, cls.__file__)
    longest = {}
    for domain in sorted(edges):
        _longest_chain(edges, domain, [domain], longest)
    deepest = max(longest.values(), key=len, default=[])
    if len(deepest) - 1 > MAX_DEPTH:
        raise LegislationError(
            f"uses chain {ARROW.join(deepest)} is {len(deepest) - 1} calls deep: calls between "
            f"domains stop at depth {MAX_DEPTH} ({_declared(edges, deepest)})"
        )


def _check_used(codex, cls, used):
    if used == cls.__domain__:
        raise Refused(f"uses its own domain, {used}", None, cls.__file__)
    if used not in codex.chapters:
        raise Refused(
            f"uses {used}, but this realm has no {used} chapter", None, cls.__file__
        )


def _longest_chain(edges, domain, stack, longest):
    """The longest chain of domains that starts at ``domain`` (kept in ``longest``, so each domain is
    walked once), refusing a cycle. ``stack`` is the chain that led here."""
    if domain not in longest:
        best = [domain]
        for used in sorted(edges.get(domain, {})):
            if used in stack:
                loop = stack[stack.index(used) :] + [used]
                raise LegislationError(
                    f"uses cycle: {ARROW.join(loop)} ({_declared(edges, loop)})"
                )
            below = _longest_chain(edges, used, stack + [used], longest)
            if len(below) + 1 > len(best):
                best = [domain] + below
        longest[domain] = best
    return longest[domain]


def _declared(edges, chain):
    """Which file declares each step of ``chain``."""
    return "; ".join(f"{edges[a][b]} uses {b}" for a, b in zip(chain, chain[1:]))
