"""What the runtime (realms-gos#488) adds to the library: ``Args``, the operation's arguments as a guard reads them;
``self.local_day``, the realm's local day of a timestamp; and the domain boundary (realms-gos#543): other domains
reach a chapter's public members only, and what a method hands back is plain data or the chapter seen through
``Public``."""

import pytest

from realms.legislation import Codex, LegislationError, Money, Ref, days
from realms.legislation.context import Args, Public, crossing

TOWN = "x/town"


def codex(**chapters):
    c = Codex([TOWN], realm=TOWN, operations=["l_act"])
    for domain, text in chapters.items():
        c.load(TOWN, domain, text)
    return c


# ── Args ─────────────────────────────────────────────────────────────────────


def test_args_read_as_attributes_and_as_a_mapping_and_a_missing_argument_reads_none():
    args = Args({"basis": {"legal_basis": "gdpr-6-1-e", "legislation": None}, "action": {"op": "l_act", "args": {}},
                 "tags": ["a", "b"]})
    assert args.basis.legal_basis == "gdpr-6-1-e" and args.basis.legislation is None
    assert args.effective_at is None and args.get("effective_at", 0) == 0
    assert args.action.op == "l_act" and "op" in args.action and args["action"]["op"] == "l_act"
    assert args.tags == ("a", "b") and len(args) == 3 and sorted(args) == ["action", "basis", "tags"]
    assert repr(args.action) == "Args({'op': 'l_act', 'args': {}})"


def test_args_never_change_and_wrap_dicts_only():
    args = Args({"a": 1})
    with pytest.raises(LegislationError, match="never change"):
        args.a = 2
    with pytest.raises(AttributeError):
        args._private
    with pytest.raises(LegislationError, match="wraps a dict"):
        Args(["a"])


# ── self.local_day ───────────────────────────────────────────────────────────


def test_local_day_is_the_realms_and_a_context_without_one_refuses():
    c = codex(procedure="from realms.legislation import above\nclass Procedure(above('procedure')):\n"
                        "    def day(self, at):\n        return self.local_day(at)\n")
    chapter = c.instance("procedure", c.context(local_day=lambda at: f"day-of-{at}"))
    assert chapter.day(7) == "day-of-7"
    with pytest.raises(LegislationError, match="no local day"):
        c.instance("procedure", c.context()).day(7)


# ── The domain boundary (#543) ───────────────────────────────────────────────

ALPHA = """
from realms.legislation import above

class Alpha(above("alpha")):
    uses = ["beta"]

    def probe_self(self):
        return self.legislation.beta.give_self()._secret()

    def probe_undeclared(self):
        return self.legislation.beta.legislation.gamma

    def probe_api(self):
        return self.legislation.beta.ggg

    def probe_method(self):
        return self.legislation.beta.give_method()

    def probe_data(self):
        beta = self.legislation.beta.give_self()
        return beta.name(), beta.rate(), beta.amount(), self.legislation.beta.setting
"""
BETA = """
from realms.legislation import Ref, above, days

class Beta(above("beta")):
    uses = ["gamma"]
    setting = {"a": [1, 2]}

    def give_self(self):
        return self

    def give_method(self):
        return self.name

    def name(self):
        return "beta:" + self.legislation.gamma.name()

    def rate(self):
        return Ref("external", "INE")

    def amount(self):
        return days(30)

    def _secret(self):
        return "the secret"
"""
GAMMA = "from realms.legislation import above\nclass Gamma(above('gamma')):\n    def name(self):\n        return 'gamma'\n"


def alpha():
    c = codex(alpha=ALPHA, beta=BETA, gamma=GAMMA)
    return c.instance("alpha", c.context())


def test_a_method_returning_self_hands_back_the_public_view_only():
    with pytest.raises(LegislationError, match="_secret is private to its chapter"):
        alpha().probe_self()


def test_another_domains_self_api_is_out_of_reach():
    with pytest.raises(LegislationError, match="legislation is not a public member of x/town/beta.py"):
        alpha().probe_undeclared()
    with pytest.raises(LegislationError, match="ggg is not a public member of x/town/beta.py"):
        alpha().probe_api()


def test_a_method_handed_back_is_refused():
    with pytest.raises(LegislationError, match="x/town/beta.py returned method: only plain data crosses"):
        alpha().probe_method()


def test_plain_data_crosses_a_setting_too_and_the_view_is_usable():
    name, rate, amount, setting = alpha().probe_data()
    assert name == "beta:gamma" and rate == Ref("external", "INE") and amount == days(30)
    assert setting == {"a": (1, 2)}


def test_crossing_passes_data_wraps_a_chapter_and_refuses_the_rest():
    c = codex(gamma=GAMMA)
    chapter = c.instance("gamma", c.context())
    assert crossing([1, "a", {"k": days(2)}, Money(100, 2)], "f.py") == [1, "a", {"k": days(2)}, Money(100, 2)]
    assert isinstance(crossing(chapter, "f.py"), Public)
    for value in (Public(chapter), chapter.name, object(), 1.5):
        with pytest.raises(LegislationError, match="only plain data crosses"):
            crossing(value, "f.py")
