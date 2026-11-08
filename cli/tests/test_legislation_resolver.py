"""The resolver of the layered codex (docs/codices/layered-codex.md §3.3, §4.4, §5.1-5.7, §5.9, §9.2 step 5):
layer paths from the links with the one-chain rule, the chapters loaded in order, ``uses`` and its depth
limit, and the flattened view. Pure library, no canister: the sync sources' records are plain data and
the chapters are loaded from their source text, as the realm and the tools load them.
"""

import hashlib
import sys
from types import SimpleNamespace

import pytest

from realms.legislation import (
    Codex,
    Event,
    LegislationError,
    Money,
    Refused,
    months,
    pct,
    plan,
    resolve,
)
from realms.legislation.context import MAX_DEPTH

VALENCIA = "eu/es/vc/46250-valencia"
OPERATIONS = (
    "codex_install",
    "document_share",
    "notification_send",
    "procedure_define",
    "proposal_create",
    "tax_assess",
    "tax_policy_propose",
    "user_add",
)

# ── The sources of §5, as the repository holds them ──────────────────────────

EU_DATA_PROTECTION = """
from realms.legislation import F, above, extension, forbid

class DataProtection(above("data_protection")):
    no_basis = forbid("document_share", F.basis.is_none(), legislation="GDPR Art. 6")

    @extension(legislation="GDPR Art. 5-6")
    def check_document_share(self, args):
        if args.basis is not None and args.basis.legal_basis == "gdpr-6-1-a":
            return self.deny("consent is not a basis between public authorities")
"""

ES_DATA_PROTECTION = """
from realms.legislation import above, extension

class DataProtection(above("data_protection")):
    @extension(legislation="Ley Orgánica 3/2018 Art. 8")
    def check_document_share(self, args):
        basis = args.basis
        if basis is not None and basis.legal_basis in ("gdpr-6-1-c", "gdpr-6-1-e") and basis.legislation is None:
            return self.deny("name the legislation that sets this public task")
"""

VALENCIA_DATA_PROTECTION = """
from ggg import External
from realms.legislation import above

class DataProtection(above("data_protection")):
    def check_document_share(self, args):
        recipient = External.get(args.recipient.id) if args.recipient.kind == "external" else None
        if recipient is not None and recipient.kind != "authority" \\
                and not self.approved_by("Junta de Govern Local"):
            return self.deny("a recipient that is not an authority needs the Junta de Govern Local")
"""

ES_PROCEDURE = """
from realms.legislation import F, above, fixed, forbid, months, overridable, working_days

class Procedure(above("procedure")):
    uses = []

    default_decision_deadline = overridable(months(3), legislation="Ley 39/2015 Art. 21.3")

    over_six_months = forbid("procedure_define",
        F.decision_deadline.is_some() & (F.decision_deadline > months(6)) & F.longer_deadline_legislation.is_none(),
        legislation="Ley 39/2015 Art. 21.2")

    @fixed(legislation="Ley 39/2015 Art. 30")
    def working_days_after(self, day, n):
        return self.calendar.add(day, working_days(n))
"""

VALENCIA_PROCEDURE = """
from realms.legislation import above, months

class Procedure(above("procedure")):
    default_decision_deadline = months(2)
"""

ES_LANGUAGE = """
from realms.legislation import above, extension

class Language(above("language")):
    co_official = extension(["es"], legislation="Constitución Art. 3")
"""

CV_LANGUAGE = """
from realms.legislation import above, extension

class Language(above("language")):
    co_official = extension(["ca-valencia"], legislation="Estatut d'Autonomia Art. 6")
"""

ES_LOCAL_REGIME = """
from realms.legislation import Ref, above, extension

class LocalRegime(above("local_regime")):
    uses = ["procedure"]

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def check_proposal_create(self, args):
        if "Code" not in args.action:
            return None
        earliest = self.legislation.procedure.working_days_after(args.earliest_close_day, 15)
        if args.effective_day is None or args.effective_day < earliest:
            return self.deny("an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_codex_install(self, event):
        for authority in ("AGE", "GVA"):
            self.ggg.notification_send(recipient=Ref("external", authority), topic="ordinance",
                                       title="Comunicació d'acord", body=event.result["codex"])
"""

CV_LOCAL_REGIME = """
from ggg import Document
from realms.legislation import above, extension

class LocalRegime(above("local_regime")):
    uses = ["language"]

    @extension(legislation="Llei 4/1983; Llei 8/2010")
    def on_codex_install(self, event):
        languages = Document.get(event.args["document"]).languages
        event.languages_missing = [lang for lang in self.legislation.language.co_official
                                   if lang not in languages]
"""

VALENCIA_LOCAL_REGIME = """
from ggg import Member
from realms.legislation import Ref, above

class LocalRegime(above("local_regime")):

    def on_codex_install(self, event):
        title = "Nova ordenança" + (" (traducció pendent)" if event.languages_missing else "")
        for m in Member.each():
            self.ggg.notification_send(recipient=Ref("user", m.user), topic="ordinance",
                title=title, body=event.result["codex"])
"""

ES_REGISTER = """
from realms.legislation import Ref, above, fixed

class Register(above("register")):
    authority = fixed(Ref("external", "INE"), legislation="Ley 7/1985 Art. 17.3")

    @fixed(legislation="Ley 7/1985 Art. 17.3")
    def on_user_add(self, event):
        self.ggg.notification_send(recipient=self.authority, topic="padron", title="alta", body=event.args["user"])
"""

