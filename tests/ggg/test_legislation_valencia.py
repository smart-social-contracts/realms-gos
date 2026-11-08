"""The chapters of València (tests/fixtures/legislation/valencia, a copy of realms-codices ``legislation/``) in the
realm's runtime (docs/codices/layered-codex.md §5, realms-gos#488): the codex of the layers EU, Spain, the Comunitat
Valenciana, EMSHI and València, installed in a realm, and each example's guard or hook answering on GGG's data.

The sandbox's guards and hooks are called with the plain arguments GGG would hand them: the operations they run
around (``document_share``, ``codex_install``, ...) need arguments and rows of their own that say nothing about the
chapter."""

from pathlib import Path

import pytest

from core import legislation, op
from ggg import Department, Document, External, Member, TaxPolicy
from kit import call, ok
import world
from test_legislation_runtime import activate, fresh_realm, layered  # noqa: F401  the autouse fixture

TREE = Path(__file__).resolve().parents[1] / "fixtures/legislation/valencia"
VALENCIA = "eu/es/vc/46250-valencia"
LAYERS = ["eu", "eu/es", "eu/es/vc", "eu/es/vc/emshi", VALENCIA]
NOW = 1_800_000_000  # seconds, as the realm counts time
DAY = 86_400

# A chapter of a domain of its own, in València's layer: a guard that reports what a helper of Spain's tax chapter says.
PROBE = """
from realms.legislation import above

class Probe(above("probe")):
    uses = ["tax"]

    def check_t_probe(self, args):
        return self.deny(str(self.legislation.tax.ibi_bonus(args.owner)))
"""


@op.op("treasury.manage")
def t_probe(c, args):
    """The operation the probe chapter guards: it always refuses, and says what it found."""
    return {}


def valencia():
    chapters = {(s, f.stem): f.read_text() for s in LAYERS for f in sorted((TREE / s).glob("*.py"))}
    chapters[(VALENCIA, "probe")] = PROBE
    calendars = [(s, (TREE / s / "calendar.json").read_text()) for s in LAYERS if (TREE / s / "calendar.json").exists()]
    return activate(layered(chapters, calendars, layers=LAYERS))


def guard(operation, args, approvals=()):
    """The outcome of every domain's ``check_<operation>``: the reason it denied, or None."""
    outcomes = legislation._call("guard", op=operation, args=args, now=NOW, approvals=list(approvals))
    assert all("error" not in o for o in outcomes), outcomes
    return next((o["denied"] for o in outcomes if "denied" in o), None)


def hook(operation, domain, args, result=None):
    """The effects one domain's ``on_<operation>`` queues, as (operation, arguments)."""
    event = {"user": "alice", "args": args, "result": result or {}, "at": NOW}
    outcome = legislation._call("hook", op=operation, domain=domain, event=event, now=NOW, approvals=[])
    assert "error" not in outcome, outcome
    return [tuple(effect) for effect in outcome["effects"]]


def bonus(owner):
    return call("t_probe", owner=owner)["error"]


def submit_title(user, value, verify=True):
    attribute = ok("attribute_submit", user, kind="large_family", value=value, ref=f"vault:{user}")["attribute"]
    if verify:
        ok("attribute_review", "hector", attribute=attribute, status="verified")


@pytest.fixture
def codex():
    world.seat("civil-registry.reviewer", "hector", Department["Civil Registry"], "attribute.review")
    world.person("hector")
    return valencia()


# ── Taxes (§5.6): the bonus is read from GGG's attributes ────────────────────────────────────────────────────────


def test_a_verified_large_family_title_forgives_its_share_of_the_quota(codex):
    submit_title("alice", "general")
    submit_title("bob", "special")
    assert bonus("User:alice") == "60000000"  # 60 %, in hundred-millionths
    assert bonus("User:bob") == "90000000"


