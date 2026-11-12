from ic_python_db import Boolean, Integer, ManyToOne, OneToMany, String

from core.db import Json, Row, indexed_ids
from core.ratio import ONE, PERCENT, upgrade_call


LISTED = "_tag_lists"  # the tags each proposal is on the lists of, under its id: {"tags": [...]}; none for a proposal with none


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

    # A proposal has several tags, which a field index (one value per row) cannot hold. Each tag has a list of its own in
    # the same store, ``_fi:Proposal:tags:<tag>``, of the ids of the proposals that carry it, and read a page at a time
    # by ``with_tags``. ``list_tags`` writes them, and keeps beside them what each proposal is listed under, so that it
    # can take a proposal off a list when the tags changed without it (a release that kept no lists). Assign the tags
    # only through ``set_tags``.
    def __init__(self, **kw):
        super().__init__(**kw)
        if not kw.get("_loaded"):
            self.list_tags()

    def list_tags(self):
        """Make the lists of the tags say what the tags of this proposal say: take it off the lists it is on and no longer
        belongs to, put it on those it belongs to. Doing it again changes nothing."""
        db, kind = self.db(), self._type
        listed = (db.load(LISTED, self._id) or {}).get("tags", [])
        for tag in set(listed) - set(self.tags):
            db.field_index_remove(kind, "tags", tag, self._id)
        for tag in self.tags:
            db.field_index_add(kind, "tags", tag, self._id)
        if listed != self.tags:
            if self.tags:
                db.save(LISTED, self._id, {"tags": self.tags})
            else:
                db.delete(LISTED, self._id)

    def set_tags(self, tags):
        """Replace the tags, and the lists that find the proposal by them."""
        self.tags = tags
        self.list_tags()

    @classmethod
    def with_tags(cls, tags, start, count):
        """The ids, ascending, of the first ``count`` proposals from id ``start`` that carry at least one of ``tags``.
        One range read of each tag's list: every id of the first ``count`` of the union is among the first ``count`` of
        its own tag's list."""
        found = {i for tag in tags for i in indexed_ids(cls, "tags", tag, start, count)}
        return sorted(found)[:count]

    @classmethod
    def count_with_tags(cls, tags):
        """How many ids the lists of ``tags`` hold: the proposals that carry at least one, or more if one carries several."""
        return sum(cls.db().field_index_count(cls.get_full_type_name(), "tags", tag) for tag in tags)

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