VALENCIA_REGISTER = """from realms.legislation import above
class Register(above("register")):
    def on_user_add(self, event):
        pass
"""

ES_TAX = """
from realms.legislation import F, S, above, fixed, forbid, overridable, pct, when

class Tax(above("tax")):
    capital = overridable(False, legislation="TRLRHL Art. 72.3")
    surface_public_transport = overridable(False, legislation="TRLRHL Art. 72.3")
    services_beyond_art26 = overridable(False, legislation="TRLRHL Art. 72.3")
    rustic_over_80pct = overridable(False, legislation="TRLRHL Art. 72.3")

    ibi_rate_required = forbid("tax_policy_propose",
        F.code.in_(["ibi_urban", "ibi_rustic"]) & F.rate.is_none(),
        each="taxes", legislation="TRLRHL Art. 72")
    ibi_urban_range = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & ~F.rate.between(pct("0.40"), pct("1.30")),
        each="taxes", legislation="TRLRHL Art. 72.1, 72.3")
    ibi_urban_cap = forbid("tax_policy_propose",
        (F.code == "ibi_urban") & F.rate.is_some() & (F.rate > pct("1.10")
            + when(S.capital, pct("0.07")) + when(S.surface_public_transport, pct("0.07"))
            + when(S.services_beyond_art26, pct("0.06"))),
        each="taxes", legislation="TRLRHL Art. 72.3")
    ibi_rustic_cap = forbid("tax_policy_propose",
        (F.code == "ibi_rustic") & F.rate.is_some() & (F.rate > pct("0.90")
            + when(S.capital, pct("0.06")) + when(S.surface_public_transport, pct("0.05"))
            + when(S.services_beyond_art26, pct("0.06")) + when(S.rustic_over_80pct, pct("0.15"))),
        each="taxes", legislation="TRLRHL Art. 72.3")

    @fixed(legislation="TRLRHL Arts. 61, 65, 71 and 74")
    def compute_tax_assess(self, entry, subject):
        if entry.code != "ibi_urban":
            return []
        gross = subject.assessed_value.times(entry.rate)
        net = gross - gross.times(self.ibi_bonus(subject))
        items = [{"concept": entry.code, "amount": gross, "legislation": "TRLRHL Art. 71.1"}]
        if net < gross:
            items.append({"concept": entry.code + "_bonus", "amount": net - gross,
                          "legislation": "TRLRHL Art. 74"})
        return items

    @overridable(legislation="TRLRHL Art. 74")
    def ibi_bonus(self, land):
        return 0
"""

VALENCIA_TAX = """
from ggg import Attribute, Human
from realms.legislation import above, pct

class Tax(above("tax")):
    capital = True
    surface_public_transport = True
    large_family_bonus = {"general": pct("60"), "special": pct("90")}

    def ibi_bonus(self, land):
        owner = Human.for_user(land.owner.id)
        verified = [a.value for a in Attribute.of(owner, kind="large_family") if a.status == "Verified"]
        return self.large_family_bonus.get(verified[0], 0) if verified else 0
"""

EMSHI_WATER = """
from ggg import TaxPolicy
from realms.legislation import Ref, above, fixed

class Water(above("water")):
    bulk_supplier = fixed(Ref("external", "EMSHI"), legislation="Llei 2/2001; EMSHI statutes (illustrative)")

    @fixed(legislation="EMSHI statutes (illustrative)")
    def on_tax_policy_propose(self, event):
        for entry in TaxPolicy.get(event.args["policy"]).taxes:
            if entry.code == "water_supply":
                self.ggg.notification_send(recipient=self.bulk_supplier, topic="tariff",
                                           title="Water tariff proposed", body=entry.code)
"""

SPAIN_CALENDAR = {
    "weekend": ["sat", "sun"],
    "years": {
        "2027": [
            {"date": "2027-01-01", "name": {"es": "Año Nuevo"}},
            {"date": "2027-10-12", "name": {"es": "Fiesta Nacional de España"}},
        ]
    },
    "legislation": "Ley 39/2015 Art. 30.7 (illustrative dates)",
}
CV_CALENDAR = {
    "years": {"2027": [{"date": "2027-10-09", "name": {"ca-valencia": "9 d'Octubre"}}]},
    "legislation": "Calendari laboral de la Comunitat Valenciana (illustrative)",
}
VALENCIA_CALENDAR = {
    "years": {
        "2027": [
            {"date": "2027-01-22", "name": {"ca-valencia": "Sant Vicent Màrtir"}},
            {"date": "2027-04-05", "name": {"ca-valencia": "Sant Vicent Ferrer"}},
        ]
    },
    "legislation": "illustrative",
}

# ── What the realm holds: the bytes by checksum, and what GGG shows a chapter ─

CHAPTERS, CALENDARS = {}, {}


def checksum(content, store):
    sha = "sha256:" + hashlib.sha256(repr(content).encode()).hexdigest()
    store[sha] = content
    return sha


def release(source, version, chapters, calendar=None):
    """One release as a sync source's record lists it (§3.2, §8), with its source."""
    return {
        "source": source,
        "version": version,
        "chapters": {d: checksum(text, CHAPTERS) for d, text in chapters.items()},
        "calendar": checksum(calendar, CALENDARS) if calendar else None,
    }


