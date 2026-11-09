"""Regression tests: every sandbox-escape attempt the reviewers of realms-gos#509 found against the
``realms.legislation`` loader must be refused, and none of them may run.

Several chapters of one codex share a subinterpreter, so a chapter must not be able to reach the host,
the loader's frame, or another chapter's classes and values. Each attempt below is a chapter the loader
must refuse while checking the readable subset, before any of its code runs. ``tests/deployed`` runs the
same catalogue inside the realm wasm and checks that no side effect was left behind. The attempts are
kept verbatim from the reviewers' scripts so a regression reproduces exactly what they found.
"""

import pytest

from realms.legislation import Codex, LegislationError, Refused

ES = """
from realms.legislation import above, fixed, overridable, working_days, pct
class Procedure(above("procedure")):
    bonus = fixed({"general": pct("60")}, legislation="TRLRHL Art. 74")
    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))
    @overridable(legislation="x")
    def on_codex_install(self, event):
        return None
"""
H = "from realms.legislation import above, days, Ref\n"
T = "class Procedure(above('procedure')):\n    pass\n"

# Each attempt -> a fragment of its expected refusal. The loader refuses it before it runs.
ATTACKS = {
    # A match-class pattern reads attributes named by its keyword constants (MATCH_CLASS), reaching
    # the real sys, basilisk.ic and ggg; the dunder name hides in co_consts, not co_names.
    "match_globals": H
    + "match days:\n    case object(__globals__=g):\n        pass\n"
    + T,
    "match_in_method": H
    + "class Procedure(above('procedure')):\n"
    + "    def on_codex_install(self, event):\n"
    + "        match days:\n            case object(__globals__=g):\n                g['x'] = 1\n",
    # A generator's frame walks f_back to the loader's frame, giving the real builtins and the Codex.
    "gen_frame": H
    + "box = []\n"
    + "def gen():\n    f = box[0].gi_frame\n    yield 1\n"
    + "box.append(gen())\nnext(box[0])\n"
    + T,
    # keep(...) set any dunder on any class; wiping __members__ overrides a fixed method.
    "meta_keep": H
    + "type(above('procedure')).keep(above('procedure'), members={})\n"
    + "class Procedure(above('procedure')):\n    def working_days_after(self, day, n):\n        return 'OVERRIDDEN'\n",
    # Monkeypatching an SDK class (STORE_ATTR on a type) disabled the marks for later loads.
    "meta_rule_patch": H + "type(above('procedure')).rule = lambda m, u, r: None\n" + T,
    "sdk_patch_ref": H
    + "Ref.to_json = lambda self: {'kind': 'pwned', 'id': self.id}\n"
    + T,
    # dict.update on a Frozen value an upper layer set fixed, at module level: refused at load.
    "frozen_bypass": H + "dict.update(above('procedure').bonus, general=1)\n" + T,
    # str.format and the format-spec conversion reach dunders named in the template.
    "format_str": H + "S = '{0.__globals__[x]}'.format(days)\n" + T,
    # Exception and frame attributes spelled out.
    "traceback": H
    + "def f(self):\n    try:\n        self.now()\n    except Exception as e:\n        return e.__traceback__\n"
    + "class Procedure(above('procedure')):\n    g = f\n",
    "print_self": H + "X = print.__self__\n" + T,
    "getattr_computed": H + "X = getattr(days, '_' + '_globals__')\n" + T,
    # Imports of the library internals and of ggg with no view.
    "import_codex": "from realms.legislation import Codex\n" + H + T,
    "ggg_noview": "import ggg\n" + H + T,
    "init_subclass": H
    + "class Procedure(above('procedure')):\n    def __init_subclass__(cls): pass\n",
    "metaclass_sub": H
    + "class M(type(above('procedure'))): pass\n"
    + "class Procedure(above('procedure'), metaclass=M):\n    pass\n",
}


# These two load (an ordinary method body), but the hook fails when it runs because the value it
# reaches for is frozen, so the shared calendar and the event a layer above set are never changed.
RUNTIME = {
    "event_frozen_bypass": H
    + "class Procedure(above('procedure')):\n"
    + "    def on_codex_install(self, event):\n        dict.update(event.result, codex='HACKED')\n",
    "calendar_mutation": H
    + "class Procedure(above('procedure')):\n"
    + "    def on_codex_install(self, event):\n        self.calendar.weekend.add('mon')\n",
}


