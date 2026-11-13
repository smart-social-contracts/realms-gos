"""Lone surrogates on the real wasm (core.surrogates).

The canister's ``json.loads`` makes a lone surrogate of the escape ``"\\ud800"`` and its ``json.dumps`` writes it as it
is, but the stable map refuses one, as a key or as a value (UnicodeEncodeError at the insert), and so does Candid. So no
row can hold one, and a call that carried one used to trap where it first wrote or replied with it, with a Python error
for its message. These tests hold:

- a call whose arguments hold one is refused, whichever operation, with the argument named, and keeps nothing;
- the stable map still refuses one. Were it ever to take them, a row that held one would make every reply that held the
  row trap, and ``json.dumps`` would have to write the escape. (A trap of ``proposal_get`` on a proposal "stored" with one
  that the review of #697 saw came from the shell: it catches the error of the insert and commits, which leaves the
  entity in ic-python-db's map for the messages of that round; the next round reads the row from the map, clean.)
- a reply of another canister, or an HTTPS body, whose JSON holds one is refused like a call that failed;
- what the checks cost.
"""

import json

import pytest
from network import Network
from pic import Canister
from test_installs import Site, active_id, failing, install, own_layer

LONE = "\ud800"

# A call with one in each place a person's words go: ``(label, operation, arguments)``.
REFUSED = [
    ("a proposal's title", "proposal_create", {"title": "Road" + LONE}),
    ("a proposal's description", "proposal_create", {"title": "Road", "description": "Works" + LONE}),
    ("a tag", "proposal_create", {"title": "Road", "tags": ["parks", "p" + LONE]}),
    ("a proposal's action", "proposal_create",
     {"title": "Road", "action": {"op": "organization_register", "args": {"organization": "club" + LONE}}}),
    ("a name", "organization_register", {"organization": "club" + LONE}),
    ("a nickname", "profile_update", {"nickname": "Ali" + LONE}),
    ("a message", "notification_send", {"recipient": "User:FOUNDER", "title": "Hello", "body": LONE}),
    ("a request", "request_file", {"kind": "petition", "body": LONE}),
    ("the name of an argument", "proposal_create", {"title": "Road", LONE: 1}),
    ("the realm's name", "realm_configure", {"name": "Agora" + LONE}),
    ("a read's argument", "proposal_get", {"proposal": "P-1" + LONE}),
]


@pytest.fixture(scope="module")
def town(wasm_of, people):
    """A launched realm, with its founder."""
    realm = Canister(wasm_of("production"), installer=people["installer"])
    founder = people["founder"]
    for who, step, args in [
        (people["installer"], "install", {"network": "local", "founder": founder}),
        (founder, "realm", {"name": "Agora", "manifesto": "Every step is tested.", "welcome_message": "Welcome",
                            "open_registration": True}),
        (founder, "branding", {"logo_url": "", "background_image_url": "", "primary_color": "#1E6B52"}),
    ]:
        out = realm.call("setup_step", {"step": step, **args}, sender=who)
        assert out["ok"], out
    assert realm.call("setup_launch", {}, sender=founder)["ok"]
    return realm


def run(realm, code):
    out = realm.shell(code)
    assert "Traceback" not in out, out
    return out


def held(realm):
    """How many proposals and organizations the realm holds, and its last audit event."""
    return [int(n) for n in run(realm, "from ggg import Event, Organization, Proposal\n"
                                       "print(Proposal.count(), Organization.count(), Event.max_id())").split()]


def kept_nothing(realm, before):
    """Nothing was filed or registered since ``held`` gave ``before``, and no audit event is of a refused call: a realm
    with an active codex records events of its own as its timers run."""
    after = held(realm)
    since = run(realm, "from ggg import Event\n"
                       f"print([Event[str(i)].op for i in range({before[2]} + 1, Event.max_id() + 1)])")
    return after[:2] == before[:2] and not any(op in since for op in ("proposal_create", "organization_register"))


def culprit(args):
    """The first argument whose name or value holds the surrogate."""
    return next(key for key, value in args.items() if LONE in key or LONE in json.dumps(value, ensure_ascii=False))


@pytest.mark.parametrize("label, name, args", REFUSED, ids=[label for label, _, _ in REFUSED])
def test_a_call_with_a_lone_surrogate_is_refused_with_the_argument_named_and_keeps_nothing(town, people, label, name, args):
    founder = people["founder"]
    args = json.loads(json.dumps(args).replace("FOUNDER", founder))
    before = held(town)
    out = town.call(name, args, sender=founder, query=name == "proposal_get")
    assert out["ok"] is False and out["kind"] == "invalid_argument", out
    assert out["error"] == f"argument {culprit(args)!r} holds a lone surrogate (U+D800 to U+DFFF), which is not text"
    assert kept_nothing(town, before)