def own(chapters, calendar=None):
    """The realm's own layer: chapters and a calendar, no version."""
    made = release("-", "-", chapters, calendar)
    return {"chapters": made["chapters"], "calendar": made["calendar"]}


EU = release("eu", "1.0.0", {"data_protection": EU_DATA_PROTECTION})
ES = release(
    "eu/es",
    "2.4.0",
    {
        "tax": ES_TAX,
        "procedure": ES_PROCEDURE,
        "register": ES_REGISTER,
        "local_regime": ES_LOCAL_REGIME,
        "data_protection": ES_DATA_PROTECTION,
        "language": ES_LANGUAGE,
    },
    SPAIN_CALENDAR,
)
CV = release(
    "eu/es/vc",
    "1.1.0",
    {"local_regime": CV_LOCAL_REGIME, "language": CV_LANGUAGE},
    CV_CALENDAR,
)
EMSHI = release("eu/es/vc/emshi", "1.0.0", {"water": EMSHI_WATER})

FROM_CV = {"source": "eu/es/vc", "layers": [EU, ES, CV]}
FROM_EMSHI = {"source": "eu/es/vc/emshi", "layers": [EU, ES, CV, EMSHI]}

VALENCIA_OWN = own(
    {
        "procedure": VALENCIA_PROCEDURE,
        "local_regime": VALENCIA_LOCAL_REGIME,
        "data_protection": VALENCIA_DATA_PROTECTION,
        "tax": VALENCIA_TAX,
    },
    VALENCIA_CALENDAR,
)
TOWN = (
    "eu/es/vc/46000-town"  # a CV town that writes nothing and does not sync from EMSHI
)

ggg = type(sys)("ggg")
ggg.Document = SimpleNamespace(get=lambda id: SimpleNamespace(languages=["es"]))
ggg.Member = SimpleNamespace(
    each=lambda: [SimpleNamespace(user="alice"), SimpleNamespace(user="bob")]
)
ggg.External = SimpleNamespace(
    get=lambda id: SimpleNamespace(kind="authority" if id == "AGE" else "company")
)
ggg.Human = SimpleNamespace(for_user=lambda user: f"human:{user}")
ggg.Attribute = SimpleNamespace(
    of=lambda human, kind: [SimpleNamespace(value="general", status="Verified")]
)
ggg.TaxPolicy = SimpleNamespace(
    get=lambda id: SimpleNamespace(
        taxes=[SimpleNamespace(code="water_supply"), SimpleNamespace(code="ibi_urban")]
    )
)


def resolved(realm=VALENCIA, relays=(FROM_CV, FROM_EMSHI), mine=VALENCIA_OWN):
    return resolve(
        plan(realm, list(relays), mine),
        CHAPTERS,
        CALENDARS,
        operations=OPERATIONS,
        modules={"ggg": ggg},
    )


def chapter(resolution, domain, **context):
    codex = resolution.codex
    return codex.instance(domain, codex.context(**context)), codex


# ── Layer paths from the links (§3.3) ─────────────────────────────────────────


def test_a_realms_path_is_what_its_sync_sources_relay_then_its_own_layer():
    p = plan(VALENCIA, [FROM_CV, FROM_EMSHI], VALENCIA_OWN)
    assert p.paths == {
        "data_protection": ["eu", "eu/es", VALENCIA],
        "language": ["eu/es", "eu/es/vc"],
        "local_regime": ["eu/es", "eu/es/vc", VALENCIA],
        "procedure": ["eu/es", VALENCIA],
        "register": ["eu/es"],
        "tax": ["eu/es", VALENCIA],
        "water": ["eu/es/vc/emshi"],
    }


def test_empty_chapters_are_skipped_so_valencias_tax_path_is_spain_then_valencia():
    p = plan(VALENCIA, [FROM_CV, FROM_EMSHI], VALENCIA_OWN)
    assert p.paths["tax"] == [
        "eu/es",
        VALENCIA,
    ]  # not eu, cv or emshi: they write no tax


def test_a_town_that_does_not_sync_from_emshi_has_no_water_path_and_never_fetches_it():
    p = plan(TOWN, [FROM_CV])
    assert "water" not in p.paths
    assert {c["domain"] for c in p.chapters} == {
        "data_protection",
        "language",
        "local_regime",
        "procedure",
        "register",
        "tax",
    }
    assert p.layers == ["eu", "eu/es", "eu/es/vc", TOWN]


def test_the_plan_names_the_chapters_to_fetch_in_the_order_they_load():
    p = plan(VALENCIA, [FROM_CV, FROM_EMSHI], VALENCIA_OWN)
    tax = [c for c in p.chapters if c["domain"] == "tax"]
    assert [(c["source"], c["version"]) for c in tax] == [
        ("eu/es", "2.4.0"),
        (VALENCIA, None),
    ]
    assert tax[0]["sha256"] == ES["chapters"]["tax"]
    assert tax[1]["sha256"] == VALENCIA_OWN["chapters"]["tax"]
    assert [c["domain"] for c in p.chapters] == sorted(c["domain"] for c in p.chapters)
    assert [(c["source"], c["version"]) for c in p.calendars] == [
        ("eu/es", "2.4.0"),
        ("eu/es/vc", "1.1.0"),
        (VALENCIA, None),
    ]
    assert p.layers == ["eu", "eu/es", "eu/es/vc", "eu/es/vc/emshi", VALENCIA]