def _codex():
    c = Codex(
        ["eu/es", "x/town"],
        realm="x/town",
        operations=["codex_install", "notification_send"],
    )
    c.load("eu/es", "procedure", ES)
    return c


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_the_reviewers_escape_attempts_are_refused_before_running(name):
    with pytest.raises(Refused):
        _codex().load("x/town", "procedure", ATTACKS[name])


def test_a_refused_attempt_leaves_no_chapter_behind():
    c = _codex()
    for text in ATTACKS.values():
        with pytest.raises(Refused):
            c.load("x/town", "procedure", text)
    assert [cls.__source__ for cls in c.chapters["procedure"]] == ["eu/es"]


@pytest.mark.parametrize("name", sorted(RUNTIME))
def test_a_runtime_mutation_fails_and_changes_no_shared_state(name):
    from realms.legislation import Event

    c = _codex()
    c.calendar_layer("eu/es", {"weekend": ["sat", "sun"], "years": {"2027": []}})
    chapter = c.load("x/town", "procedure", RUNTIME[name])(c.context())
    result = {"codex": "C-1"}
    with pytest.raises((LegislationError, TypeError, AttributeError)):
        chapter.on_codex_install(Event("a", {}, result, 0))
    assert c.calendar.weekend == frozenset({"sat", "sun"})  # unchanged
    assert result == {"codex": "C-1"}  # the event a layer above set is unchanged


# A class body may read attributes now, for the F trees of forbids (realms-gos#484). The dunder names
# a class body may hold are the ones CPython writes there, as names: read as attributes they reach
# another class. ``above('procedure').__annotations__.update(...)`` wrote into Spain's chapter class.
CLASS_BODY_ATTACKS = {
    "upper_annotations_update": H
    + "class Procedure(above('procedure')):\n"
    + "    v = above('procedure').__annotations__.update({'planted': 1})\n",
    "sdk_annotations_update": H
    + "class Procedure(above('procedure')):\n"
    + "    v = days.__annotations__.update({'n': 1})\n",
    "module_read": H
    + "class Procedure(above('procedure')):\n    v = above.__module__\n",
    "private_attribute": H
    + "class Procedure(above('procedure')):\n    v = days._private\n",
    "method_dunder": H
    + "class Procedure(above('procedure')):\n    v = Ref('a', 'b').__class__\n",
}


@pytest.mark.parametrize("name", sorted(CLASS_BODY_ATTACKS))
def test_a_class_body_reads_no_private_or_dunder_attribute(name):
    from realms.legislation import subset_ast

    c = _codex()
    upper = c.chapters["procedure"][0]
    with pytest.raises(Refused, match="no private or dunder attribute|no dunder names"):
        c.load("x/town", "procedure", CLASS_BODY_ATTACKS[name])
    with pytest.raises(Refused, match="no private or dunder attribute|no dunder names"):
        subset_ast.check(CLASS_BODY_ATTACKS[name], "x/town/procedure.py")
    assert "__annotations__" not in vars(upper) and "planted" not in str(vars(upper))
    from realms.legislation import days

    assert not getattr(days, "__annotations__", {})


# The second review of #546: a lower layer must not be able to change an upper layer's forbid. The
# list of F.code.in_([...]) was a Python list inside the tree, so `.remove()` on it passed every
# check; the nodes hold tuples now, and every node and Period refuses attribute assignment.
ES_FORBIDS = (
    "from realms.legislation import F, above, forbid, months, pct\n"
    "class Procedure(above('procedure')):\n"
    "    codes = forbid('codex_install', F.code.in_(['a', 'b']) & F.rate.is_none(), legislation='x')\n"
    "    deadline = forbid('codex_install', F.d.is_some() & (F.d > months(6)), legislation='x')\n"
)
UPPER_FORBID_ATTACKS = {
    "class body: remove a code from the upper forbid's list": H
    + "class Procedure(above('procedure')):\n"
    + "    v = next(iter(above('procedure').codes.expression.children)).values.value.remove('b')\n",
    "lambda: remove a code": H
    + "class Procedure(above('procedure')):\n"
    + "    v = (lambda: above('procedure').codes.expression.children[0].values.value.remove('b'))()\n",
    "Period count via a local": H
    + "class Procedure(above('procedure')):\n"
    + "    def _h(self):\n        p = above('procedure').deadline.expression.children[1].right.value\n        p.count = 600\n"
    + "    x = _h(None)\n",
    "Forbid.expression via a local": H
    + "class Procedure(above('procedure')):\n"
    + "    def _h(self):\n        f = above('procedure').deadline\n        f.expression = False\n    x = _h(None)\n",
    "Expr node via a local": H
    + "class Procedure(above('procedure')):\n"
    + "    def _h(self):\n        n = above('procedure').deadline.expression\n        n.children = ()\n    x = _h(None)\n",
    "Literal value via a local": H
    + "class Procedure(above('procedure')):\n"
    + "    def _h(self):\n        p = above('procedure').deadline.expression.children[1].right\n        p.value = None\n    x = _h(None)\n",
}


