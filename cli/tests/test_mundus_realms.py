"""The realms of a local mundus: what each one keeps its accounts in."""

import json
from types import SimpleNamespace

from realms.cli.commands import mundus_realms


def mundus(tmp_path, realms, codices=None):
    """A mundus with ``realms`` in its file, each already minted with the ``codex`` it names."""
    for name, codex in (codices or {}).items():
        folder = tmp_path / "codices" / "codices" / name
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"currency": {"symbol": codex}}))
    m = SimpleNamespace(spec={"realms": realms}, realms_dir=tmp_path, state=tmp_path / "state")
    for r in realms:
        mundus_realms.save(m, {"name": r["name"], "codex": r.get("codex", "")})
    return m


def test_the_mundus_file_names_the_currency(tmp_path):
    m = mundus(tmp_path, [{"name": "valencia", "codex": "dominion", "currency": "DEMO"}], {"dominion": "DOM"})
    assert mundus_realms.currency(m, "valencia") == "DEMO"


def test_else_the_codex_bills_in_its_own(tmp_path):
    m = mundus(tmp_path, [{"name": "capital", "codex": "dominion"}], {"dominion": "DOM"})
    assert mundus_realms.currency(m, "capital") == "DOM"


def test_else_gst(tmp_path):
    m = mundus(tmp_path, [{"name": "eu"}])
    assert mundus_realms.currency(m, "eu") == mundus_realms.DEFAULT_CURRENCY