def mini(source, version, chapters):
    return {
        "source": source,
        "version": version,
        "chapters": {d: f"sha256:{source}:{d}" for d in chapters},
        "calendar": None,
    }


def test_paths_that_extend_each_other_form_one_chain_and_the_longest_is_used():
    top, mid, low = (
        mini("a", "1", ["d"]),
        mini("a/b", "1", ["d"]),
        mini("a/b/c", "1", ["d"]),
    )
    for relays in (
        [
            {"source": "s1", "layers": [top, mid]},
            {"source": "s2", "layers": [top, mid, low]},
        ],
        [
            {"source": "s2", "layers": [top, mid, low]},
            {"source": "s1", "layers": [top, mid]},
        ],
        [
            {"source": "s1", "layers": [top, mid, low]},
            {"source": "s2", "layers": [top, mid, low]},
        ],
    ):
        assert plan("town", relays).paths == {"d": ["a", "a/b", "a/b/c"]}


def test_paths_that_differ_are_refused_unless_one_extends_the_other():
    top = mini("a", "1", ["d"])
    relays = [
        {"source": "s1", "layers": [top, mini("a/b", "1", ["d"])]},
        {"source": "s2", "layers": [top, mini("a/c", "1", ["d"])]},
    ]
    with pytest.raises(LegislationError) as e:
        plan("town", relays)
    assert str(e.value) == (
        "d: sync source s1 relays a → a/b and sync source s2 relays a → a/c. "
        "Paths that differ are refused unless one extends the other (layered-codex.md §3.3)"
    )


def test_one_domain_in_conflict_refuses_the_whole_install():
    top = mini("a", "1", ["calm", "wrong"])
    relays = [
        {"source": "s1", "layers": [top, mini("a/b", "1", ["calm", "wrong"])]},
        {"source": "s2", "layers": [top, mini("a/c", "1", ["wrong"])]},
    ]
    with pytest.raises(LegislationError, match="^wrong: sync source s1 relays a → a/b"):
        plan("town", relays)


def test_layers_that_no_relay_orders_against_each_other_are_refused_even_in_different_domains():
    spain = mini("eu/es", "1", ["tax"])
    relays = [
        {"source": "cv", "layers": [spain, mini("eu/es/vc", "1", ["local_regime"])]},
        {"source": "metro", "layers": [spain, mini("eu/es/amv", "1", ["transport"])]},
    ]
    with pytest.raises(LegislationError) as e:
        plan("town", relays)
    assert str(e.value) == (
        "the sync sources' records leave eu/es/vc and eu/es/amv with neither above the other: "
        "the layers a realm runs form one chain (layered-codex.md §3.3)"
    )


def test_layers_with_nothing_to_run_do_not_need_an_order():
    spain = mini("eu/es", "1", ["tax"])
    relays = [
        {"source": "cv", "layers": [spain, mini("eu/es/vc", "1", [])]},
        {"source": "metro", "layers": [spain, mini("eu/es/amv", "1", [])]},
    ]
    p = plan("town", relays)
    assert p.paths == {"tax": ["eu/es"]} and p.layers == ["eu/es", "town"]


def test_layers_ordered_through_a_shared_relay_are_one_chain():
    a, b, c = mini("a", "1", ["x"]), mini("b", "1", ["y"]), mini("c", "1", ["z"])
    relays = [
        {"source": "s1", "layers": [a, c]},
        {"source": "s2", "layers": [a, b]},
        {"source": "s3", "layers": [a, b, c]},  # puts b above c
    ]
    assert plan("town", relays).layers == ["a", "b", "c", "town"]


def test_two_records_that_share_a_layer_order_the_rest_through_it():
    a, b, c = mini("a", "1", ["x"]), mini("b", "1", ["y"]), mini("c", "1", ["z"])
    relays = [{"source": "b", "layers": [a, b]}, {"source": "c", "layers": [b, c]}]
    assert plan("town", relays).layers == ["a", "b", "c", "town"]  # a and c: through b


def test_relays_that_order_two_layers_the_other_way_round_are_refused_naming_them():
    a, b, c = mini("a", "1", ["tax"]), mini("b", "1", ["water"]), mini("c", "1", ["x"])
    relays = [
        {"source": "s1", "layers": [a, b, c]},  # c is in no conflict
        {"source": "s2", "layers": [b, a]},
    ]
    with pytest.raises(LegislationError) as e:
        plan("town", relays)
    assert str(e.value) == (
        "the sync sources put these layers in contradicting orders: b → a → b"
    )


def test_a_circle_of_three_orders_is_refused_naming_the_circle():
    a, b, c = mini("a", "1", ["x"]), mini("b", "1", ["y"]), mini("c", "1", ["z"])
    relays = [
        {"source": "s1", "layers": [a, b]},
        {"source": "s2", "layers": [b, c]},
        {"source": "s3", "layers": [c, a]},
    ]
    with pytest.raises(LegislationError, match="orders: b → c → a → b$"):
        plan("town", relays)


