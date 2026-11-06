"""``realms.legislation``: the library a chapter of a layered codex imports, and the one the tools and
the realm canister run (docs/codices/layered-codex.md §4, §12).

A chapter writes::

    from realms.legislation import above, fixed, extension, overridable, forbid, pct, days, working_days, months, years, Ref

    class Procedure(above("procedure")):
        default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

        @fixed(legislation="Ley 39/2015 Art. 30")
        def working_days_after(self, day, n):
            return self.calendar.add(day, working_days(n))

``__all__`` is exactly what a chapter may import; the loader refuses any other name. The tools and
the runtime use the rest of the module: ``Codex`` (build a codex, ``load`` each chapter top to bottom,
``check`` the settings, run the resolved classes with a ``Context``), ``Calendar``, ``Event``,
``Money`` and the errors.

The module is self-contained: it imports nothing from the CLI or the canister, so the same code runs
on a laptop and in the realm. In the realm, ``Codex.load`` runs inside the subinterpreter sandbox
(§9.3), which supplies the read-only ``ggg`` view. S2 (#484) adds the ``F`` expression language of
forbids and the Cedar it becomes; S3 (#485) the resolver that computes the layers from the realm's
links.
"""

# The ``as`` form marks the re-export; the names a chapter may import are listed in ``__all__`` below.
from realms.legislation.calendar import Calendar as Calendar
from realms.legislation.chapter import RESERVED as RESERVED
from realms.legislation.chapter import Chapter as Chapter
from realms.legislation.chapter import above as above
from realms.legislation.codex import Codex as Codex
from realms.legislation.context import Context as Context
from realms.legislation.context import Denial as Denial
from realms.legislation.context import Effects as Effects
from realms.legislation.context import Event as Event
from realms.legislation.context import Ref as Ref
from realms.legislation.context import plain as plain
from realms.legislation.errors import LegislationError as LegislationError
from realms.legislation.errors import Refused as Refused
from realms.legislation.errors import UnitMismatch as UnitMismatch
from realms.legislation.marks import Forbid as Forbid
from realms.legislation.marks import extension as extension
from realms.legislation.marks import fixed as fixed
from realms.legislation.marks import forbid as forbid
from realms.legislation.marks import overridable as overridable
from realms.legislation.periods import Period as Period
from realms.legislation.periods import days as days
from realms.legislation.periods import months as months
from realms.legislation.periods import working_days as working_days
from realms.legislation.periods import years as years
from realms.legislation.rates import Money as Money
from realms.legislation.rates import pct as pct
from realms.legislation.rates import times as times

# What a chapter may import, and nothing else (S2 adds F, S and when).
__all__ = [
    "Ref",
    "above",
    "days",
    "extension",
    "fixed",
    "forbid",
    "months",
    "overridable",
    "pct",
    "working_days",
    "years",
]
