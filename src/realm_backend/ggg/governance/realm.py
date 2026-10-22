from ic_python_db import Boolean, Entity, Integer, OneToMany, OneToOne, String, TimestampedMixin
from ic_python_logging import get_logger
from ..system.constants import STATUS_MAX_LENGTH

logger = get_logger("entity.realm")


class RealmStatus:
    SETUP = "setup"              # Creator-only configuration stage (pre-alpha)
    ALPHA = "alpha"              # Gathering interest, deposits refundable
    BETA = "beta"                # Deposits locked, auctions & land bidding
    PRODUCTION = "production"    # Fully operational
    DEPRECATION = "deprecation"  # Winding down, no new members
    TERMINATED = "terminated"    # Closed, read-only archive


class Realm(Entity, TimestampedMixin):
    __alias__ = "name"
    __version__ = 12
    name = String(min_length=2, max_length=256)
    manifesto = String(max_length=256)

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        if from_version < 2:
            obj["manifesto"] = obj.pop("description", "")
        if from_version < 3:
            if obj.get("ai_assistant_enabled") is None:
                obj["ai_assistant_enabled"] = True
        if from_version < 4:
            obj.setdefault("auto_scale_enabled", True)
            obj.setdefault("scale_in_flight", False)
            obj.setdefault("scale_requested_at", "")
        if from_version < 5:
            obj.setdefault("bootstrap_state", "")
        if from_version < 6:
            # Realms predating the trust policy adopt the secure default.
            obj.setdefault("require_marketplace_approval", True)
            obj.setdefault("trusted_approvers", "")
        if from_version < 7:
            obj.setdefault("can_test_mode", False)
        if from_version < 8:
            obj.setdefault("sync_state", "")
        if from_version < 9:
            from core.demo_notice import seed_host_flag_defaults

            seed_host_flag_defaults(obj)
        if from_version < 10:
            obj.setdefault("codex_install_state", "")
        if from_version < 11:
            # test_mode_skip_authentication disabled every permission check and
            # no longer exists. Drop any stored value so it cannot be read back.
            obj.pop("test_mode_skip_authentication", None)
        if from_version < 12:
            obj.setdefault("last_activity_at", 0)
        return obj
    welcome_message = String(max_length=1024)  # Welcome message displayed on landing page
    status = String(max_length=STATUS_MAX_LENGTH, default=RealmStatus.SETUP)
    manifest_data = String(max_length=4096, default="{}")
    calendar = OneToOne("Calendar", "realm")
    treasury = OneToOne("Treasury", "realm")
    funds = OneToMany("Fund", "realm")
    justice_systems = OneToMany("JusticeSystem", "realm")
    accounting_currency = String(max_length=16, default="")
    accounting_currency_decimals = Integer(default=8)
    principal_id = String(max_length=64)
    # Canister IDs for this realm (set post-deployment via set_canister_config)
    frontend_canister_id = String(max_length=64)
    token_canister_id = String(max_length=64)
    nft_canister_id = String(max_length=64)
    # Shared infrastructure canister IDs (set by the installer from casals.json)
    file_registry_canister_id = String(max_length=64)
    marketplace_canister_id = String(max_length=64)
    # {symbol: {ledger, indexer, decimals}} — shared ledgers this realm may adopt
    shared_tokens_json = String(max_length=4096, default="{}")
    # Quarter/Federation fields (dormant for single-quarter realms)
    is_quarter = Boolean(default=False)
    is_capital = Boolean(default=False)  # This quarter coordinates federation governance
    federation_realm_id = String(max_length=64)  # parent realm ID if this is a quarter
    quarter_ids = OneToMany("Quarter", "federation")
    federation_codex = OneToOne("Codex", "federation")
    # Auto-scaling / sharding (issue #156). When enabled, a new quarter deploy
    # is requested once the fullest quarter reaches 90% of N (see core.autoscale).
    auto_scale_enabled = Boolean(default=True)
    # Idempotent guard: True while a quarter provisioning job is queued/running.
    # Set by the user-registration trigger, cleared by the async provisioning
    # endpoint on success/failure so the next threshold crossing can re-trigger.
    scale_in_flight = Boolean(default=False)
    scale_requested_at = String(max_length=32, default="")
    # Quarter-local self-bootstrap progress (issue #156). JSON install plan +
    # cursor persisted on a freshly minted quarter; the recurring TaskManager
    # task in core.quarter_bootstrap installs one item per tick from this state.
    bootstrap_state = String(max_length=8192, default="")
    # Quarter-local codex-sync progress (issue #295). JSON install plan +
    # cursor persisted separately from first-boot bootstrap so an approved sync
    # ballot cannot clobber or be clobbered by bootstrap_state, and re-seeding
    # the sync task does not reset the first-boot plan.
    sync_state = String(max_length=8192, default="")
    # Codex install job cursor (multi-message IC0522-safe install).
    codex_install_state = String(max_length=8192, default="")
    # When a person last changed something here, in seconds (core.activity).
    last_activity_at = Integer(default=0)
    # Canister id of the realm_installer that provisioned this realm. Used for
    # bootstrap-admin bypass after setup. Empty on realms not wired to an installer.
    installer_canister_id = String(max_length=64, default="")
    # Comma-separated canister principal IDs trusted for inter-canister calls
    # (DAO controllers, AI agents, parent realms). These bypass User-based access checks.
    trusted_principals = String(max_length=2048, default="")
    # Marketplace trust policy (issue #267). When True (default) the realm
    # installs only extensions and codices carrying an approval from a trusted
    # approver, and refuses content whose registry cannot prove one. Turning it
    # off means accepting arbitrary unreviewed code, so it is governed by its
    # own operation (realm.configure.trust_policy) rather than realm admin.
    require_marketplace_approval = Boolean(default=True)
    # Comma-separated principal ids whose approvals this realm honours.
    # Empty => trust only the realm's configured marketplace_canister_id, so a
    # third party approving its own package does not make it installable here.
    trusted_approvers = String(max_length=1024, default="")
    # When False (default), all users must present an invite code to join.
    # When True, anyone can join as member without a code. Admin always requires a code.
    open_registration = Boolean(default=False)
    # Federation join policy (capital-only). Controls how the join page picks a
    # target quarter for an open (codeless) join once sub-quarters exist:
    #   "auto"   -> impose the newest active quarter (default)
    #   "choice" -> let the user pick from the quarter directory
    # Invite-link joins always target the quarter encoded in the link, so this
    # only affects open/self-registration. Unused on leaf quarters.
    quarter_join_mode = String(max_length=16, default="auto")
    # When True (default), Explain actions and related realm-context hooks are available.
    # The chat UI lives on the mundus registry (RegistryAssistant), not llm_chat.
    ai_assistant_enabled = Boolean(default=True)
    # Branding URLs (optional; frontend falls back to static /images/ assets)
    logo_url = String(max_length=512, default="")
    background_image_url = String(max_length=512, default="")
    # Deployed version (set by set_canister_config or CI post-deploy)
    installed_version = String(max_length=32, default="")
    # IC network this realm is deployed on (e.g. "test", "staging", "demo", "ic")
    network = String(max_length=16, default="")
    # Test mode flags (set via set_canister_config; rejected on production
    # network ic/production unless can_test_mode is set)
    can_test_mode = Boolean(default=False)
    test_mode = Boolean(default=False)
    test_mode_ii_bypass = Boolean(default=False)
    test_mode_user_self_registration = Boolean(default=False)
    test_mode_demo_data = Boolean(default=False)
    test_mode_skip_terms = Boolean(default=False)
    test_mode_skip_passport_zkproof = Boolean(default=False)
    # Host go-live flags (gos.earth). Staging/demo default on via migrate +
    # descriptors; do not strip an already-configured treasury ledger.
    test_mode_disable_monetary_tokens = Boolean(default=False)
    test_mode_demo_notice = Boolean(default=False)
    # JSON object of locale → notice body. Empty English falls back to Legal seed.
    demo_notice_body = String(max_length=8192, default="")