def _forbid_codex():
    c = Codex(["eu/es", "x/town"], realm="x/town", operations=["codex_install"])
    c.load("eu/es", "procedure", ES_FORBIDS)
    return c


@pytest.mark.parametrize("name", sorted(UPPER_FORBID_ATTACKS))
def test_a_lower_layer_cannot_change_an_upper_forbid(name):
    c = _forbid_codex()
    upper = c.chapters["procedure"][0]
    before = repr(upper.codes.expression), repr(upper.deadline.expression)
    with pytest.raises(Refused):
        c.load("x/town", "procedure", UPPER_FORBID_ATTACKS[name])
    assert (repr(upper.codes.expression), repr(upper.deadline.expression)) == before
    assert upper.codes.expression.children[0].values.value == ("a", "b")


def test_a_hook_cannot_change_an_upper_forbid_at_run_time():
    from realms.legislation import Event

    c = _forbid_codex()
    cls = c.load(
        "x/town",
        "procedure",
        H + "class Procedure(above('procedure')):\n"
        "    def on_codex_install(self, event):\n"
        "        above('procedure').codes.expression.children[0].values.value.remove('b')\n",
    )
    with pytest.raises((LegislationError, AttributeError)):
        cls(c.context()).on_codex_install(Event("a", {}, {}, 0))
    assert c.chapters["procedure"][0].codes.expression.children[0].values.value == (
        "a",
        "b",
    )


# The third review of #546: Cedar injection through a setting. The builtin ``type`` in its
# three-argument form built an int or str subclass with a ``__str__`` or ``__iter__`` of its own
# (the dunder spelled as U + U + 'str' + U + U), S1 accepted it as data, and the generator wrote
# ``str(value)`` into the Cedar: ``rate > 1 && false``, or a whole policy under Spain's @id. A
# chapter's ``type`` now takes one argument, and every door of the SDK and the generator takes the
# exact types only.
INJECTION_SPAIN = (
    "from realms.legislation import F, S, above, forbid, overridable, pct\n"
    "class Tax(above('tax')):\n"
    "    cap = overridable(pct('1.30'), legislation='x')\n"
    "    name = overridable('ibi_urban', legislation='x')\n"
    "    ibi_urban_cap = forbid('tax_policy_propose', (F.code == S.name) & F.rate.is_some() & (F.rate > S.cap),"
    " each='taxes', legislation='x')\n"
)
INJECTIONS = {
    "int subclass with its own __str__, as a setting": "from realms.legislation import above\nU = '_'\nclass Tax(above('tax')):\n"
    "    cap = type('E', (int,), {U + U + 'str' + U + U: lambda self: '1 && false'})(1300000)\n",
    "int subclass, a whole policy injected": "from realms.legislation import above\nU = '_'\nclass Tax(above('tax')):\n"
    "    cap = type('E', (int,), {U + U + 'str' + U + U: lambda self: '0 && false };\\nforbid (principal, action, resource) when { false'})(1300000)\n",
    "str subclass with its own __iter__, as a setting": "from realms.legislation import above\nU = '_'\nclass Tax(above('tax')):\n"
    "    name = type('E', (str,), {U + U + 'iter' + U + U: lambda self: iter('nothing')})('ibi_urban')\n",
    "Period count from an int subclass": "from realms.legislation import above, months\nU = '_'\nclass Tax(above('tax')):\n"
    "    cap = months(type('E', (int,), {U + U + 'str' + U + U: lambda self: '6 && false'})(6))\n",
    "own literal: str subclass in a forbid": "from realms.legislation import F, above, forbid\nU = '_'\nclass Tax(above('tax')):\n"
    "    mine = forbid('tax_policy_propose', F.code == type('E', (str,), {U + U + 'iter' + U + U: lambda self: iter('x')})('y'), each='taxes')\n",
    "own literal: int subclass injecting Spain's @id": "from realms.legislation import F, above, forbid\nU = '_'\nclass Tax(above('tax')):\n"
    "    mine = forbid('tax_policy_propose', F.rate.is_some() & (F.rate > type('E', (int,), {U + U + 'str' + U + U: "
    "lambda self: '0 };\\n@id(\"eu/es/tax/ibi_urban_cap\")\\nforbid (principal, action, resource) when { false'})(5)), each='taxes')\n",
    "the metaclass called directly": "from realms.legislation import above\nclass Tax(above('tax')):\n"
    "    x = (lambda: type(above('tax'))('Tax', (above('tax'),), {'capital': True}))()\n",
    "legislation= from a str subclass": "from realms.legislation import above, overridable\nU = '_'\nclass Tax(above('tax')):\n"
    "    capital = overridable(True, legislation=type('E', (str,), {U + U + 'iter' + U + U: lambda self: iter('x')})('y'))\n",
}
RATE = {"type": "Long", "optional": True, "min": 0, "max": 100_000_000}
TAX_FACTS = {
    "tax_policy_propose.taxes": {
        "facts": {"code": "String", "rate": RATE},
        "also": ["tax_policy_draft.taxes"],
    },
    "tax_policy_draft.taxes": {"facts": {"code": "String", "rate": RATE}},
}


