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
    from core import op

    defining = {p.stem for p in SRC.rglob("*.py") if "\n@op(" in p.read_text()}
    assert defining == set(op.MODULES) == {p.stem for p in (SRC / "api").glob("*.py")} - {"__init__"}


def test_only_op_reads_the_raw_registry():
    for path in SRC.rglob("*.py"):
        if path.name not in ("op.py", "main.py"):
            assert "OPS" not in path.read_text(), f"{path.name}: use op.ops(), OPS may be partly loaded"


def test_every_op_says_what_it_does_and_names_its_arguments():
    # ops_list serves these docstrings: they are how agents and developers discover the realm.
    problems = []
    for path in sorted((SRC / "api").glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if not (isinstance(node, ast.FunctionDef) and any(
                    getattr(getattr(d, "func", None), "id", "") == "op" for d in node.decorator_list)):
                continue
            doc = ast.get_docstring(node) or ""
            if not doc:
                problems.append(f"{node.name}: no docstring")
            read = {n.slice.value for n in ast.walk(node) if isinstance(n, ast.Subscript)
                    and getattr(n.value, "id", "") == "args" and isinstance(n.slice, ast.Constant)}
            read |= {n.args[0].value for n in ast.walk(node) if isinstance(n, ast.Call)
                     and getattr(n.func, "attr", "") == "get" and getattr(n.func.value, "id", "") == "args"
                     and n.args and isinstance(n.args[0], ast.Constant)}
            problems += [f"{node.name}: ``{a}`` not described" for a in sorted(read) if f"``{a}``" not in doc]
    assert not problems, "\n".join(problems)


def test_core_does_not_import_api():
    # op.py is the exception: it loads the operations it dispatches to.
    for path in (SRC / "core").glob("*.py"):
        if path.name != "op.py":
            assert "from api" not in path.read_text() and "import api" not in path.read_text(), path.name
