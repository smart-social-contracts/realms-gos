"""The storage of a layered codex (docs/codices/layered-codex.md §9.1): its id, its chapter texts, the one install job,
and how it sits beside a legacy codex. Nothing installs a layered codex yet, so the tests build one from the
entities."""

import hashlib
import json

import pytest
from ic_python_db import ManyToMany, ManyToOne, OneToMany, OneToOne

from api import assets, codex
from core import layered_codex as store
from core.db import Conflict, Fail, NotFound
from ggg import ChapterSource, Codex, InstallJob, LayeredCodex
from kit import ok
from test_codex import SOURCE, build, hook, manifest

CHAPTER = "class Tax:\n    pass\n"
LAYERS = ["eu/es", "eu/es/vc"]
PATHS = {"tax": ["eu/es", "eu/es/vc"]}


def entry(source, text, **more):
    """A chapter as the install records it: where it came from, and when."""
    return {"source": source, "version": "1.0.0", "sha256": store.checksum(text), "vouched_by": source,
            "route": "https", "fetched_at": 1000, "url": f"https://example.org/{source}/tax.py", "size": len(text),
            **more}


def two_chapters(**more):
    return {"eu/es/tax": entry("eu/es", CHAPTER, **more), "eu/es/vc/tax": entry("eu/es/vc", CHAPTER + "# vc\n", **more)}


def layered(chapters=None, layers=LAYERS, paths=PATHS, **fields):
    """A layered codex written through the entity, with the id its content gives."""
    chapters = chapters or two_chapters()
    return LayeredCodex(id=store.codex_id(layers, paths, chapters), layers=layers, layer_paths=paths,
                        chapters=chapters, **fields)


def test_the_id_is_the_checksum_of_the_canonical_json_of_the_legislation():
    got = store.codex_id(LAYERS, PATHS, two_chapters())
    sums = {name: c["sha256"] for name, c in two_chapters().items()}
    text = json.dumps({"chapters": sums, "layer_paths": PATHS, "layers": LAYERS}, sort_keys=True, separators=(",", ":"))
    assert got == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    assert store.canonical({"b": "ñ", "a": [1]}) == '{"a":[1],"b":"ñ"}'


def test_the_id_is_stable_for_equal_content_and_changes_with_any_of_it():
    base = store.codex_id(LAYERS, PATHS, two_chapters())
    again = store.codex_id(list(LAYERS), {"tax": ["eu/es", "eu/es/vc"]}, dict(reversed(list(two_chapters().items()))))
    assert again == base
    edited = two_chapters()
    edited["eu/es/tax"] = entry("eu/es", CHAPTER + "# edited\n")
    changed = [
        store.codex_id(LAYERS[::-1], PATHS, two_chapters()),  # the layers in another order
        store.codex_id(LAYERS, {"tax": ["eu/es"]}, two_chapters()),  # another path
        store.codex_id(LAYERS, {"water": PATHS["tax"]}, two_chapters()),  # another domain
        store.codex_id(LAYERS, PATHS, edited),  # another text
        store.codex_id(LAYERS, PATHS, {**two_chapters(), "eu/es/calendar": entry("eu/es", "{}")}),  # one more chapter
    ]
    assert len({base, *changed}) == 6


def test_the_same_legislation_fetched_by_two_routes_at_two_times_has_the_same_id():
    # A capital and its quarters each fetch the texts themselves, when they stage, from the capital or from a URL.
    there = two_chapters(route="capital", fetched_at=1_000, vouched_by="eu/es/vc", url="")
    here = two_chapters(route="https", fetched_at=9_000_000, vouched_by="eu/es", url="https://mirror.example/tax.py")
    assert there != here
    assert layered(there).id == layered(here).id


def test_a_chapter_is_stored_once_under_its_checksum():
    sha = store.checksum(CHAPTER)
    first = store.store_chapter(sha, CHAPTER)
    assert store.store_chapter(sha, CHAPTER).sha256 == first.sha256 == sha
    assert ChapterSource.count() == 1 and ChapterSource[sha].source == CHAPTER


