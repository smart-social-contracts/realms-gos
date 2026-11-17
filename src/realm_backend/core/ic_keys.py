"""Which of the IC's master keys this realm asks for.

The IC's threshold services (vetKD, Schnorr, ECDSA) hold one master key per kind of network, and what a realm derives
from it is only as private as that key: the test key is held by a test subnet and is not for real data. So every
call that names a key takes its name from here, and the rule is in this one place.
"""

from ggg import Realm

# The networks that are not production. An unset or unknown network counts as production, as it does for the test
# flags (scripts/build_variants/test.py): a mistake then names the production key, which a test subnet refuses, and
# never the test key for real data.
TEST_NETWORKS = ("test", "staging", "demo", "local")


def key_name():
    """The key of the realm's network: ``dfx_test_key`` on a local replica, ``test_key_1`` on a test network, and
    ``key_1`` in production."""
    network = Realm["1"].network
    if network == "local":
        return "dfx_test_key"
    return "test_key_1" if network in TEST_NETWORKS else "key_1"
