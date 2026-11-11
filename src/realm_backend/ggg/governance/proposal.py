from ic_python_db import Boolean, Integer, ManyToOne, OneToMany, String

from core.db import Json, Row
from core.ratio import ONE, PERCENT, upgrade_call


class Proposal(Row):
    __alias__ = "id"
    # 2: threshold and quorum in hundred-millionths, not whole percents; 3: the rule is stored; 4: tags, majority_of_cast,
    # first_vote
    __version__ = 4
    id = String()
    title = String(default="")
    description = String(default="")
    kind = String(default="")  # what the proposal is and what it decides, in the realm's words (GGG: kind, matter)
    matter = String(default="")
    proposer = String(default="")
    on_behalf_of = String(default="")
    filed_under = String(default="")  # the power of attorney a delegate filed it under
    prepared_by = String(default="")  # the delegate that prepared it, when the proposer approved it from a pending action
    department = ManyToOne("Department", "proposals")
    action = Json(None)  # {"op": name, "args": {...}} replayed once accepted
    # draft | voting | accepted | execution_scheduled | rejected | expired | executed | failed | withdrawn
    status = String(default="voting", indexed=True)
    opens_at = Integer(default=0)
    deadline = Integer(default=0)
    effective_at = Integer(default=0)  # seconds; once accepted, the action runs then. 0: on acceptance
    # The rule that decides it (ggg I-GOV-3), fixed at creation: the yes votes exceed (realm-wide) or reach (a department)
    # ``threshold`` of the votes cast or of the eligible voters (``threshold_of``), with ``quorum`` of them voting. A
    # department's threshold is a share of its policy's n seats (``tally["seats"]``), whoever fills them.
    threshold = Integer(default=ONE // 2)
    quorum = Integer(default=0)
    threshold_of = String(default="cast")  # cast | eligible
    casting_vote = String(default="")  # the scope's casting member when the vote opened: the realm's, or a department's head
    majority_of_cast = Boolean(default=False)  # a department that counts the votes cast decides it (ggg I-GOV-3, I-GOV-14)
    first_vote = Json(None)  # the count of a first vote that tied; ``tally`` is then the second vote's
    tally = Json({})
    result = Json(None)
    links = Json([])  # [{"kind": "replaces" | "relates_to", "proposal": id}], fixed at creation like the action
    tags = Json([])  # the proposer's own words for what it is about, replaced only while a draft (ggg I-GOV-15)
    replaced_by = String(default="")  # the one proposal that replaces this withdrawn one
    votes = OneToMany("Vote", "proposal")

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("tags", [])  # a Json field a stored row lacks reads as the text of its default
        obj.setdefault("first_vote", None)
        # quorum_percent, the old field, shows a row still in whole percents: a row without it is converted already.
        if "quorum_percent" in obj:
            obj["quorum"] = obj.pop("quorum_percent") * PERCENT
            obj["threshold"] = obj.get("threshold", 50) * PERCENT
            # The action replays when the vote ends, with the arguments the proposal was filed with.
            if obj.get("action"):
                obj["action"] = upgrade_call(obj["action"])
        # A department's vote took m approvals: its rule is m of the n seats of its policy, which is the department's
        # policy now. A realm-wide one took the realm's threshold and quorum over the votes cast, which the row already
        # holds, and no casting vote.
        tally = obj.get("tally") or {}
        if "required" in tally:
            from ggg import Department

            department = Department.load(obj["department"]) if obj.get("department") else None
            n = max(int(((department.policy if department else None) or {}).get("n", 1)), tally["required"])
            obj["threshold"], obj["threshold_of"] = tally.pop("required") * ONE // n, "eligible"
            tally["seats"] = n
        return obj