def test_a_chapter_of_256_kb_is_kept_and_one_byte_more_is_refused():
    whole, over = "x" * store.MAX_CHAPTER, "x" * (store.MAX_CHAPTER + 1)
    assert store.store_chapter(store.checksum(whole), whole).source == whole
    with pytest.raises(Fail, match="above 256 KB"):
        store.store_chapter(store.checksum(over), over)
    # The limit counts bytes: half as many characters of two bytes each are over it too.
    wide = "ñ" * (store.MAX_CHAPTER // 2 + 1)
    with pytest.raises(Fail, match="above 256 KB"):
        store.store_chapter(store.checksum(wide), wide)
    assert ChapterSource.count() == 1


def test_a_text_that_is_not_the_one_its_key_names_is_refused():
    with pytest.raises(Fail, match="not sha256:"):
        store.store_chapter(store.checksum(CHAPTER), CHAPTER + "# changed\n")
    with pytest.raises(Fail, match="not sha256:"):
        store.store_chapter("sha256:" + "0" * 64, CHAPTER)
    assert ChapterSource.count() == 0


def test_a_second_install_job_is_refused_while_one_is_running_or_paused():
    change_list = {"url": "https://example.org/change.json", "checksum": "sha256:" + "1" * 64}
    job = store.start_job("P-1", change_list)
    assert (job.id, job.status, job.proposal, job.change_list, job.step, job.error) == (
        "J-1", "running", "P-1", change_list, "", "")
    with pytest.raises(Conflict, match="an install is running"):
        store.start_job("P-2", change_list)
    job.status = "paused"
    with pytest.raises(Conflict, match="an install is running"):  # one message, whatever holds the place (§9.2)
        store.start_job("P-2", change_list)
    assert InstallJob.count() == 1


@pytest.mark.parametrize("ended", ["failed", "done"])
def test_a_job_that_has_ended_makes_room_for_the_next(ended):
    store.start_job("P-1", {}).status = ended
    assert store.live_job() is None
    assert store.start_job("P-1", {}).id == "J-2"


def test_a_legacy_install_replaces_the_active_layered_codex_and_names_it_as_previous():
    build()
    lc = layered(status="active")
    out = ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert out["codex"] == "test@1.0.0" and codex.active().id == "test@1.0.0"
    assert (LayeredCodex[lc.id].status, Codex["test@1.0.0"].previous) == ("inactive", lc.id)


def test_active_answers_for_either_kind_and_never_for_two():
    build()
    assert codex.active() is None
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    assert codex.active().id == "test@1.0.0" and isinstance(codex.active(), Codex)
    Codex["test@1.0.0"].status = "inactive"
    lc = layered(status="active")
    assert codex.active().id == lc.id and isinstance(codex.active(), LayeredCodex)
    assert codex.active_legacy() is None and hook("init") is None  # a layered codex has no legacy hooks


def test_previous_names_a_codex_of_either_kind_and_a_revert_goes_back_across_kinds():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    lc = layered(previous="test@1.0.0", status="active")
    Codex["test@1.0.0"].status = "inactive"
    assert codex.find_codex(lc.previous).id == "test@1.0.0" and codex.find_codex("test@1.0.0") is Codex["test@1.0.0"]
    assert codex.find_codex(lc.id).id == lc.id
    assert ok("codex_revert", "ctl")["codex"] == "test@1.0.0"
    assert (Codex["test@1.0.0"].status, LayeredCodex[lc.id].status) == ("active", "reverted")
    # And forward again: a legacy codex that replaced a layered one can be reverted to it.
    ok("codex_install", "ctl", manifest=manifest("1.1.0"), source=SOURCE)
    Codex["test@1.1.0"].previous = lc.id
    assert ok("codex_revert", "ctl")["codex"] == lc.id
    assert (codex.active().id, Codex["test@1.1.0"].status) == (lc.id, "reverted")


def test_a_layered_codex_declares_no_credential_schema():
    build()
    layered(status="active")
    with pytest.raises(Fail, match="no active codex declares"):
        assets.schema_of("licence")


def test_uninstall_deactivates_a_layered_codex():
    build()
    layered(status="active")
    ok("codex_uninstall", "ctl")
    assert ok("codex_status", "alice")["codex"] is None and codex.active() is None


def test_status_shows_a_layered_codex_with_its_chapters_a_pending_one_and_the_job():
    build()
    lc = layered(status="active", installed_at=500, proposal="P-3")
    waiting = layered(two_chapters(route="capital"), layers=LAYERS[:1], paths={"tax": LAYERS[:1]}, status="pending",
                      previous=lc.id, activates_at=7_000, proposal="P-4")
    job = store.start_job("P-4", {"url": "https://example.org/change.json", "checksum": "sha256:" + "1" * 64})
    job.step, job.error = "fetch_chapters", "slow"

    out = ok("codex_status", "alice")

    assert out["codex"] == {"id": lc.id, "status": "active", "layers": LAYERS, "layer_paths": PATHS,
                            "chapters": two_chapters(), "previous": "", "installed_at": 500, "activates_at": 0,
                            "proposal": "P-3"}
    assert (out["pending"]["id"], out["pending"]["status"], out["pending"]["activates_at"]) == (
        waiting.id, "pending", 7_000)
    assert out["pending"]["chapters"]["eu/es/tax"]["route"] == "capital"
    assert out["job"] == {"id": "J-1", "proposal": "P-4", "step": "fetch_chapters", "status": "running",
                          "error": "slow", "started_at": job.started_at, "finished_at": 0}
    assert (out["healthy"], out["problem"], out["hooks"], out["config"]) == (True, {}, [], {})


def test_status_of_a_legacy_codex_shows_what_it_showed_and_no_layered_one():
    build()
    ok("codex_install", "ctl", manifest=manifest(), source=SOURCE)
    out = ok("codex_status", "alice")
    assert out["codex"]["id"] == "test@1.0.0" and out["codex"]["capabilities"] == ["t_note", "t_peek"]
    assert out["hooks"] == manifest()["hooks"] and out["config"]["id"] == "test"
    assert [h["id"] for h in out["history"]] == ["test@1.0.0"] and out["next"] is None
    assert (out["pending"], out["job"]) == (None, None)


def test_the_history_lists_the_legacy_codices_then_the_layered_ones_a_page_at_a_time():
    build()
    for v in ("1.0.0", "1.1.0"):
        ok("codex_install", "ctl", manifest=manifest(v), source=SOURCE)
    first_layered = layered(previous="test@1.1.0", status="inactive")
    second = layered(two_chapters(), layers=LAYERS[:1], paths={"tax": LAYERS[:1]}, status="active",
                     previous=first_layered.id)

    ids = [first_layered.id, second.id]
    page = ok("codex_status", "alice", count=3)
    assert [h["id"] for h in page["history"]] == ["test@1.0.0", "test@1.1.0", ids[0]] and page["next"] == 4
    rest = ok("codex_status", "alice", count=3, **{"from": page["next"]})
    assert [h["id"] for h in rest["history"]] == [ids[1]] and rest["next"] is None
    assert [h["status"] for h in rest["history"]] == ["active"]
    assert [h["id"] for h in ok("codex_status", "alice", count=2, **{"from": 3})["history"]] == ids


def test_the_entities_hold_ids_as_text_and_so_export_as_plain_rows():
    # ic-python-db refuses to serialize a relation of more than MAX_UNPAGED rows (core.db.Row.unlisted), which would
    # trap realm_export: a relation on these entities would need that care.
    relations = (ManyToOne, OneToMany, OneToOne, ManyToMany)
    for entity in (LayeredCodex, ChapterSource, InstallJob):
        assert not [name for name, prop in vars(entity).items() if isinstance(prop, relations)]
    lc = layered(status="active", proposal="P-1")
    store.store_chapter(store.checksum(CHAPTER), CHAPTER)
    store.start_job("P-1", {"url": "u", "checksum": "c"})

    def exported(entity):
        return ok("realm_export", "ctl", entity=entity, count=500)["rows"]

    assert [(r["id"], r["layer_paths"], r["proposal"]) for r in exported("LayeredCodex")] == [(lc.id, PATHS, "P-1")]
    assert [r["source"] for r in exported("ChapterSource")] == [CHAPTER]
    assert [(r["id"], r["change_list"]) for r in exported("InstallJob")] == [("J-1", {"url": "u", "checksum": "c"})]


def test_the_export_pages_chapter_texts_one_row_at_a_time():
    # A chapter can be 256 KB: more than a few in a page pass a query's instruction limit or the reply's size.
    texts = [CHAPTER + f"# {n}\n" for n in range(3)]
    for text in texts:
        store.store_chapter(store.checksum(text), text)
    seen, start = [], 1
    while start is not None:
        out = ok("realm_export", "ctl", entity="ChapterSource", count=500, **{"from": start})
        assert len(out["rows"]) == 1
        seen += [row["source"] for row in out["rows"]]
        start = out["next"]
    assert seen == texts
    assert len(ok("realm_export", "ctl", entity="LayeredCodex", count=500)["rows"]) == 0  # no cap on the others


def test_a_codex_id_without_a_version_is_a_layered_one_and_unknown_ids_are_refused_by_kind():
    with pytest.raises(NotFound, match="unknown LayeredCodex 'sha256:00'"):
        codex.find_codex("sha256:00")
    with pytest.raises(NotFound, match="unknown Codex 'test@9.9.9'"):
        codex.find_codex("test@9.9.9")