@pytest.mark.parametrize("name", sorted(INJECTIONS))
def test_no_cedar_is_injected_through_a_setting_or_a_literal(name):
    c = Codex(
        ["eu/es", "x/town"],
        realm="x/town",
        operations=["tax_policy_propose", "tax_policy_draft"],
        facts=TAX_FACTS,
    )
    c.load("eu/es", "tax", INJECTION_SPAIN)
    before = c.cedar()
    with pytest.raises(
        Refused,
        match="type\\(name, bases, dict\\) is not allowed in a chapter|no nested classes|type\\(\\) of a class is not allowed",
    ):
        c.load("x/town", "tax", INJECTIONS[name])
    assert c.cedar() == before and before.count('@id("eu/es/tax/ibi_urban_cap")') == 1
    assert (
        "context.facts.rate > 1300000 };" in before
        and 'context.facts.code == "ibi_urban"' in before
    )


# The security review before wave 3 (realms-gos#512). One instance runs every layer's code of a domain in a call, so
# a name a lower layer stores on ``self`` must never hide a member or the self API the layer above reads.
ES_SELF = """
from realms.legislation import above, fixed, overridable
class Procedure(above("procedure")):
    limit = fixed(5, legislation="x")
    capital = overridable(False, legislation="x")

    @fixed(legislation="x")
    def cap(self):
        return 5

    @overridable(legislation="x")
    def charge(self):
        return self.limit, self.capital, self.cap(), self.approved_by("Junta"), self.running_layer()
"""
UNTOUCHED = (5, False, 5, False, "eu/es")


def _lower(body):
    """The lowest layer's ``charge``: ``body`` runs on ``self``, then the layer above reads from the same instance."""
    return (
        H
        + "class Procedure(above('procedure')):\n    def charge(self):\n        "
        + body
        + "\n        return super().charge()\n"
    )


def _self_codex():
    c = Codex(["eu/es", "x/town"], realm="x/town", operations=["codex_install"])
    c.load("eu/es", "procedure", ES_SELF)
    return c


@pytest.mark.parametrize(
    "store", ["self.limit = 99", "self.capital = True", "self.cap = lambda: 99"]
)
def test_a_lower_layer_cannot_change_what_the_layer_above_reads_from_self(store):
    c = _self_codex()
    chapter = c.load("x/town", "procedure", _lower(store))(c.context())
    with pytest.raises(LegislationError, match="a store on self takes a new name"):
        chapter.charge()
    assert c.chapters["procedure"][0](c.context()).charge() == UNTOUCHED


