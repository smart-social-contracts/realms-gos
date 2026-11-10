from ic_python_db import Boolean, Integer, OneToOne, String

from core.db import Json, Row
from core.ratio import PERCENT, upgrade_federal_rule


class Realm(Row):
    __alias__ = "id"
    __version__ = 2  # 2: the voting rules in hundred-millionths, under GGG's names
    id = String(default="1")
    name = String(default="")
    manifesto = String(default="")
    terms = String(default="")  # the realm's terms text, which a member accepts on joining (Realms GOS adds it)
    welcome_message = String(default="")
    status = String(default="setup")  # setup | alpha | beta | production | deprecation | terminated
    manifest_data = Json({})
    calendar = OneToOne("Calendar", "realm")
    treasury = OneToOne("Treasury", "realm")
    accounting_currency = String(default="")
    accounting_currency_decimals = Integer(default=8)
    principal_id = String(default="")
    frontend_canister_id = String(default="")
    token_canister_id = String(default="")
    nft_canister_id = String(default="")
    file_registry_canister_id = String(default="")
    marketplace_canister_id = String(default="")
    installer_canister_id = String(default="")
    shared_tokens_json = Json({})
    is_quarter = Boolean(default=False)
    is_capital = Boolean(default=True)
    federation_realm_id = String(default="")
    federation_codex = String(default="")
    quarter_join_mode = String(default="auto")
    auto_scale_enabled = Boolean(default=True)
    scale_in_flight = Boolean(default=False)
    scale_requested_at = Integer(default=0)
    bootstrap_state = Json({})
    sync_state = Json({})
    codex_install_state = Json({})
    last_activity_at = Integer(default=0)
    trusted_principals = Json([])
    require_marketplace_approval = Boolean(default=True)
    trusted_approvers = Json([])
    open_registration = Boolean(default=False)
    ai_assistant_enabled = Boolean(default=True)
    logo_url = String(default="")
    background_image_url = String(default="")
    primary_color = String(default="")
    installed_version = String(default="")
    network = String(default="")
    timezone = String(default="UTC")  # an IANA name (core.zones); UTC until the realm sets one
    languages = Json([])  # BCP 47 tags, the first the default; none until the realm sets them
    can_test_mode = Boolean(default=False)
    test_mode = Boolean(default=False)
    test_flags = Json({})  # the test_mode_* switches
    it_team = String(default="")  # the department that reverts a failing codex fast (§10.2): the admins designate it

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        manifest = obj.get("manifest_data") or {}
        rules = manifest.get("governance", {})
        # The old names show a rule still in whole percents.
        for old, new in (("threshold", "voting_threshold"), ("quorum_percent", "voting_quorum")):
            if old in rules:
                rules[new] = rules.pop(old) * PERCENT
        federation = manifest.get("federation", {})
        if "rule" in federation:
            federation["rule"] = upgrade_federal_rule(federation["rule"])
        return obj
