"""Quarter auto-scaling policy (pure decision logic).

Decides *whether* a federation should spawn a new quarter when a new user is
created. The *how* (direct Casals provisioning) lives in
``core/quarter_scaling`` + ``api/quarter_provisioning``; this module is
intentionally pure so the policy is unit-testable without a replica.

Design (see issue #156):

* The federation codex may define ``should_deploy_quarter(populations, network,
  realm)`` to fully override the policy. It runs sandboxed over plain data
  (issue #265). When it does not, the built-in default applies.
* **Default rule:** scale when *every joinable* quarter has reached **90% of N**
  (i.e. there is no joinable quarter left with headroom). Using only the
  fullest quarter would keep minting forever after the first scale, because
  the old full quarter stays above the threshold.
* **N** = 2000 in production; **10** for the ``test`` / ``staging`` / ``demo``
  environments (so CI exercises sharding without 2000 joins).
* The actual deploy is **non-blocking**: the trigger only sets an idempotent
  "scale in flight" flag on the Realm; a separate async endpoint performs the
  Casals call and clears the flag.

Pure stdlib only (no ``_cdk``) so it imports under plain CPython in tests.
"""

import json
import math

from ic_python_logging import get_logger

logger = get_logger("core.autoscale")

# Capacity per quarter before we consider it "full".
DEFAULT_N = 2000

# Environments that should shard early so sharding is exercised cheaply.
LOW_THRESHOLD_NETWORKS = ("test", "staging", "demo")
LOW_THRESHOLD_N = 10

# Scale when the fullest quarter reaches this fraction of N (headroom so the
# triggering join still lands somewhere).
SCALE_FRACTION = 0.9


def _manifest_environment(realm) -> str:
    """Logical environment from the realm manifest (``staging``, ``production``, …)."""
    try:
        from .demo_notice import host_environment
    except ImportError:
        from core.demo_notice import host_environment

    return host_environment(realm)


def default_threshold_n(network):
    """Return N (per-quarter capacity) for the given environment name."""
    net = (network or "").strip().lower()
    if net in LOW_THRESHOLD_NETWORKS:
        return LOW_THRESHOLD_N
    return DEFAULT_N


def scale_at(n):
    """Population at which scaling should begin: ceil(0.9 * N), min 1."""
    if n <= 0:
        return 0  # 0/unlimited => never auto-scale
    return max(1, int(math.ceil(SCALE_FRACTION * n)))


def should_scale_default(populations, network, n_override=None):
    """Built-in policy: True when every joinable quarter is at/above the threshold.

    We mint a new quarter only when there is *no* joinable quarter with
    headroom left. Using ``max(pops) >= threshold`` alone would keep minting
    forever after the first scale: the old full quarter stays above the
    threshold even though the freshly minted one has room for new joins.

    Args:
        populations: iterable of per-quarter populations (ints) for *joinable*
            quarters only (capital is excluded when ``joinable=false``).
        network: realm network string ("test"/"staging"/"demo"/"ic"/...).
        n_override: explicit N (e.g. from codex config); falls back to env default.

    Returns False for empty input or N<=0 (unlimited / disabled).
    """
    pops = [int(p or 0) for p in (populations or [])]
    if not pops:
        return False
    n = int(n_override) if n_override is not None else default_threshold_n(network)
    threshold = scale_at(n)
    if threshold <= 0:
        return False
    # All joinable quarters are full enough → need another one.
    return min(pops) >= threshold


def resolve_should_scale(populations, network, codex_fn=None, n_override=None,
                         realm=None):
    """Resolve the scaling decision: codex hook if provided, else default.

    ``codex_fn`` (if given) is the federation codex's ``should_deploy_quarter``.
    It is called as ``codex_fn(populations, network, realm)`` and must return a
    truthy value to request scaling. Any exception falls back to the default
    policy so a broken codex never silently disables scaling.
    """
    if codex_fn is not None:
        try:
            return bool(codex_fn(list(populations or []), network, realm))
        except Exception:
            # Fall through to the built-in default on codex error.
            pass
    return should_scale_default(populations, network, n_override=n_override)


# ── Runtime glue (lazy imports; not exercised in pure unit tests) ──────────

def _ggg():
    """Import the ggg package under both the canister ("ggg") and the test
    ("realm_backend.ggg") module layouts."""
    try:
        import ggg as _g
    except ImportError:
        import realm_backend.ggg as _g
    return _g


def _codex_should_deploy_fn(realm):
    """Return a sandboxed ``should_deploy_quarter`` callable, or None.

    The federation policy used to be ``exec()``'d in-process with full
    builtins (issue #265). It now runs over the capability bridge; a missing
    or broken policy returns None so ``resolve_should_scale`` applies the
    built-in default. ``realm`` is accepted for call-site compatibility but
    unused — lookup goes through ``core.codex_hooks``.
    """
    del realm  # resolved inside call_should_deploy_quarter
    try:
        from core import codex_hooks
    except Exception:
        return None

    # Probe without spawning: no source / no function => no override.
    name, source = codex_hooks._federation_codex()
    if not source or "def should_deploy_quarter" not in source:
        return None

    def _fn(populations, network, realm=None):
        # Hand the realm's capacity override to the hook as plain data —
        # sandboxed hooks receive no realm entity (issue #265), so without
        # this the codex policy silently scaled at the env default (P22).
        cap = (quarter_capacity_override(realm) or 0) if realm is not None else 0
        verdict = codex_hooks.call_should_deploy_quarter(
            populations, network, quarter_capacity=cap
        )
        if verdict is None:
            # Signal resolve_should_scale to fall through to the default.
            raise RuntimeError("federation should_deploy_quarter unavailable")
        return verdict

    return _fn


