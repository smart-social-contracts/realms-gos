from ic_python_db import Integer, ManyToOne, OneToMany, String

from db import Json, Row


class Proposal(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    description = String(default="")
    proposer = String(default="")
    on_behalf_of = String(default="")
    department = ManyToOne("Department", "proposals")
    action = Json(None)  # {"op": name, "args": {...}} replayed once accepted
    status = String(default="voting", indexed=True)  # draft | voting | accepted | rejected | expired | executed | failed | withdrawn
    opens_at = Integer(default=0)
    deadline = Integer(default=0)
    threshold = Integer(default=50)  # percent of decisive votes
    quorum_percent = Integer(default=0)
    tally = Json({})
    result = Json(None)
    votes = OneToMany("Vote", "proposal")


class Vote(Row):
    __alias__ = "id"
    id = String()
    proposal = ManyToOne("Proposal", "votes")
    voter = String(indexed=True)
    cast_by = String(default="")
    choice = String(default="")  # yes | no | abstain
    cast_at = Integer(default=0)


class Mandate(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    scope = Json({})
    executor = ManyToOne("Department", "mandates")
    proposal = String(default="")
    status = String(default="active")  # active | completed | revoked | expired
    expires_at = Integer(default=0)
    contracts = OneToMany("Contract", "mandate")


class Contract(Row):
    __alias__ = "id"
    id = String()
    title = String(default="")
    parties = Json([])  # ["Organization:buildco", "Department:infrastructure"]
    mandate = ManyToOne("Mandate", "contracts")
    terms_ref = String(default="")
    terms_hash = String(default="")
    status = String(default="draft")  # draft | active | fulfilled | terminated | disputed
    starts_at = Integer(default=0)
    ends_at = Integer(default=0)
    trades = OneToMany("Trade", "contract")
    ledger_entries = OneToMany("LedgerEntry", "contract")
