from ic_python_db import Boolean, Integer, ManyToOne, OneToMany, OneToOne, String

from db import Json, Row


class Realm(Row):
    __alias__ = "id"
    id = String(default="1")
    name = String(default="")
    manifesto = String(default="")
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
    can_test_mode = Boolean(default=False)
    test_mode = Boolean(default=False)
    test_flags = Json({})  # the test_mode_* switches


class Calendar(Row):
    __alias__ = "id"
    id = String(default="1")
    realm = OneToOne("Realm", "calendar")
    epoch = Integer(default=0)
    fiscal_period = Integer(default=365 * 86400)
    voting_window = Integer(default=7 * 86400)
    codex_release_cycle = Integer(default=0)
    benefit_cycle = Integer(default=30 * 86400)
    service_payment_cycle = Integer(default=30 * 86400)
    license_review_cycle = Integer(default=2 * 365 * 86400)
    custom_cycles = Json({})


class Registry(Row):
    __alias__ = "id"
    id = String()
    description = String(default="")
    principal_id = String(default="")


class Quarter(Row):
    __alias__ = "id"
    id = String()  # canister id
    name = String(default="")
    index = Integer(default=0)
    status = String(default="setup")  # setup | active | suspended | splitting | merging
    population = Integer(default=0)
    codex_id = String(default="")
    codex_version = String(default="")
    last_sync = Integer(default=0)
    last_activity_at = Integer(default=0)


class Forward(Row):
    __alias__ = "id"
    id = String()
    subject = String(indexed=True)
    origin = String(default="")
    destination = String(default="")
    moved_at = Integer(default=0)
    signature = String(default="")


class FederalVote(Row):
    __alias__ = "id"
    id = String()
    origin_quarter = String(default="")
    action = Json({})
    rule = Json({"kind": "majority_of_quarters"})
    hash = String(default="")
    deadline = Integer(default=0)
    status = String(default="open")  # open | passed | failed | cancelled
    tally = Json({})
    legs = OneToMany("FederalVoteLeg", "vote")


class FederalVoteLeg(Row):
    __alias__ = "id"
    id = String()
    vote = ManyToOne("FederalVote", "legs")
    quarter = String(default="")
    proposal_id = String(default="")
    outcome = String(default="")
    yes = Integer(default=0)
    no = Integer(default=0)
    abstain = Integer(default=0)
    eligible = Integer(default=0)
    status = String(default="open")
    hash = String(default="")


class FederationMessage(Row):
    __alias__ = "id"
    id = String()
    topic = String(default="")
    source = String(default="")
    body = Json({})
    response = Json({})


class Codex(Row):
    __alias__ = "id"
    id = String()
    version = String(default="")
    checksum = String(default="")
    status = String(default="active")  # active | inactive | reverted
    manifest = Json({})
    capabilities = Json([])
    source = String(default="")
    installed_at = Integer(default=0)
    previous = String(default="")


class Extension(Row):
    __alias__ = "id"
    id = String()  # name
    version = String(default="")
    status = String(default="installed")
    operations = Json([])
    access = Json({"profiles": ["member"], "departments": []})
    sidebar = Json({})
    manifest = Json({})
    source = String(default="")


class Notification(Row):
    __alias__ = "id"
    id = String()
    recipient = String(indexed=True)  # "User:alice", "Department:health", "Organization:clinic-b"
    topic = String(default="")
    title = String(default="")
    body = String(default="")
    href = String(default="")
    sender = String(default="")
    visibility = String(default="private")
    read_by = Json([])
    at = Integer(default=0)


class Event(Row):
    __alias__ = "id"
    id = String()
    op = String(default="")
    user = String(default="")
    by = String(default="")
    origin = String(default="")
    args = Json({})
    result = Json({})
    at = Integer(default=0)


class KeyEnvelope(Row):
    __alias__ = "id"
    id = String()  # "<scope>|<principal>"
    scope = String(indexed=True)  # "user:alice:private", "dept:Health:doc:DOC-1", "realm:x"
    principal = String(indexed=True)
    wrapped_dek = String(default="")  # the scope's key, IBE-wrapped for this principal
    granted_by = String(default="")
    at = Integer(default=0)


class Document(Row):
    __alias__ = "id"
    id = String()
    department = String(indexed=True)
    title = String(default="")
    ref = String(default="")  # where the ciphertext lives (file registry path)
    hash = String(default="")
    author = String(default="")
    at = Integer(default=0)