def test_two_sync_sources_relaying_different_releases_of_one_layer_are_refused():
    spain_new, spain_old = mini("eu/es", "2.4.0", ["tax"]), mini(
        "eu/es", "2.3.0", ["tax"]
    )
    relays = [
        {"source": "cv", "layers": [spain_old]},
        {"source": "mad", "layers": [spain_new]},
    ]
    with pytest.raises(LegislationError) as e:
        plan("town", relays)
    assert str(e.value).startswith(
        "eu/es: sync sources cv and mad relay different releases (2.3.0 and 2.4.0). "
        "The realm's own legislation chooses which one it installs; until it does, the install is refused"
    )
    forged = dict(spain_new, chapters={"tax": "sha256:other"})
    with pytest.raises(LegislationError, match="2.4.0 with different checksums"):
        plan(
            "town",
            [
                {"source": "cv", "layers": [spain_new]},
                {"source": "mad", "layers": [forged]},
            ],
        )


def test_a_relay_that_carries_the_realms_own_layer_is_a_cycle():
    relays = [
        {
            "source": "cv",
            "layers": [mini("eu/es", "1", ["tax"]), mini("town", "1", ["tax"])],
        }
    ]
    with pytest.raises(LegislationError, match="relays town, this realm's own layer"):
        plan("town", relays)


def test_a_relay_whose_source_is_the_realm_itself_is_refused():
    with pytest.raises(LegislationError, match="sync source town is this realm itself"):
        plan("town", [{"source": "town", "layers": [mini("a", "1", ["x"])]}])


def test_a_sync_sources_own_layer_comes_last():
    a, b = mini("a", "1", ["x"]), mini("b", "1", ["y"])
    assert plan("town", [{"source": "b", "layers": [a, b]}]).layers == [
        "a",
        "b",
        "town",
    ]
    assert plan("town", [{"source": "a", "layers": [a]}]).layers == ["a", "town"]
    with pytest.raises(
        LegislationError, match="sync source b lists its own layer before the end"
    ):
        plan("town", [{"source": "b", "layers": [b, a]}])


@pytest.mark.parametrize(
    "relay, reason",
    [
        ("cv", "a sync source is"),
        ({"source": "cv"}, "a sync source is"),
        ({"source": "CV", "layers": []}, "not a source id"),
        (
            {"source": "cv", "layers": [{"source": "eu", "chapters": {}}]},
            "eu has no version",
        ),
        ({"source": "cv", "layers": [mini("eu", "1", ["Tax"])]}, "not a domain id"),
        (
            {"source": "cv", "layers": [dict(mini("eu", "1", ["tax"]), calendar="")]},
            "eu/calendar: a checksum",
        ),
        ({"source": "cv", "layers": [mini("eu", "1", ["tax"])] * 2}, "relays eu twice"),
        (
            {"source": "cv", "layers": [dict(mini("eu", "1", []), chapters=[])]},
            "chapters are",
        ),
    ],
)
def test_a_record_that_is_not_the_agreed_shape_is_refused(relay, reason):
    with pytest.raises(LegislationError, match=reason):
        plan("town", [relay])


@pytest.mark.parametrize(
    "mine",
    [
        ["x"],
        "x",
        {"chapters": {"tax": "sha256:t"}, "version": "9"},
        {"source": "eu", "chapters": {}},
    ],
)
def test_the_realms_own_layer_is_chapters_and_a_calendar_and_nothing_else(mine):
    with pytest.raises(LegislationError, match="the realm's own layer is"):
        plan("town", [], mine)


def test_a_relayed_realm_chapter_is_skipped_and_reported():
    """A release never lists the realm domain; a source that does must not break its followers."""
    mistaken = mini("eu/es", "1", ["tax", "realm"])
    p = plan("town", [{"source": "eu/es", "layers": [mistaken]}])
    assert p.paths == {"tax": ["eu/es"]}
    assert [c["domain"] for c in p.chapters] == ["tax"]
    assert p.skipped == [
        {"source": "eu/es", "domain": "realm", "sha256": "sha256:eu/es:realm"}
    ]
    two = plan(
        "town",
        [{"source": "a", "layers": [mistaken]}, {"source": "b", "layers": [mistaken]}],
    )
    assert len(two.skipped) == 1  # one layer, reported once


def test_a_layer_whose_only_chapter_was_a_realm_chapter_runs_nothing():
    only = mini("eu/es", "1", ["realm"])
    p = plan("town", [{"source": "eu/es", "layers": [only]}])
    assert p.paths == {} and p.layers == ["town"] and len(p.skipped) == 1


def test_the_realm_domain_is_the_realms_own_and_still_loads():
    r = resolved(
        mine=dict(
            VALENCIA_OWN,
            chapters={
                **VALENCIA_OWN["chapters"],
                "realm": checksum(
                    "from realms.legislation import above\nclass Realm(above('realm')):\n    pass\n",
                    CHAPTERS,
                ),
            },
        )
    )
    assert r.plan.paths["realm"] == [VALENCIA] and r.plan.skipped == []


def test_a_realm_with_no_sync_source_has_its_own_layer_only():
    assert plan("eu", [], own({"data_protection": EU_DATA_PROTECTION})).paths == {
        "data_protection": ["eu"]
    }
    assert plan("eu", []).paths == {} and plan("eu", []).layers == ["eu"]


# ── Examples 5.1-5.7, resolved ───────────────────────────────────────────────


