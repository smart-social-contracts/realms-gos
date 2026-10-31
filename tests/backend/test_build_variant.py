"""The production wasm must not contain the test-only code paths.

Runtime gating can only ever say "this setting is off". It cannot say "this code
is not here", so it stays one stale database row, one compromised admin session,
or one in-process extension away from being switched back on. Compiling the two
variants separately is what turns that into a property of the artifact.

basilisk copies the whole ``src/realm_backend`` tree into the WASM, so the test
variant is kept in ``scripts/build_variants/`` — outside the packed tree — and
copied over ``core/build_variant.py`` at pack time. These tests hold that
arrangement in place; tests/deployed/test_build_variants.py checks the packed
wasms themselves on PocketIC. See realms-gos#395.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BACKEND = REPO_ROOT / "src" / "realm_backend"
VARIANT_SOURCES = REPO_ROOT / "scripts" / "build_variants"
ACTIVE_VARIANT = BACKEND / "core" / "build_variant.py"

VARIANT_API = ("BUILD_VARIANT", "test_flags_allowed")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def production():
    return _load(VARIANT_SOURCES / "production.py", "_variant_production")


@pytest.fixture(scope="module")
def test_variant():
    return _load(VARIANT_SOURCES / "test.py", "_variant_test")


@pytest.fixture(scope="module")
def packer():
    return _load(REPO_ROOT / "scripts" / "pack_realm_backend.py", "_packer")


def test_both_variants_expose_the_same_api(production, test_variant):
    """A missing symbol in one variant would be an ImportError at runtime."""
    for name in VARIANT_API:
        assert hasattr(production, name), f"production variant lacks {name}"
        assert hasattr(test_variant, name), f"test variant lacks {name}"


def test_production_variant_allows_no_test_flag(production):
    assert production.BUILD_VARIANT == "production"
    for network in ("local", "test", "demo", "ic", ""):
        for can_test_mode in (False, True):
            assert production.test_flags_allowed(network, can_test_mode) is False


@pytest.mark.parametrize("network, can_test_mode, allowed", [
    ("local", False, True),
    ("test", False, True),
    ("demo", False, True),
    (" Local ", False, True),
    ("", False, False),
    (None, False, False),
    ("ic", False, False),
    ("staging", False, False),
    ("prod", False, False),
    ("production", False, False),
    ("mainnet", False, False),
    ("ic-1", False, False),
    ("", True, True),
    ("ic", True, True),
])
def test_test_variant_gate_fails_closed(test_variant, network, can_test_mode, allowed):
    """Only a named non-production network, or the installer's can_test_mode, opens the gate."""
    assert test_variant.BUILD_VARIANT == "test"
    assert test_variant.test_flags_allowed(network, can_test_mode) is allowed


def test_only_the_test_variant_defines_the_test_only_symbols(packer):
    for symbol in packer.TEST_ONLY_SYMBOLS:
        assert symbol.decode() in (VARIANT_SOURCES / "test.py").read_text()
        assert symbol.decode() not in (VARIANT_SOURCES / "production.py").read_text()


def test_the_scan_checks_both_directions(packer, tmp_path):
    """A scan that could only pass would report a guarantee it is not testing."""
    marked = tmp_path / "marked.wasm"
    marked.write_bytes(b"\0asm" + b" ".join(packer.TEST_ONLY_SYMBOLS))
    clean = tmp_path / "clean.wasm"
    clean.write_bytes(b"\0asm no test code here")
    packer.verify_build_variant(marked, "test")
    packer.verify_build_variant(clean, "production")
    with pytest.raises(SystemExit):
        packer.verify_build_variant(marked, "production")
    with pytest.raises(SystemExit):
        packer.verify_build_variant(clean, "test")


def test_production_is_the_default_build(packer):
    assert packer.DEFAULT_VARIANT == "production", (
        "the unadorned build must be the safe one; a test build has to be asked for"
    )


def test_the_committed_active_variant_is_production():
    """A test variant committed by accident would ship on the next deploy.

    The pack script restores production after building, so a diff here means
    something went wrong rather than someone iterating.
    """
    assert ACTIVE_VARIANT.read_text() == (VARIANT_SOURCES / "production.py").read_text(), (
        "src/realm_backend/core/build_variant.py must match "
        "scripts/build_variants/production.py in a clean checkout"
    )


def test_the_test_variant_is_not_inside_the_packed_source_tree(packer):
    """The whole point: what is not selected is not compiled in."""
    packed_copies = [
        p for p in BACKEND.rglob("*.py")
        for symbol in packer.TEST_ONLY_SYMBOLS
        if symbol.decode() in p.read_text()
    ]
    assert not packed_copies, (
        "a test-only symbol appears inside src/realm_backend, so it would be packed "
        "into a production WASM: " + ", ".join(str(p) for p in packed_copies)
    )


# --- Frontend variant -------------------------------------------------------
#
# The backend variant removes the code that honours the II bypass; the frontend
# variant removes the login UI that would call it. They are separate build
# systems, so each needs its own guard.

FRONTEND = REPO_ROOT / "src" / "realm_frontend"
FRONTEND_SENTINEL = "REALMS_TEST_BUILD_AUTH_BYPASS"


def test_frontend_build_defaults_to_production():
    """An unset REALMS_BUILD_VARIANT must not produce a test bundle."""
    config = (FRONTEND / "vite.config.js").read_text()
    assert "__REALMS_TEST_BUILD__" in config, (
        "the frontend needs a compile-time constant for the variant; a runtime "
        "check would leave the test login code in the production bundle"
    )
    assert "REALMS_BUILD_VARIANT === 'test'" in config, (
        "the test variant must be opt-in by exact value, so anything unset or "
        "misspelled builds production"
    )


def test_frontend_sentinel_is_reachable_from_the_gated_path():
    """A sentinel nothing reads is tree-shaken from *both* variants.

    That would make the production scan pass vacuously, which is worse than no
    scan at all: it reports a guarantee it is not testing. Keep the sentinel
    wired into the deterministic-identity login itself.
    """
    declared = (FRONTEND / "src" / "lib" / "test-identities.js").read_text()
    assert FRONTEND_SENTINEL in declared

    auth = (FRONTEND / "src" / "lib" / "auth.js").read_text()
    assert "TEST_BUILD_AUTH_SENTINEL" in auth, (
        "src/lib/auth.js must read TEST_BUILD_AUTH_SENTINEL inside the test-only "
        "login path, otherwise scripts/check_frontend_variant.py proves nothing"
    )


def test_frontend_test_identity_creation_is_gated():
    """Both entry points into deterministic identities refuse in production."""
    for rel in ("src/lib/auth.js", "src/lib/test-identities.js"):
        source = (FRONTEND / rel).read_text()
        assert "__REALMS_TEST_BUILD__" in source, f"{rel} is not variant-gated"

    # The II-bypass flag reported by the backend must not be able to switch the
    # frontend back on by itself.
    config_js = (FRONTEND / "src" / "lib" / "config.js").read_text()
    assert "if (!__REALMS_TEST_BUILD__) return false;" in config_js, (
        "getTestModeIIBypass must fail closed in a production bundle"
    )
