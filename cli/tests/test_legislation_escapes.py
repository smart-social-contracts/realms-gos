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