def test_5_1_model_legislation_most_towns_write_nothing():
    valencia, _ = chapter(resolved(), "procedure")
    assert valencia.default_decision_deadline == months(2)
    town, _ = chapter(resolved(TOWN, [FROM_CV], None), "procedure")
    assert town.default_decision_deadline == months(3)  # Spain's default, Art. 21.3
    for view in (
        resolved().view("procedure"),
        resolved(TOWN, [FROM_CV], None).view("procedure"),
    ):
        assert {
            "name": "over_six_months",
            "kind": "forbid",
            "mark": None,
            "layers": [{"source": "eu/es", "legislation": "Ley 39/2015 Art. 21.2"}],
        } in view["members"]


def test_5_2_a_chain_down_the_layers():
    r = resolved()
    event = Event(
        "alice",
        {"document": "ordinance-1"},
        {"codex": "sha256:3f1c"},
        at=0,
    )
    local_regime, _ = chapter(r, "local_regime")
    local_regime.on_codex_install(event)

    def notify(recipient, title):
        args = {"recipient": recipient, "topic": "ordinance", "title": title}
        return ["notification_send", {**args, "body": "sha256:3f1c"}]

    late = "Nova ordenança (traducció pendent)"  # CV set event.languages_missing, València read it
    assert event.languages_missing == ("ca-valencia",)
    effects = local_regime.__context__.effects.queued
    assert effects == [
        notify({"kind": "external", "id": "AGE"}, "Comunicació d'acord"),
        notify({"kind": "external", "id": "GVA"}, "Comunicació d'acord"),
        notify({"kind": "user", "id": "alice"}, late),
        notify({"kind": "user", "id": "bob"}, late),
    ]


def test_5_2_spains_guard_makes_the_proposer_wait_15_working_days():
    args = SimpleNamespace(
        action={"Code": {}}, earliest_close_day="2026-12-31", effective_day="2027-01-22"
    )
    town, _ = chapter(resolved(TOWN, [FROM_CV], None), "local_regime")
    assert (
        town.check_proposal_create(args) is None
    )  # 15 working days after 31 Dec 2026, on Spain's calendar
    valencia, _ = chapter(resolved(), "local_regime")
    assert valencia.check_proposal_create(args).reason.startswith(
        "an ordinance takes effect"
    )
    args.effective_day = "2027-01-25"
    assert valencia.check_proposal_create(args) is None
    args.action, args.effective_day = {"Other": {}}, None
    assert (
        valencia.check_proposal_create(args) is None
    )  # only a change of legislation is held


def test_5_3_working_days_count_with_each_levels_holidays():
    r = resolved()
    holidays = r.codex.calendar.holidays
    assert holidays[2027] >= {
        "2027-01-01",
        "2027-10-12",
        "2027-10-09",
        "2027-01-22",
        "2027-04-05",
    }
    town_holidays = resolved(TOWN, [FROM_CV], None).codex.calendar.holidays
    assert (
        "2027-01-22" not in town_holidays[2027] and "2027-10-09" in town_holidays[2027]
    )
    assert [c["source"] for c in r.plan.calendars] == ["eu/es", "eu/es/vc", VALENCIA]


def test_5_4_guards_stack_and_the_first_deny_stops():
    guard = chapter(resolved(), "data_protection")[0].check_document_share

    def share(legal_basis, legislation=None, kind="external", to="AGE"):
        return SimpleNamespace(
            basis=SimpleNamespace(legal_basis=legal_basis, legislation=legislation),
            recipient=SimpleNamespace(kind=kind, id=to),
        )

    assert (
        guard(share("gdpr-6-1-a")).reason
        == "consent is not a basis between public authorities"
    )
    assert (
        guard(share("gdpr-6-1-e")).reason
        == "name the legislation that sets this public task"
    )
    assert guard(share("gdpr-6-1-e", "Ley 7/1985", to="ACME")).reason.startswith(
        "a recipient that is not an authority needs"
    )
    assert guard(share("gdpr-6-1-e", "Ley 7/1985", to="AGE")) is None
    approved = resolved().codex.instance(
        "data_protection", resolved().codex.context(approvals=["Junta de Govern Local"])
    )
    assert (
        approved.check_document_share(share("gdpr-6-1-e", "Ley 7/1985", to="ACME"))
        is None
    )


def test_5_5_overriding_fixed_legislation_is_refused_with_the_file_and_line():
    with pytest.raises(Refused) as e:
        resolved(mine={"chapters": {"register": checksum(VALENCIA_REGISTER, CHAPTERS)}})
    assert str(e.value) == (
        "eu/es/vc/46250-valencia/register.py:3: on_user_add is fixed by eu/es/register.py (Ley 7/1985 Art. 17.3)"
    )


def test_5_6_the_ibi_rule_the_quota_and_the_bonus():
    tax, _ = chapter(resolved(), "tax")
    assert tax.capital is True and tax.surface_public_transport is True
    assert (
        tax.services_beyond_art26 is False
    )  # Spain's default: València does not fill it
    entry = SimpleNamespace(code="ibi_urban", rate=pct("1.24"))
    subject = SimpleNamespace(
        assessed_value=Money(20_000_000, 2), owner=SimpleNamespace(id="o")
    )
    gross = Money(248_000, 2)  # 2 480.00, nearest cent
    assert tax.compute_tax_assess(entry, subject) == [
        {"concept": "ibi_urban", "amount": gross, "legislation": "TRLRHL Art. 71.1"},
        {
            "concept": "ibi_urban_bonus",
            "amount": Money(-148_800, 2),
            "legislation": "TRLRHL Art. 74",
        },
    ]
    town, _ = chapter(resolved(TOWN, [FROM_CV], None), "tax")
    assert town.capital is False and town.ibi_bonus(subject) == 0
    assert [i["amount"] for i in town.compute_tax_assess(entry, subject)] == [gross]


