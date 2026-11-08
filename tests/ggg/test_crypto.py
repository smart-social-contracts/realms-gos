import fake_chain
import world
from ggg import Document, Event, KeyEnvelope
from kit import call, denied, direct, ok

REPORT = {"title": "Inspection report, Clinic B", "ref": "files/health/DOC-1.enc", "hash": "sha256:ab12",
          "languages": ["en"]}


def health():
    depts = world.build()
    world.seat("health.inspector", "carla", depts["Health"], "document.manage")
    return depts


def test_story_health_shares_an_encrypted_report_with_clinic_b():
    health()
    out = ok("document_store", "carla", department="Health", keys={"carla": "env:carla"}, **REPORT)
    doc, scope = out["document"], out["scope"]
    assert scope == f"dept:Health:doc:{doc}"
    ok("document_share", "carla", document=doc, recipient="User:dr-ana", keys={"dr-ana": "env:ana"})
    got = ok("document_get", "dr-ana", document=doc)
    assert (got["wrapped_dek"], got["document"]["ref"], got["document"]["hash"]) == ("env:ana", REPORT["ref"], "sha256:ab12")
    assert "not shared with you" in call("document_get", "bob", document=doc)["error"]
    fake_chain.state["replies"][("aaaaa-aa", "vetkd_derive_key")] = '(record { 2_345 = blob "\\0a\\ffK\\5c" })'
    assert ok("crypto_derive_key", "dr-ana", transport_public_key="beef")["encrypted_key"] == "0aff4b5c"
    sent = fake_chain.state["calls"][-1][2]
    assert "dr-ana".encode().hex() == sent.split('input = blob "')[1].split('"')[0].replace("\\", "")
    assert ok("crypto_envelope", "dr-ana", scope=scope)["wrapped_dek"] == "env:ana"
    ok("document_revoke", "carla", document=doc, principals=["dr-ana"])
    assert "not shared" in call("document_get", "dr-ana", document=doc)["error"]
    assert [d["id"] for d in ok("document_list", "carla", department="Health")["documents"]] == [doc]


def test_documents_are_managed_by_the_departments_staff():
    depts = health()
    world.seat("finance.clerk", "bob", depts["Finance"], "document.manage")
    doc = ok("document_store", "carla", department="Health", **REPORT)["document"]
    assert "Health's staff" in call("document_share", "bob", document=doc, recipient="User:bob", keys={"bob": "env"})["error"]
    assert "Health's staff" in call("document_store", "bob", department="Health", **REPORT)["error"]
    assert "belongs to Health" in call("document_store", "bob", department="Finance", document=doc, **REPORT)["error"]
    denied("document_store", "dr-ana", department="Health", **REPORT)
    denied("document_list", "alice", department="Health")
    denied("document_get", fake_chain.ANONYMOUS, document=doc)
    ok("document_share", "carla", document=doc, recipient="User:dr-ana", keys={"dr-ana": "env:ana"})
    denied("document_delete", "dr-ana", document=doc)
    ok("document_delete", "carla", document=doc)
    assert KeyEnvelope[f"dept:Health:doc:{doc}|dr-ana"] is None and Document[doc] is None


def test_key_scopes_decide_who_grants():
    world.build()
    ok("crypto_grant", "alice", scope="user:alice:private", keys={"alice": "env:a", "claude-conn": "env:c"})
    assert "do not manage" in call("crypto_grant", "bob", scope="user:alice:private", keys={"bob": "x"})["error"]
    ok("crypto_grant", "dana", scope="dept:Civil Registry:roster", keys={"bob": "env:b"})
    ok("crypto_grant", "bob", scope="dept:Civil Registry:roster", keys={"dana": "env:d"})
    assert "do not manage" in call("crypto_grant", "alice", scope="dept:Civil Registry:roster", keys={})["error"]
    assert "unknown scope kind" in call("crypto_grant", "alice", scope="club:x", keys={})["error"]
    assert ok("crypto_scopes", "bob")["scopes"] == ["dept:Civil Registry:roster"]
    assert ok("crypto_access", "dana", scope="dept:Civil Registry:roster")["principals"] == ["bob", "dana"]
    assert "do not manage" in call("crypto_access", "alice", scope="dept:Civil Registry:roster")["error"]
    assert "unknown KeyEnvelope" in call("crypto_envelope", "bob", scope="user:alice:private")["error"]
    ok("crypto_revoke", "alice", scope="user:alice:private", principals=["claude-conn"])
    assert ok("crypto_access", "alice", scope="user:alice:private")["principals"] == ["alice"]


