"""Build variant: test. The test-only half of ``production.py``; read it first.

For deterministic identities and fast clocks on test, demo and local realms. Never deploy it to staging or
production: ``status`` reports ``build_variant``, and the wasm holds the marker below, which the packer
refuses to find in a production build.
"""

BUILD_VARIANT = "test"

# Literals, so the packer can find them in the wasm's embedded sources.
TEST_BUILD_MARKER = "REALMS_TEST_BUILD_AUTH_BYPASS"
# The networks where test flags need no install-time permission. Anything else, an unset network included,
# counts as production.
NON_PRODUCTION_NETWORKS = ("local", "test", "demo")


def test_flags_allowed(network, can_test_mode):
    """Whether test mode and test flags may be set: on a non-production network, or when the installer allowed it."""
    return bool(can_test_mode) or (network or "").strip().lower() in NON_PRODUCTION_NETWORKS