@pytest.mark.parametrize(
    "store",
    [
        "self.charge = None",  # a member of this chapter
        "self.approved_by = lambda department: True",  # the self API
        "self.running_layer = lambda: 'x/town'",
        "self.now = lambda: 0",
        "self.deny = lambda reason: None",
    ],
)
def test_both_checkers_refuse_a_store_under_a_name_the_chapter_or_the_self_api_defines(
    store,
):
    from realms.legislation import subset_ast

    with pytest.raises(Refused, match="a store on self takes a new name"):
        _self_codex().load("x/town", "procedure", _lower(store))
    with pytest.raises(Refused, match="a store on self takes a new name"):
        subset_ast.check(_lower(store), "x/town/procedure.py")


@pytest.mark.parametrize(
    "name",
    [
        "limit",
        "capital",
        "cap",
        "charge",
        "approved_by",
        "running_layer",
        "now",
        "uses",
    ],
)
def test_an_instance_refuses_a_store_under_any_name_its_classes_define(name):
    c = _self_codex()
    chapter = c.load("x/town", "procedure", _lower("pass"))(c.context())
    with pytest.raises(
        LegislationError, match=f"self.{name}: .* a store on self takes a new name"
    ):
        setattr(chapter, name, 1)


def test_a_store_under_a_new_name_still_works():
    c = _self_codex()
    chapter = c.load("x/town", "procedure", _lower("self.memo = 7"))(c.context())
    assert chapter.charge() == UNTOUCHED and chapter.memo == 7


# A hook may store on its event parameter, so it must be handed the call's own Event: a chapter that called its own hook
# with an SDK function, or with itself, would turn that store into state kept between calls, or into a store on self.
HOOK_CALLS = {
    "an SDK function": "self.on_codex_install(days)",
    "the chapter itself": "self.on_codex_install(self)",
    "no event": "self.on_codex_install()",
    "an event as a keyword": "self.on_codex_install(event=days)",
}


def _hook_chapter(mark, call):
    """A chapter with a hook, marked ``mark``, that stores on its event, and a method that makes ``call``."""
    c = Codex(["x/town"], realm="x/town", operations=["codex_install"])
    text = (
        f"from realms.legislation import above, days, {mark}\n"
        "class Procedure(above('procedure')):\n"
        f"    @{mark}\n    def on_codex_install(self, event):\n        event.memo = 'x'\n\n"
        f"    def charge(self):\n        {call}\n"
    )
    return c.load("x/town", "procedure", text)(c.context())


@pytest.mark.parametrize("mark", ["overridable", "extension"])
@pytest.mark.parametrize("call", sorted(HOOK_CALLS))
def test_a_hook_runs_with_the_calls_event_only(mark, call):
    from realms.legislation import days

    chapter = _hook_chapter(mark, HOOK_CALLS[call])
    with pytest.raises(
        LegislationError, match="a hook runs with the operation's event"
    ):
        chapter.charge()
    assert not hasattr(days, "memo")


@pytest.mark.parametrize("mark", ["overridable", "extension"])
def test_a_hook_still_runs_with_an_event(mark):
    from realms.legislation import Event

    chapter = _hook_chapter(mark, "pass")
    event = Event("a", {}, {}, 0)
    chapter.on_codex_install(event)
    assert event.memo == "x"


EXPORTED = (
    "above",
    "days",
    "extension",
    "fixed",
    "forbid",
    "months",
    "overridable",
    "pct",
    "when",
    "working_days",
    "years",
)


@pytest.mark.parametrize("name", EXPORTED)
def test_the_functions_a_chapter_imports_refuse_attributes(name):
    import realms.legislation as sdk

    function = getattr(sdk, name)
    with pytest.raises(LegislationError, match="never change"):
        function.memo = 1
    assert not hasattr(function, "memo")


def test_every_other_name_a_chapter_imports_is_a_class_or_an_expression_builder():
    import realms.legislation as sdk

    assert sorted(set(sdk.__all__) - set(EXPORTED)) == ["F", "Money", "Ref", "S"]


def test_the_functions_a_chapter_imports_still_work():
    import realms.legislation as sdk

    assert (
        sdk.days(2).to_json() == {"count": 2, "unit": "Day"}
        and sdk.pct("1.10") is not None
    )
    assert (
        sdk.fixed(5, legislation="x").mark == "fixed"
        and sdk.overridable(lambda self: 1).mark == "overridable"
    )