def test_scopes_holders_and_documents_are_listed_a_page_at_a_time():
    health()
    docs = [ok("document_store", "carla", department="Health", keys={"bob": f"env:{i}"}, **REPORT)["document"]
            for i in range(3)]
    first = ok("document_list", "carla", department="Health", count=2)
    rest = ok("document_list", "carla", department="Health", count=2, **{"from": first["next"]})
    assert [d["id"] for d in first["documents"] + rest["documents"]] == docs and rest["next"] is None
    scopes = ok("crypto_scopes", "bob", count=2)
    tail = ok("crypto_scopes", "bob", count=2, **{"from": scopes["next"]})
    assert scopes["scopes"] + tail["scopes"] == [f"dept:Health:doc:{d}" for d in docs] and tail["next"] is None
    keys = {p: "env" for p in ("alice", "bob", "dana")}
    ok("crypto_grant", "carla", scope="dept:Health:roster", keys=keys)
    who = ok("crypto_access", "carla", scope="dept:Health:roster", count=2)
    more = ok("crypto_access", "carla", scope="dept:Health:roster", count=2, **{"from": who["next"]})
    assert who["principals"] + more["principals"] == ["alice", "bob", "dana"] and more["next"] is None


def test_document_delete_revokes_keys_in_batches():
    health()
    keys = {p: f"env:{p}" for p in ("alice", "bob", "carla", "dana", "dr-ana")}
    doc = ok("document_store", "carla", department="Health", keys=keys, **REPORT)["document"]
    first = ok("document_delete", "carla", document=doc, count=3)
    assert first["revoked"] == ["alice", "bob", "carla"] and not first["deleted"] and Document[doc]
    last = ok("document_delete", "carla", document=doc, count=3)
    assert last["revoked"] == ["dana", "dr-ana"] and last["deleted"] and Document[doc] is None
    assert KeyEnvelope.count_by("scope", f"dept:Health:doc:{doc}") == 0


def test_vetkd_public_key_and_sign_in():
    world.build()
    fake_chain.state["replies"][("aaaaa-aa", "vetkd_public_key")] = '(record { public_key = blob "\\01\\02\\"" })'
    assert ok("crypto_public_key", "alice")["public_key"] == "010222"
    assert 'name = "test_key_1"' in fake_chain.state["calls"][-1][2]
    for name, args in [("crypto_public_key", {}), ("crypto_derive_key", {"transport_public_key": "00"}),
                       ("crypto_grant", {"scope": "user:x:y", "keys": {}}), ("crypto_scopes", {})]:
        denied(name, fake_chain.ANONYMOUS, **args)


def test_a_document_has_a_kind_languages_and_facts_and_replaces_them():
    health()
    doc = ok("document_store", "carla", department="Health", kind="report", facts={"given_freely": True},
             **{**REPORT, "languages": ["en", "es-ES"]})["document"]
    row = Document[doc]
    assert (row.kind, row.languages, row.facts, row.public, row.publications) == (
        "report", ["en", "es-ES"], {"given_freely": True}, None, [])
    ok("document_store", "carla", department="Health", document=doc, **{**REPORT, "languages": ["ca"]})
    assert (Document[doc].kind, Document[doc].languages, Document[doc].facts) == ("", ["ca"], {})
    for languages in (["english"], "en", [5], [""]):
        out = call("document_store", "carla", department="Health", **{**REPORT, "languages": languages})
        assert not out["ok"] and out["kind"] == "invalid_argument", languages
    assert call("document_store", "carla", department="Health", title="x", ref="r", hash="h")["kind"] == "invalid_argument"
    assert Document.max_id() == 1


def test_document_list_filters_by_kind_and_language():
    health()
    reports = ok("document_store", "carla", department="Health", kind="report", **{**REPORT, "languages": ["en", "es-ES"]})
    minutes = ok("document_store", "carla", department="Health", kind="minutes", **{**REPORT, "languages": ["ca"]})
    plain = ok("document_store", "carla", department="Health", **REPORT)

    def listed(**filters):
        return [d["id"] for d in ok("document_list", "carla", department="Health", **filters)["documents"]]

    assert listed() == [reports["document"], minutes["document"], plain["document"]]
    assert listed(kind="minutes") == [minutes["document"]]
    assert listed(language="ES-es") == [reports["document"]]
    assert listed(language="en") == [reports["document"], plain["document"]]
    assert listed(kind="report", language="ca") == []


