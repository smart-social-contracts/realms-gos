"""The marketplace's Candid file, its frontend declarations and its source describe one service.

The three frontends call the canister through src/declarations/marketplace_backend: a method
missing there is a method the UI cannot call. These checks only read files, so they run in the
unit gate. scripts/marketplace_candid.py regenerates the .did and the declarations.
"""

import ast
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "src" / "marketplace_backend"
DID = BACKEND / "marketplace_backend.did"
DECLARATIONS = ROOT / "src" / "declarations" / "marketplace_backend"
REGENERATE = "Regenerate with scripts/marketplace_candid.py (its docstring says how)."
# Every basilisk build adds this query; the canister's source does not define it
ADDED_BY_BUILD = {"__get_candid_interface_tmp_hack"}

_spec = importlib.util.spec_from_file_location(
    "marketplace_candid", ROOT / "scripts" / "marketplace_candid.py"
)
marketplace_candid = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(marketplace_candid)


def decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return getattr(target, "id", None)


def is_exported(node: ast.AST) -> bool:
    return isinstance(node, ast.FunctionDef) and any(
        decorator_name(d) in ("query", "update") for d in node.decorator_list
    )


def exported_methods() -> set[str]:
    """basilisk exports every @query and @update function under the entry file's directory."""
    trees = [ast.parse(path.read_text()) for path in BACKEND.rglob("*.py")]
    return {
        node.name for tree in trees for node in ast.walk(tree) if is_exported(node)
    } | ADDED_BY_BUILD


def methods_in_did(text: str) -> set[str]:
    return set(marketplace_candid.service_methods(text))


def methods_in_idl_factory(text: str) -> set[str]:
    service = text[text.index("return IDL.Service(") :]
    return set(re.findall(r"^    '(\w+)' : IDL\.Func", service, re.M))


def methods_in_typescript(text: str) -> set[str]:
    service = text[text.index("export interface _SERVICE") :]
    return set(re.findall(r"^  '(\w+)' : ActorMethod", service, re.M))


def assert_same_methods(actual: set[str], expected: set[str], what: str) -> None:
    missing, unexpected = sorted(expected - actual), sorted(actual - expected)
    assert actual == expected, f"{what} lacks {missing}, has {unexpected}. {REGENERATE}"


def test_did_service_is_what_the_source_exports():
    declared = methods_in_did(DID.read_text())
    assert_same_methods(declared, exported_methods(), "marketplace_backend.did")


@pytest.mark.parametrize(
    "filename, methods_in",
    [
        ("marketplace_backend.did", methods_in_did),
        ("marketplace_backend.did.js", methods_in_idl_factory),
        ("marketplace_backend.did.d.ts", methods_in_typescript),
    ],
)
def test_declarations_have_the_did_service(filename, methods_in):
    declared = methods_in((DECLARATIONS / filename).read_text())
    expected = methods_in_did(DID.read_text())
    assert_same_methods(declared, expected, f"declarations/{filename}")


def test_declarations_did_has_the_signatures_of_the_did():
    service_of = marketplace_candid.service_of
    declared = service_of((DECLARATIONS / "marketplace_backend.did").read_text())
    assert declared == service_of(
        DID.read_text()
    ), f"declarations/marketplace_backend.did has other signatures. {REGENERATE}"


def test_did_declares_only_types_its_service_reaches():
    did = DID.read_text()
    declared = set(marketplace_candid.type_definitions(did))
    unreachable = sorted(declared - marketplace_candid.reachable_types(did))
    assert not unreachable, (
        f"{len(unreachable)} types are unreachable from the service ({unreachable[:5]}...). "
        f"A build writes every type it finds under src/ onto this file. {REGENERATE}"
    )
