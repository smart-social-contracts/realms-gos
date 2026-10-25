from ic_python_db import Integer, ManyToMany, ManyToOne, OneToMany, String

from db import Json, Row


class Case(Row):
    __alias__ = "id"
    id = String()
    court = ManyToOne("Court", "cases")
    plaintiff = String(default="")
    defendant = String(default="")
    judges = ManyToMany(["Judge"], "cases")
    title = String(default="")
    dispute = String(default="")
    status = String(default="filed")  # filed | assigned | in_progress | verdict_issued | appealed | closed | dismissed | transferred
    evidence = Json([])
    filed_at = Integer(default=0)
    closed_at = Integer(default=0)
    verdicts = OneToMany("Verdict", "case")
    appeals = OneToMany("Appeal", "case")
    ledger_entries = OneToMany("LedgerEntry", "case")
