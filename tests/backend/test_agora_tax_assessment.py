"""Agora turns the adopted tax policy into invoices, one page per call.

Runs the codex module against the real ``ggg`` entities: flat taxes bill each
eligible member once per period, land taxes bill each active parcel's owner,
and a second run over the same period bills nobody twice.
"""

import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
AGORA_BACKEND = REPO / "codices" / "codices" / "agora" / "backend"

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(REPO / "src" / "realm_backend"))

from _cdk_stub import ensure_cdk_stub  # noqa: E402

ensure_cdk_stub()

pytestmark = pytest.mark.skipif(
    not AGORA_BACKEND.is_dir(), reason="codices submodule not checked out"
)

NOW = 1_792_000_000  # 2026-10-14
PROPERTY = {"code": "property", "label": "Property tax", "base": "land_area",
            "unit_value": 10, "method": "proportional", "rate_bp": 100,
            "exempt_below": 50, "period": "annual"}
MEMBERSHIP = {"code": "membership", "base": "flat", "method": "fixed", "amount": 5,
              "period": "monthly", "exempt_profiles": ["admin"]}


def load_installed(monkeypatch):
    """Agora's backend as an installed codex: a ``_runtime_ext_agora`` package
    whose modules exist only under it (``core.runtime_extensions``), with the
    backend directory not on ``sys.path``."""
    package_name = "_runtime_ext_agora"
    package = types.ModuleType(package_name)
    package.__path__ = [str(AGORA_BACKEND)]
    package.__package__ = package_name
    monkeypatch.setitem(sys.modules, package_name, package)
    for path in sorted(AGORA_BACKEND.glob("*.py")):
        if path.name in ("entry.py", "__init__.py"):
            continue
        full = f"{package_name}.{path.stem}"
        module = types.ModuleType(full)
        module.__file__ = str(path)
        module.__package__ = package_name
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
        monkeypatch.setitem(sys.modules, full, module)
        setattr(package, path.stem, module)
    return sys.modules[f"{package_name}.tax_assessment"]


@pytest.fixture
def agora(monkeypatch):
    from ic_python_db import Database

    Database.get_instance().clear()
    assert str(AGORA_BACKEND) not in sys.path
    module = load_installed(monkeypatch)
    monkeypatch.setattr(module.ic, "time", lambda: NOW * 1_000_000_000, raising=False)
    monkeypatch.setattr(module.ic, "print", lambda *a: None, raising=False)

    import ggg
    from core import membership

    monkeypatch.setattr(ggg, "user_has_profile", membership.user_has_profile, raising=False)
    return module


def realm_with(taxes, members=3, admins=1, parcels=()):
    from core import taxation
    from ggg import Land, User, UserProfile

    member = UserProfile(name="member", allowed_to="")
    admin = UserProfile(name="admin", allowed_to="all")
    users = []
    for i in range(members):
        users.append(User(id=f"m{i}", profiles=[member]))
    for i in range(admins):
        users.append(User(id=f"a{i}", profiles=[member, admin]))
    for i, (owner, w, h, status) in enumerate(parcels):
        Land(id=f"L{i}", owner_user=User[owner], size_width=w, size_height=h,
             status=status)
    row = taxation.save_draft({"taxes": taxes})
    taxation.adopt_policy({"policy_id": row["id"]})
    return users


MANIFEST = {"currency": {"symbol": "ckUSDC"}}


def run_all(agora, count=2):
    cursor, pages = "", []
    while True:
        out = agora.assess({"cursor": cursor, "count": count}, MANIFEST, "Agora")
        assert out["success"], out
        pages.append(out)
        cursor = out["next_cursor"]
        if not cursor:
            return pages


def test_flat_tax_bills_each_eligible_member_once(agora):
    from ggg import Invoice

    realm_with([MEMBERSHIP], members=3, admins=1)
    pages = run_all(agora)
    assert sum(p["invoices_created"] for p in pages) == 3
    assert sum(p["not_eligible"] for p in pages) == 1, "admins are exempt"

    invoices = Invoice.load_some(1, Invoice.max_id())
    assert {i.user.id for i in invoices} == {"m0", "m1", "m2"}
    meta = json.loads(invoices[0].metadata)
    assert meta == {"type": "tax", "tax": "membership", "period": "2026-10",
                    "policy": meta["policy"]}
    assert invoices[0].amount == 5.0
    assert invoices[0].currency == "ckUSDC"
    assert invoices[0].due_date.startswith("2026-11-13")
    assert len(invoices[0].id) <= 32


def test_running_the_same_period_again_bills_nobody_twice(agora):
    from ggg import Invoice

    realm_with([MEMBERSHIP], members=2, admins=0)
    run_all(agora)
    second = run_all(agora)
    assert sum(p["invoices_created"] for p in second) == 0
    assert sum(p["already_invoiced"] for p in second) == 2
    assert Invoice.count() == 2


def test_land_tax_bills_each_active_parcel_to_its_owner(agora):
    from ggg import Invoice

    realm_with([PROPERTY], members=2, admins=0, parcels=[
        ("m0", 10, 10, "active"),      # value 1000 → 10
        ("m0", 2, 2, "active"),        # value 40 → under exempt_below
        ("m1", 5, 10, "revoked"),      # not active
    ])
    pages = run_all(agora)
    assert [p["phase"] for p in pages][-1] == "parcels"
    created = Invoice.load_some(1, Invoice.max_id())
    assert len(created) == 1
    assert created[0].user.id == "m0"
    assert created[0].amount == pytest.approx(10.0)
    assert json.loads(created[0].metadata)["parcel"] == "L0"
    assert sum(p["nothing_due"] for p in pages) == 1


def test_cursor_walks_members_then_parcels(agora):
    realm_with([MEMBERSHIP, PROPERTY], members=3, admins=0,
               parcels=[("m0", 10, 10, "active")])
    pages = run_all(agora, count=2)
    phases = [(p["phase"], p["next_cursor"]) for p in pages]
    assert phases == [
        ("members", "members:3"),
        ("members", "parcels:1"),
        ("parcels", ""),
    ]


def test_needs_an_adopted_policy_and_a_currency(agora):
    out = agora.assess({}, MANIFEST, "Agora")
    assert out["success"] is False and "adopted" in out["error"]

    realm_with([MEMBERSHIP])
    out = agora.assess({}, {}, "Agora")
    assert out["success"] is False and out["error_code"] == "no_treasury_token"


def test_unknown_cursor_is_an_error(agora):
    realm_with([MEMBERSHIP])
    with pytest.raises(ValueError, match="cursor"):
        agora.assess({"cursor": "everyone:1"}, MANIFEST, "Agora")
