"""Rules the canister's CPython imposes that a desktop Python does not."""

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "realm_backend"


def test_no_re_module():
    # The canister's `re` has no `compile`; a lazy module that touches it fails on first use.
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            assert "re" not in names and getattr(node, "module", None) != "re", f"{path.name} imports re"


def test_every_op_module_is_loaded_by_ops():
    # The canister loads modules lazily: an op module missing from MODULES is unknown to lookups by name.
    import op

    defining = {p.stem for p in SRC.glob("*.py") if "\n@op(" in p.read_text()}
    assert defining == set(op.MODULES)


def test_only_op_reads_the_raw_registry():
    for path in SRC.glob("*.py"):
        if path.name not in ("op.py", "main.py"):
            assert "OPS" not in path.read_text(), f"{path.name}: use op.ops(), OPS may be partly loaded"
