"""The canister's ``re`` has no ``compile``.

Basilisk registers every realm module as a lazy module whose body runs on
first attribute access. If that body raises ``AttributeError`` (as
``re.compile`` does in the canister), every ``getattr(module, name, None)``
returns None. That is how ``core.proposal_dispatch`` silently lost
``freeze_action`` on every realm and typed proposals could not be filed.
"""

import ast
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

BACKEND = Path(__file__).parent.parent.parent / "src" / "realm_backend"
sys.path.insert(0, str(BACKEND))
sys.modules.setdefault("_cdk", MagicMock())


def _module_level_re_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import) and any(a.name == "re" for a in node.names):
            yield node.lineno
        if isinstance(node, ast.ImportFrom) and node.module == "re":
            yield node.lineno


def test_no_realm_backend_module_imports_re_at_module_level():
    offenders = [
        f"{p.relative_to(BACKEND)}:{line}"
        for p in sorted(BACKEND.rglob("*.py"))
        if "__pycache__" not in p.parts and "extension_packages" not in p.parts
        for line in _module_level_re_imports(p)
    ]
    assert offenders == [], (
        "import re inside the function that needs it, with a fallback: "
        + ", ".join(offenders)
    )


def _load_with_gutted_re(name):
    real_re = sys.modules["re"]
    gutted = types.ModuleType("re")
    gutted.__dict__.update({k: v for k, v in vars(real_re).items() if k != "compile"})
    sys.modules["re"] = gutted
    sys.modules.pop(name, None)
    try:
        return importlib.import_module(name)
    finally:
        sys.modules["re"] = real_re


def test_proposal_dispatch_loads_and_validates_amounts_without_re():
    pd = _load_with_gutted_re("core.proposal_dispatch")
    base = {"token": "ckBTC", "to_principal": "aaaaa-aa"}
    for amount, ok in (("1", True), ("120", True), ("0", False), ("012", False),
                       ("-5", False), ("1.5", False), ("", False), ("١٢", False),
                       (12, False)):
        _, _, err = pd.freeze_action("transaction", {**base, "amount": amount})
        assert (err is None) is ok, (amount, err)


def test_federal_vote_id_sanitizes_without_re():
    ft = _load_with_gutted_re("core.federal_tally")
    assert ft.build_vote_id(" q-1 é/x_y ", 5, 2) == "fv_q-1___x_y_5_2"