def test_5_7_a_body_beside_the_line_reaches_only_the_realms_that_sync_from_it():
    water, _ = chapter(resolved(), "water")
    event = Event("alice", {"policy": "p1"}, None, at=0)
    water.on_tax_policy_propose(event)
    assert water.__context__.effects.queued == [
        [
            "notification_send",
            {
                "recipient": {"kind": "external", "id": "EMSHI"},
                "topic": "tariff",
                "title": "Water tariff proposed",
                "body": "water_supply",
            },
        ]
    ]
    with pytest.raises(LegislationError, match="has no water chapter"):
        resolved(TOWN, [FROM_CV], None).codex.resolved("water")


# ── The extension merge and the checks reach the resolved codex ──────────────


def test_extension_values_merge_down_the_path_and_the_view_shows_each_layer():
    row = [
        m for m in resolved().view("language")["members"] if m["name"] == "co_official"
    ][0]
    assert row == {
        "name": "co_official",
        "kind": "setting",
        "mark": "extension",
        "value": ["es", "ca-valencia"],
        "layers": [
            {"source": "eu/es", "legislation": "Constitución Art. 3", "value": ["es"]},
            {
                "source": "eu/es/vc",
                "legislation": "Estatut d'Autonomia Art. 6",
                "value": ["es", "ca-valencia"],
            },
        ],
    }


def test_a_setting_that_fails_its_check_refuses_the_codex():
    spain = release(
        "eu/es",
        "1",
        {"procedure": """
from realms.legislation import above, days, overridable, working_days

class Procedure(above("procedure")):
    comment_period = overridable(working_days(30), legislation="Ley 7/1985 Art. 49",
                                 check=lambda v: v >= working_days(30))
"""},
    )
    mine = own(
        {
            "procedure": "from realms.legislation import above, days\n"
            "class Procedure(above('procedure')):\n    comment_period = days(45)\n"
        }
    )
    with pytest.raises(Refused, match="comment_period = days\\(45\\) fails the check"):
        resolved("town", [{"source": "eu/es", "layers": [spain]}], mine)


def test_the_view_of_a_domain_is_its_path_and_a_row_per_public_member():
    view = resolved().view("tax")
    assert view["domain"] == "tax" and view["path"] == ["eu/es", VALENCIA]
    rows = {m["name"]: m for m in view["members"]}
    assert [m["name"] for m in view["members"]][:5] == [
        "capital",
        "surface_public_transport",
        "services_beyond_art26",
        "rustic_over_80pct",
        "ibi_rate_required",
    ]
    assert rows["capital"]["value"] is True and rows["capital"]["layers"] == [
        {"source": "eu/es", "legislation": "TRLRHL Art. 72.3", "value": False},
        {"source": VALENCIA, "legislation": None, "value": True},
    ]
    assert rows["ibi_urban_cap"] == {
        "name": "ibi_urban_cap",
        "kind": "forbid",
        "mark": None,
        "layers": [{"source": "eu/es", "legislation": "TRLRHL Art. 72.3"}],
    }
    assert rows["compute_tax_assess"]["kind"] == "compute"
    assert rows["compute_tax_assess"]["mark"] == "fixed"
    assert [layer["source"] for layer in rows["ibi_bonus"]["layers"]] == [
        "eu/es",
        VALENCIA,
    ]
    assert rows["large_family_bonus"]["value"] == {
        "general": pct("60"),
        "special": pct("90"),
    }
    assert set(resolved().views()) == set(resolved().plan.paths)
    with pytest.raises(LegislationError, match="has no nothing chapter"):
        resolved().view("nothing")


def test_chapters_whose_bytes_are_missing_are_refused_by_file():
    p = plan(VALENCIA, [FROM_CV, FROM_EMSHI], VALENCIA_OWN)
    without = {k: v for k, v in CHAPTERS.items() if k != ES["chapters"]["tax"]}
    with pytest.raises(
        Refused,
        match=f"^eu/es/tax.py: the bytes of {ES['chapters']['tax']} are missing",
    ):
        resolve(p, without, CALENDARS, operations=OPERATIONS, modules={"ggg": ggg})


# ── `uses` between domains, and the depth limit (§4.4) ───────────────────────


def own_chapters(texts):
    p = plan("town", [], own(texts))
    return p, CHAPTERS


def chapter_using(domain, uses, body="return 0"):
    return (
        "from realms.legislation import above, overridable\n"
        f"class {domain.capitalize()}(above('{domain}')):\n"
        f"    uses = {uses!r}\n    @overridable(legislation='x')\n    def call(self):\n        {body}\n"
    )


def test_uses_cycles_are_refused_and_name_the_files():
    p, found = own_chapters(
        {
            "tax": chapter_using("tax", ["procedure"]),
            "procedure": chapter_using("procedure", ["tax"]),
        }
    )
    with pytest.raises(LegislationError) as e:
        resolve(p, found)
    assert str(e.value) == (
        "uses cycle: procedure → tax → procedure "
        "(town/procedure.py uses tax; town/tax.py uses procedure)"
    )


