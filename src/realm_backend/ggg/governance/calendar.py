from ic_python_db import Integer, OneToOne, String

from core.db import Json, Row


class Calendar(Row):
    __alias__ = "id"
    id = String(default="1")
    realm = OneToOne("Realm", "calendar")
    epoch = Integer(default=0)
    fiscal_period = Integer(default=365 * 86400)
    voting_window = Integer(default=7 * 86400)
    codex_release_cycle = Integer(default=0)
    benefit_cycle = Integer(default=30 * 86400)
    service_payment_cycle = Integer(default=30 * 86400)
    license_review_cycle = Integer(default=2 * 365 * 86400)
    custom_cycles = Json({})