def test_a_share_names_its_recipient_basis_and_facts():
    health()
    doc = ok("document_store", "carla", department="Health", **REPORT)["document"]
    basis = {"legal_basis": "a public health duty", "purpose": "inspection follow-up", "special_category": False}
    out = ok("document_share", "carla", document=doc, recipient="Organization:clinic-b", basis=basis,
             facts={"given_freely": False}, keys={"dr-ana": "env:ana", "records-bot": "env:bot"})
    assert out["granted"] == ["dr-ana", "records-bot"]
    event = Event[str(Event.max_id())]
    assert event.args["recipient"] == "Organization:clinic-b" and event.args["basis"] == basis
    assert "keys" not in event.args
    ok("document_share", "carla", document=doc, recipient="User:alice", keys={"alice": "env:alice"})
    for recipient in ("User:nobody", "Organization:nowhere", "Document:" + doc, "alice", "External:statistics-office"):
        out = call("document_share", "carla", document=doc, recipient=recipient, keys={"bob": "x"})
        assert not out["ok"] and out["kind"] in ("invalid_argument", "not_found"), recipient
    refused = call("document_share", "carla", document=doc, recipient="User:alice", keys={}, basis={"purpose": "x"})
    assert "legal_basis" in refused["error"]
    assert KeyEnvelope[f"dept:Health:doc:{doc}|bob"] is None


def test_I_PRV_4_anyone_reads_a_published_document_but_not_its_ciphertext():
    health()
    doc = ok("document_store", "carla", department="Health", kind="report", facts={"source": "inspection"},
             **REPORT)["document"]
    assert "sign in" in denied("document_get", fake_chain.ANONYMOUS, document=doc)
    assert "not shared" in denied("document_get", "alice", document=doc)
    assert "document.manage" in denied("document_publish", "alice", document=doc, ref="pub/report.pdf",
                                       hash="sha256:cd34")
    out = ok("document_publish", "carla", document=doc, ref="pub/report.pdf", hash="sha256:cd34")
    assert out["document"] == doc and Document[doc].public == {"ref": "pub/report.pdf", "hash": "sha256:cd34"}
    for reader in (fake_chain.ANONYMOUS, "alice"):
        got = ok("document_get", reader, document=doc)
        assert got["document"]["public"] == {"ref": "pub/report.pdf", "hash": "sha256:cd34"}
        assert (got["document"]["title"], got["document"]["kind"]) == (REPORT["title"], "report")
        assert (got["document"]["ref"], got["document"]["hash"], got["document"]["facts"]) == ("", "", {})
        assert got["wrapped_dek"] == "" and got["envelope"] is None
    staff = ok("document_get", "carla", document=doc)["document"]
    assert (staff["ref"], staff["hash"], staff["facts"]) == (REPORT["ref"], REPORT["hash"], {"source": "inspection"})
    ok("document_publish", "carla", document=doc, ref="pub/report.pdf", hash="sha256:cd34")
    assert "other content" in denied("document_publish", "carla", document=doc, ref="pub/other.pdf", hash="sha256:ee")


def test_a_document_is_published_to_an_outside_body_only_when_the_realm_knows_it():
    health()
    doc = ok("document_store", "carla", department="Health", **REPORT)["document"]
    out = call("document_publish", "carla", document=doc, to="gazette", ref="pub/r.pdf", hash="sha256:cd34")
    assert not out["ok"] and "gazette" in out["error"] and Document[doc].public is None


def test_json_arguments_of_the_wrong_type_are_refused_not_trapped():
    """Candid cannot say these, so the arguments go to the operations as JSON."""
    health()
    doc = ok("document_store", "carla", department="Health", **REPORT)["document"]
    for args in ({"recipient": 5}, {"recipient": ["User:alice"]}, {"recipient": "User:alice", "basis": "a duty"},
                 {"recipient": "User:alice", "basis": ["a duty"]}):
        out = direct("document_share", "carla", document=doc, keys={}, **args)
        assert not out["ok"] and out["kind"] == "invalid_argument", args
    out = direct("document_list", "carla", department="Health", language=5)
    assert not out["ok"] and out["kind"] == "invalid_argument"
