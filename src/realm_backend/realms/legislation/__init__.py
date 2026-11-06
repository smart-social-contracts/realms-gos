"""``realms.legislation``: the library a chapter of a layered codex imports, and the one the tools and
the realm canister run (docs/codices/layered-codex.md §4, §12).

A chapter writes::

    from realms.legislation import above, fixed, extension, overridable, forbid, pct, days, working_days, months, years, Ref

    class Procedure(above("procedure")):
        default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

        @fixed(legislation="Ley 39/2015 Art. 30")
        def working_days_after(self, day, n):
            return self.calendar.add(day, working_days(n))

The tools and the runtime build a ``Codex`` from the realm's layers, ``load`` each chapter top to bottom,
``check`` the settings, and run the resolved classes with a ``Context``.

The module is self-contained: it imports nothing from the CLI or the canister, so the same code runs
on a laptop and in the realm. S2 (#484) adds the ``F`` expression language of forbids and the Cedar it
becomes; S3 (#485) the resolver that computes the layers from the realm's links.
"""

from realms.legislation.calendar import Calendar
from realms.legislation.chapter import RESERVED, Chapter, above
from realms.legislation.codex import Codex
from realms.legislation.context import Context, Denial, Effects, Event, Ref, plain
from realms.legislation.errors import LegislationError, Refused, UnitMismatch
from realms.legislation.marks import Forbid, extension, fixed, forbid, overridable
from realms.legislation.periods import Period, days, months, working_days, years
from realms.legislation.rates import Money, pct, times

__all__ = [
    "Calendar",
    "Chapter",
    "Codex",
    "Context",
    "Denial",
    "Effects",
    "Event",
    "Forbid",
    "LegislationError",
    "Money",
    "Period",
    "RESERVED",
    "Ref",
    "Refused",
    "UnitMismatch",
    "above",
    "days",
    "extension",
    "fixed",
    "forbid",
    "months",
    "overridable",
    "pct",
    "plain",
    "times",
    "working_days",
    "years",
]