@pytest.fixture(scope="module")
def codex_town(wasm_of, people):
    """A realm whose layered codex is active: the guards of its sandbox are asked about every call, with its arguments,
    which the sandbox's boundary cannot take a lone surrogate through (candid_encode: expected string argument)."""
    net = Network(wasm_of("production"), people)
    realm = net.realm("town")
    install(net, realm, Site(net.web).change_list(own_layer(people["founder"])))
    assert realm.ok("codex_status", "founder", query=True)["codex"]
    return realm


@pytest.mark.parametrize("label, name, args", [r for r in REFUSED if r[1] == "proposal_create"],
                         ids=[label for label, name, _ in REFUSED if name == "proposal_create"])
def test_a_call_is_refused_before_the_active_codex_is_asked(codex_town, people, label, name, args):
    """The refusal runs first, ahead of the codex's guards and hooks and whatever is filed: a clean reply, not a trap."""
    before = held(codex_town)
    out = codex_town.call(name, args, sender=people["founder"])
    assert out["ok"] is False and out["kind"] == "invalid_argument", out
    assert out["error"] == f"argument {culprit(args)!r} holds a lone surrogate (U+D800 to U+DFFF), which is not text"
    assert kept_nothing(codex_town, before)


def test_a_pair_is_one_character_and_is_taken(town, people):
    out = town.call("proposal_create", {"title": "Party \U0001F600"}, sender=people["founder"])
    assert out["ok"], out
    shown = town.call("proposal_get", {"proposal": out["proposal"]}, sender=people["founder"], query=True)
    assert shown["proposal"]["title"] == "Party \U0001F600"


@pytest.fixture(scope="module")
def calendars(wasm_of, people):
    """A realm with no codex yet, and the harness's web that serves the change lists."""
    net = Network(wasm_of("production"), people)
    return net, net.realm("calendars")


def calendar(name):
    """A layer's calendar, written with the escapes of ``json.dumps``: ``\\ud800`` for a lone surrogate, a pair of them
    for ``\U0001F600``."""
    return json.dumps({"holidays": [{"day": "01-01", "name": name}]}).encode()


def test_a_layers_calendar_that_holds_a_lone_surrogate_fails_the_install_by_name_and_a_pair_installs(calendars, people):
    """The calendar is the one chapter that is JSON: it is read where the legislation parses it, after its checksum has
    been checked (tests/ggg/test_lone_surrogates.py runs the routes). The job names it once, the codex stays as it was."""
    net, realm = calendars
    site, layer = Site(net.web), own_layer(people["founder"])
    job = failing(net, realm, site.change_list({**layer, "arcadia/calendar": calendar("New Year " + LONE)}),
                  "arcadia/calendar.json holds a lone surrogate (U+D800 to U+DFFF), which is not text")
    assert job["error"].count("arcadia/calendar") == 1, job
    assert active_id(realm) is None
    done = install(net, realm, site.change_list({**layer, "arcadia/calendar": calendar("New Year \U0001F600")}))
    assert done["status"] == "done" and done["error"] == "", done
    assert active_id(realm)


# The shell is the one place a lone surrogate is made in the canister, on purpose: it runs Python.
MAP_REFUSES = r'''
from ggg import Proposal
from ic_python_db import Database

db = Database.get_instance()
tries = {
    "a value": lambda: db._db_storage.insert("probe:value", "a\ud800b"),
    "a key": lambda: db._db_storage.insert("probe:k\ud800", "x"),
    "a new row": lambda: Proposal(id="P-90", title="T\ud800"),
    "a row's text": lambda: setattr(Proposal["PID"], "title", "T\ud800"),
    "a row's action": lambda: setattr(Proposal["PID"], "action", {"op": "x", "args": {"source": "x = '\ud800'"}}),
}
for what, attempt in tries.items():
    try:
        attempt()
        print(what, "was taken")
    except Exception as e:
        print(what, "raised", type(e).__name__)
'''
READ_BACK = r'''
from ggg import Proposal

print(ascii(Proposal["PID"].title), ascii(Proposal["PID"].action))
'''


def test_the_stable_map_refuses_a_lone_surrogate_so_that_no_row_can_hold_one(town, people):
    """If this fails, the map takes what it refused: a row may then hold a lone surrogate, and every reply that holds the
    row traps. ``json.dumps`` must then write its escape, and a GGG reply its text written out."""
    founder = people["founder"]
    filed = town.call("proposal_create", {"title": "Road"}, sender=founder)
    assert filed["ok"], filed
    pid = filed["proposal"]
    assert run(town, MAP_REFUSES.replace("PID", pid)).splitlines() == [
        "a value raised UnicodeEncodeError", "a key raised UnicodeEncodeError", "a new row raised UnicodeEncodeError",
        "a row's text raised UnicodeEncodeError", "a row's action raised UnicodeEncodeError"]
    town.wait(1)  # the next round: ic-python-db's map of this message's entities is empty
    assert run(town, READ_BACK.replace("PID", pid)).split() == ["'Road'", "None"]
    for name in ("proposal_get", "proposal_list"):
        assert town.call(name, {"proposal": pid}, sender=founder, query=True)["ok"]
    town.upgrade()
    assert run(town, READ_BACK.replace("PID", pid)).split() == ["'Road'", "None"]