def test_no_bonus_without_a_verified_title_of_a_known_kind_held_by_a_person(codex):
    submit_title("alice", "general", verify=False)  # still pending
    submit_title("bob", "unheard-of")
    ok("attribute_submit", "dana", kind="student", value="general", ref="vault:dana")
    assert [bonus(f"User:{who}") for who in ("alice", "bob", "dana", "carla")] == ["0", "0", "0", "0"]
    assert bonus("Organization:buildco") == "0"


# ── Local regime (§5.2): the guard on a change of legislation and the hooks that run when it is installed ────────


def test_an_ordinance_must_take_effect_15_working_days_after_the_vote_can_close(codex):
    code = {"Code": {"url": "https://example.org/change.json", "checksum": "sha256:x"}}
    assert "15 working days" in guard("proposal_create", {"action": code, "effective_at": None})
    assert "15 working days" in guard("proposal_create", {"action": code, "effective_at": NOW + 7 * DAY})
    assert guard("proposal_create", {"action": code, "effective_at": NOW + 40 * DAY}) is None
    assert guard("proposal_create", {"action": {"Transfer": {}}, "effective_at": None}) is None  # not an ordinance


def test_an_ordinance_installed_tells_the_authorities_then_every_member_in_the_languages_it_has(codex):
    Member(id="zoe", joined_at=1)
    Document(id="D1", title="Ordenança", languages=["es"])  # the co-official Valencian is missing
    effects = hook("codex_install", "local_regime", {"document": "D1"}, {"codex": "sha256:abc"})
    sent = [(args["recipient"]["id"], args["title"]) for _, args in effects]
    assert sent[:2] == [("AGE", "Comunicació d'acord"), ("GVA", "Comunicació d'acord")]
    assert ("zoe", "Nova ordenança (traducció pendent)") in sent
    Document(id="D2", title="Ordenança", languages=["es", "ca-valencia"])
    complete = hook("codex_install", "local_regime", {"document": "D2"}, {"codex": "sha256:abc"})
    assert {args["title"] for _, args in complete[2:]} == {"Nova ordenança"}


# ── Data protection (§5.4): the guards stack down the layers ─────────────────────────────────────────────────────


def share(basis, recipient, approvals=()):
    return guard("document_share", {"basis": basis, "recipient": recipient}, approvals)


def test_the_guards_of_the_eu_spain_and_valencia_run_in_that_order(codex):
    External(id="AGE", name="Administración General del Estado", kind="authority")
    External(id="BUILDCO", name="BuildCo", kind="company")
    authority, company = {"kind": "external", "id": "AGE"}, {"kind": "external", "id": "BUILDCO"}
    assert "consent is not a basis" in share({"legal_basis": "gdpr-6-1-a"}, authority)
    assert "name the legislation" in share({"legal_basis": "gdpr-6-1-c"}, authority)
    task = {"legal_basis": "gdpr-6-1-c", "legislation": "Ley 7/1985"}
    assert share(task, authority) is None
    assert "Junta de Govern Local" in share(task, company)
    assert share(task, company, approvals=["Junta de Govern Local"]) is None


# ── The register and the body beside the line (§5.5, §5.7) ───────────────────────────────────────────────────────


def test_a_new_resident_is_reported_to_the_national_register(codex):
    [(operation, args)] = hook("user_add", "register", {"user": "zoe"})
    assert (operation, args["recipient"], args["topic"], args["body"]) == (
        "notification_send", {"kind": "external", "id": "INE"}, "padron", "zoe")


def test_emshi_is_told_when_a_water_tariff_is_proposed(codex):
    TaxPolicy(id="P1", taxes=[{"code": "water_supply"}, {"code": "ibi_urban"}])
    [(_, args)] = hook("tax_policy_propose", "water", {"policy": "P1"})
    assert (args["recipient"], args["topic"]) == ({"kind": "external", "id": "EMSHI"}, "tariff")
    TaxPolicy(id="P2", taxes=[{"code": "ibi_urban"}])
    assert hook("tax_policy_propose", "water", {"policy": "P2"}) == []