def test_a_cycle_through_several_domains_and_layers_is_refused():
    p, found = own_chapters(
        {
            "water": chapter_using("water", ["tax"]),
            "tax": chapter_using("tax", ["procedure"]),
            "procedure": chapter_using("procedure", ["water"]),
        }
    )
    with pytest.raises(
        LegislationError, match="uses cycle: procedure → water → tax → procedure"
    ):
        resolve(p, found)


def test_a_chapter_that_uses_its_own_domain_or_a_domain_the_realm_lacks_is_refused():
    p, found = own_chapters({"tax": chapter_using("tax", ["tax"])})
    with pytest.raises(Refused, match="^town/tax.py: uses its own domain, tax"):
        resolve(p, found)
    p, found = own_chapters({"tax": chapter_using("tax", ["language"])})
    with pytest.raises(
        Refused,
        match="^town/tax.py: uses language, but this realm has no language chapter",
    ):
        resolve(p, found)


def test_a_uses_that_a_lower_layer_declares_counts_too():
    spain = release("eu/es", "1", {"tax": chapter_using("tax", [])})
    mine = own({"tax": chapter_using("tax", ["language"])})
    with pytest.raises(Refused, match="^town/tax.py: uses language"):
        resolve(plan("town", [{"source": "eu/es", "layers": [spain]}], mine), CHAPTERS)


def step_chapters(n):
    """step0 uses step1, which uses step2, ...: ``n`` domains, ``n - 1`` calls deep."""
    return {
        f"step{i}": chapter_using(
            f"step{i}",
            [f"step{i + 1}"] if i < n - 1 else [],
            (
                f"return 1 + self.legislation.step{i + 1}.call()"
                if i < n - 1
                else "return 0"
            ),
        )
        for i in range(n)
    }


def test_the_depth_limit_is_eight_calls_between_domains():
    assert MAX_DEPTH == 8
    p, found = own_chapters(step_chapters(MAX_DEPTH + 1))  # 8 calls deep: allowed
    r = resolve(p, found)
    assert r.codex.instance("step0", r.codex.context()).call() == MAX_DEPTH


def test_a_uses_chain_deeper_than_eight_is_refused_naming_the_chain():
    p, found = own_chapters(step_chapters(MAX_DEPTH + 2))  # 9 calls deep
    with pytest.raises(LegislationError) as e:
        resolve(p, found)
    message = str(e.value)
    assert message.startswith(
        "uses chain step0 → step1 → step2 → step3 → step4 → step5 → step6 → step7 → step8 → step9 "
        "is 9 calls deep: calls between domains stop at depth 8"
    )
    assert "town/step8.py uses step9" in message


PING = """
from realms.legislation import above, overridable
class Alpha(above("alpha")):
    uses = ["beta"]
    @overridable(legislation="x")
    def call(self):
        pong = self.legislation.beta.give()
        return pong(self.ping, 0)
    @overridable(legislation="x")
    def ping(self, other, n):
        return n if n >= 50 else other(self.ping, n + 1)
"""
PONG = """
from realms.legislation import above, overridable
class Beta(above("beta")):
    @overridable(legislation="x")
    def give(self):
        return self.pong
    @overridable(legislation="x")
    def pong(self, other, n):
        return other(self.pong, n + 1)
"""


def test_methods_handed_across_domains_still_count_as_calls_between_domains():
    """Alpha gets beta's bound method once, through ``uses``; then they call each other directly."""
    codex = Codex(["town"], realm="town")
    codex.load("town", "alpha", PING)
    codex.load("town", "beta", PONG)
    context = codex.context()
    with pytest.raises(LegislationError, match="calls between domains stop at depth 8"):
        codex.instance("alpha", context).call()
    assert context.depth == 0 and context.running == []  # undone as the calls unwind


def test_calls_inside_one_domain_are_not_calls_between_domains():
    own_text = (
        "from realms.legislation import above\nclass Tax(above('tax')):\n"
        "    def deep(self, n):\n        return n if n >= 30 else self.deep(n + 1)\n"
    )
    codex = Codex(["town"], realm="town")
    codex.load("town", "tax", own_text)
    assert codex.instance("tax", codex.context()).deep(0) == 30


def test_a_guard_that_calls_another_domain_leaves_the_count_as_it_found_it():
    r = resolved()
    context = r.codex.context()
    local_regime = r.codex.instance("local_regime", context)
    local_regime.check_proposal_create(
        SimpleNamespace(
            action={"Code": {}},
            earliest_close_day="2026-12-31",
            effective_day="2027-02-01",
        )
    )  # Spain's guard calls procedure's working_days_after: one call at a time
    assert context.depth == 0 and context.running == []


def test_the_runtime_stops_a_call_that_would_be_deeper_than_eight():
    codex = Codex(["town"], realm="town")
    for domain, text in step_chapters(
        MAX_DEPTH + 2
    ).items():  # 9 calls deep: the resolver would refuse
        codex.load("town", domain, text)
    context = codex.context()
    with pytest.raises(LegislationError, match="calls between domains stop at depth 8"):
        codex.instance("step0", context).call()
    assert context.depth == 0 and context.running == []  # undone as the calls unwind
    assert (
        codex.instance("step1", codex.context()).call() == MAX_DEPTH
    )  # one call shorter: allowed