# A reply the way a peer's ``run`` gives it: a JSON text with the escape of a lone surrogate in it, in Candid text. The
# calls go through chain.call_text and chain.call_op, the real ones, with the call to the other canister replaced.
PEER_REPLIES = r'''
from core import chain, layered_codex, op
from core.candid import quote
from core.db import Fail

was = chain.call
replies = {}


def replying(cid, method, candid="()", cycles=0):
    return "(" + quote(replies[cid]) + ")"
    yield


chain.call = replying
try:
    replies["holds"] = r'{"ok":true,"name":"Q\ud800"}'
    replies["pair"] = r'{"ok":true,"name":"Q😀"}'
    replies["escaped backslash"] = '{"ok":true,"name":"Q\\\\ud800"}'
    for cid in replies:
        for how, fn in (("call_text", lambda: chain.call_text(cid, "status")),
                        ("call_op", lambda: chain.call_op(cid, "status", {}))):
            try:
                op.run(fn())
                print(cid, how, "read")
            except Exception as e:
                print(cid, how, "failed:", e)
finally:
    chain.call = was
# A body is scanned only when it is JSON. The canister's json.loads reads the first value and ignores the rest, so code
# that begins with a docstring would, scanned, be taken for JSON that holds one.
for what, body, is_json in (("holds", rb'{"id":"lex\ud800"}', True), ("code", rb"x = '\ud800'", False),
                            ("docstring", rb'"\ud800 a docstring"' + b"\nx = 1\n", False),
                            ("docstring, scanned", rb'"\ud800 a docstring"' + b"\nx = 1\n", True)):
    try:
        layered_codex.decoded("https://x.test/r.json", 200, body, 1024, is_json=is_json)
        print("decoded", what, "read")
    except Fail as e:
        print("decoded", what, "refused:", e)
'''
REASON = "holds a lone surrogate (U+D800 to U+DFFF), which is not text"


def test_a_reply_or_an_https_body_whose_json_holds_a_lone_surrogate_is_refused(town):
    assert run(town, PEER_REPLIES).splitlines() == [
        f"holds call_text failed: status on holds failed: its reply {REASON}",
        f"holds call_op failed: status on holds failed: its reply {REASON}",
        "pair call_text read", "pair call_op read",
        "escaped backslash call_text read", "escaped backslash call_op read",
        f"decoded holds refused: https://x.test/r.json: {REASON}",
        "decoded code read", "decoded docstring read",
        f"decoded docstring, scanned refused: https://x.test/r.json: {REASON}"]


# What the checks cost, in instructions on the wasm.
MEASURE = '''
from basilisk import ic
from core import surrogates
value = %(value)s
start = ic.performance_counter(0)
%(code)s
print(ic.performance_counter(0) - start)
'''
USERS = "{'users': [{'user': f'principal-{i}', 'nickname': f'Nick {i}'} for i in range(1000)]}"


def instructions(realm, value, code):
    return int(run(realm, MEASURE % {"value": value, "code": code}).split()[-1])


def test_the_check_of_a_calls_arguments_is_a_few_thousand_instructions_a_text(town, costs):
    """1,000 users to import are 4,000 texts and keys, and the one key."""
    spent = instructions(town, USERS, "assert not surrogates.holds(value)")
    costs.report("surrogates.holds, the arguments of user_import for 1,000 users", spent)
    assert spent < 4_001 * 5_000  # measured 3,700 a text


def test_the_check_of_text_that_is_plain_ascii_costs_a_constant(town, costs):
    """The largest image a realm takes is 960,000 characters; ASCII text is known to hold none without a pass."""
    spent = instructions(town, "{'logo': 'Ab+/' * 240_000}", "assert not surrogates.holds(value)")
    costs.report("surrogates.holds, a 960,000-character image", spent)
    assert spent < 50_000


def test_the_check_of_text_past_ascii_is_a_pass_in_c(town, costs):
    spent = instructions(town, "{'manifesto': 'Aprovació de l\\'expedient. ' * 40_000}",
                         "assert not surrogates.holds(value)")
    costs.report("surrogates.holds, a 1,000,000-character manifesto past ASCII", spent)
    assert spent < 40_000_000  # measured 23 an input character


def test_the_check_of_a_reply_is_a_scan_in_c_unless_the_text_has_the_start_of_an_escape(town, costs):
    reply = "'{\"ok\":true,\"text\":\"' + 'Aprovació de l\\'expedient. ' * 40_000 + '\"}'"
    spent = instructions(town, reply, "assert not surrogates.json_holds(value)")
    costs.report("surrogates.json_holds, a reply of 1,000,000 characters", spent)
    assert spent < 40_000_000  # measured 23 an input character, two scans of the text
