"""Estado: procedimiento administrativo común. Ley 39/2015."""

from realms.legislation import F, above, fixed, forbid, months, overridable, working_days


class Procedure(above("procedure")):
    uses = []                                             # domains this chapter calls; see layered-codex.md §4.4

    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")   # a GGG Period

    over_six_months = forbid("procedure_define",
        F.decision_deadline.is_some() & (F.decision_deadline > months(6))     
        & F.legislation.is_none(),
        legislation="Ley 39/2015 Art. 21.2")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))    # holidays come from calendar.json