def quarter_populations(realm):
    """Populations of every active or setup quarter (for the scale decision).

    The capital is included only while it still accepts joins (no peer quarters
    yet, or ``is_quarter`` is unset). Once peer quarters exist the capital is
    typically ``joinable=false``, and counting its historical members would
    either force perpetual scaling (``max`` policy) or suppress needed scales
    (``min`` policy). Peer quarters contribute their last-synced ``population``.

    Setup quarters count as reserved capacity (usually pop 0) so autoscale does
    not mint another quarter while bootstrap is still in progress.
    """
    pops = []
    try:
        from _cdk import ic

        self_id = ic.id().to_str()
    except Exception:
        self_id = ""

    peers = []
    try:
        for q in getattr(realm, "quarter_ids", []) or []:
            status = (getattr(q, "status", "") or "setup").strip()
            if status not in ("active", "setup"):
                continue
            cid = (getattr(q, "canister_id", "") or "").strip()
            if not cid or cid == self_id:
                continue
            peers.append(int(getattr(q, "population", 0) or 0))
    except Exception:
        pass

    if peers:
        # Capital no longer takes joins — only peer (joinable) populations matter.
        return peers

    # Lone capital (or quarter evaluating locally): use local User count.
    try:
        pops.append(int(_ggg().User.count()))
    except Exception:
        pass
    return pops


def quarter_capacity_override(realm):
    """Per-realm quarter capacity N from ``manifest_data`` (0/absent = env default).

    Realms tune sharding via the optional manifest block::

        {"scaling": {"quarter_capacity": 2000}}

    Staging's env default (10) exists so CI exercises sharding cheaply, but it
    would mint thousands of canisters on a busy realm — a real realm always
    sets its own capacity here.
    """
    try:
        md = json.loads(getattr(realm, "manifest_data", "") or "{}")
        cap = int((md.get("scaling") or {}).get("quarter_capacity") or 0)
        return cap if cap > 0 else None
    except Exception:
        return None


def maybe_request_quarter_scale():
    """Evaluate the scaling policy and set an idempotent "scale in flight" flag.

    Called after every successful user registration on the capital **and** when a
    quarter pushes an updated population via ``report_quarter_population``.
    performs the Casals provisioning itself (that needs an async endpoint);
    it only records intent so a separate task can act on it. Safe to call
    repeatedly — once the flag is set it is a no-op until cleared.

    Quarters never set the flag: only the capital has the Casals stand /
    ``manifest_data.casals`` needed to mint a sibling. A quarter that set
    ``scale_in_flight`` locally would stick forever (``process_quarter_scaling``
    returns ``blocked`` and the autoscale task self-disables). The capital
    re-evaluates with the federation-wide populations after each sync.

    Returns True iff this call newly requested a scale.
    """
    try:
        Realm = _ggg().Realm
    except Exception:
        return False

    realm = Realm.load("1")
    if not realm:
        return False
    if not bool(getattr(realm, "auto_scale_enabled", True)):
        return False
    # Only the capital provisions. Quarters that previously set this flag
    # (pre-fix) also clear it here so a stuck quarter recovers on the next
    # registration / tick without an operator poke.
    if bool(getattr(realm, "is_quarter", False)):
        if bool(getattr(realm, "scale_in_flight", False)):
            realm.scale_in_flight = False
        return False
    if bool(getattr(realm, "scale_in_flight", False)):
        return False  # idempotent: a deploy is already queued/running

    populations = quarter_populations(realm)
    environment = _manifest_environment(realm)
    codex_fn = _codex_should_deploy_fn(realm)
    if not resolve_should_scale(
        populations,
        environment,
        codex_fn=codex_fn,
        n_override=quarter_capacity_override(realm),
        realm=realm,
    ):
        return False

    realm.scale_in_flight = True
    try:
        from _cdk import ic

        realm.scale_requested_at = str(int(ic.time()))
    except Exception:
        pass
    # Spin up the on-chain provisioning loop. Seeding lives in core: this
    # function runs in contexts where ``from main import ...`` resolves
    # nothing (WASI sandbox / __shell__), and a swallowed failure here leaves
    # scale_in_flight set with no driver task — the exact stuck-scale bug
    # found at the 10k rung. Log, never silence.
    try:
        from core.quarter_bootstrap import ensure_autoscale_task

        if not ensure_autoscale_task():
            logger.error("scale requested but autoscale task seeding failed")
    except Exception as e:
        logger.error(f"autoscale task seeding raised: {e}")
    return True
