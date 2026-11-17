"""The canister's ``re`` has no ``compile``.

Basilisk registers every realm module as a lazy module whose body runs on
first attribute access. If that body raises ``AttributeError`` (as
``re.compile`` does in the canister), every ``getattr(module, name, None)``
returns None. That is how ``core.proposal_dispatch`` silently lost
``freeze_action`` on every realm and typed proposals could not be filed.

So no module imports ``re`` at module level: a module that needs it imports it inside the function.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).parent.parent.parent / "src" / "realm_backend"


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
