from ic_python_db import Entity, ManyToMany, String, TimestampedMixin
from ic_python_logging import get_logger

logger = get_logger("entity.user_profile")


class Operations:
    ALL = "all"

    # User management
    USER_ADD = "user.add"
    USER_EDIT = "user.edit"
    USER_DELETE = "user.delete"
    USER_UPDATE_STATUS = "user.update_status"
    # Read member profiles, lists, and notification history (member_manager,
    # migration_console)
    USER_VIEW = "user.view"
    # Create/revoke/list registration codes and invites (role_manager,
    # migration_console)
    INVITE_MANAGE = "invite.manage"

    # Department management (wire IDs keep the legacy "organization." prefix
    # because they are stored in existing Permission grants)
    ORGANIZATION_ADD = "organization.add"
    ORGANIZATION_EDIT = "organization.edit"
    ORGANIZATION_DELETE = "organization.delete"

    # Transfer / Finance
    TRANSFER_CREATE = "transfer.create"
    TRANSFER_REVERT = "transfer.delete"
    INVOICE_REFRESH = "invoice.refresh"
    # Read vault balances, transactions, and subaccounts (vault extension)
    TREASURY_VIEW = "treasury.view"
    # Subaccount registration and ledger-sync configuration
    TREASURY_MANAGE = "treasury.manage"
    NFT_MINT = "nft.mint"
    # Registry-authority override of NFT ownership (judicial/governance):
    # force-transfer to a new owner, or freeze/unfreeze during a dispute.
    NFT_FORCE_TRANSFER = "nft.force_transfer"
    NFT_FREEZE = "nft.freeze"
    # Monetary-authority override of fungible token balances (ERC-3643-style):
    # forced transfer between accounts, or freeze/unfreeze of an account.
    TOKEN_FORCE_TRANSFER = "token.force_transfer"
    TOKEN_FREEZE = "token.freeze"
    LICENSE_ISSUE = "license.issue"
    LICENSE_REVOKE = "license.revoke"

    # Task management
    TASK_CREATE = "task.create"
    TASK_EDIT = "task.edit"
    TASK_DELETE = "task.delete"
    TASK_RUN = "task.run"
    TASK_SCHEDULE = "task.schedule"
    TASK_CANCEL = "task.cancel"
    # Read tasks, executions, and logs (task_monitor)
    TASK_VIEW = "task.view"

    # Realm administration
    REALM_ADMIN = "realm.admin"
    REALM_UPGRADE = "realm.upgrade"
    REALM_CONFIGURE = "realm.configure"
    REALM_CONFIGURE_BRANDING = "realm.configure.branding"
    REALM_CONFIGURE_CODEX = "realm.configure.codex"
    REALM_CONFIGURE_INFRASTRUCTURE = "realm.configure.infrastructure"
    REALM_CONFIGURE_TOKENS = "realm.configure.tokens"
    # Whether this realm still requires marketplace approval for the code it
    # installs, and whose approvals it honours. Separate from realm.configure
    # because relaxing it admits unreviewed code into the realm.
    REALM_CONFIGURE_TRUST_POLICY = "realm.configure.trust_policy"
    REALM_REGISTER = "realm.register"
    QUARTER_REGISTER = "quarter.register"
    QUARTER_DEREGISTER = "quarter.deregister"
    QUARTER_CONFIGURE = "quarter.configure"
    QUARTER_SECEDE = "quarter.secede"
    QUARTER_JOIN_FEDERATION = "quarter.join_federation"
    SHELL_EXECUTE = "shell.execute"
    # System health monitoring: cycles, memory, DB stats (system_info)
    REALM_MONITOR = "realm.monitor"

    # Governance
    # Approve/reject Baton orchestration actions (managed upgrades / asset
    # provisions) on behalf of this realm. The codex decides which profiles
    # carry it: admins (dominion), organization representatives (agora), or
    # every member (syntropia).
    ORCHESTRATION_APPROVE = "orchestration.approve"
    MANDATE_CREATE = "mandate.create"
    MANDATE_ASSIGN_EXECUTOR = "mandate.assign_executor"
    PROPOSAL_CREATE = "proposal.create"
    PROPOSAL_VOTE = "proposal.vote"
    # Proposal lifecycle actions (open voting, finalize after deadline,
    # approve/execute) — deadline/policy rules are enforced in-code.
    PROPOSAL_MANAGE = "proposal.manage"
    FEDERAL_VOTE_PROPOSE = "federal_vote.propose"
    FEDERAL_VOTE_MANAGE = "federal_vote.manage"
    CONTRACT_CREATE_UNDER_MANDATE = "contract.create_under_mandate"
    SCOPE_AUTHORIZE = "scope.authorize"
    GOVERNANCE_UPDATE = "governance.update"
    PERMISSION_VIEW = "permission.view"
    PERMISSION_REVOKE = "permission.revoke"

    # Role / permission management
    ROLE_ASSIGN = "role.assign"
    ROLE_REVOKE = "role.revoke"
    PERMISSION_GRANT = "permission.grant"

    # Organization governance (issue #240) — org-over-org and policy
    ORG_CREATE = "org.create"
    ORG_APPOINT = "org.appoint"
    ORG_EXPEL = "org.expel"
    ORG_SET_POLICY = "org.set_policy"
    ORG_GRANT_AUTHORITY = "org.grant_authority"
    ORG_REVOKE_AUTHORITY = "org.revoke_authority"
    ORG_MANAGE_BUDGET = "org.manage_budget"
    # Department membership self-management (add/remove members, positions).
    # Department-head/manager rules are enforced in-code by the extensions.
    ORG_MANAGE_MEMBERS = "org.manage_members"

    # Judicial administration
    DISPUTE_CREATE = "dispute.create"
    DISPUTE_VIEW = "dispute.view"
    DISPUTE_ACCEPT = "dispute.accept"
    DISPUTE_REJECT = "dispute.reject"
    DISPUTE_ASSIGN = "dispute.assign"
    DISPUTE_VIEW_ALL = "dispute.view_all"
    EVIDENCE_EVALUATE = "evidence.evaluate"
    RESOLUTION_DRAFT = "resolution.draft"
    RESOLUTION_ISSUE = "resolution.issue"
    RESOLUTION_LINK_CONTRACT = "resolution.link_contract"
    RESOLUTION_MODIFY_TERMS = "resolution.modify_terms"
    RESOLUTION_FINALIZE = "resolution.finalize"
    APPEAL_ALLOW = "appeal.allow"

    # Judicial execution
    TRADE_EXECUTE = "trade.execute"
    FINE_APPLY = "fine.apply"
    ACCESS_REVOKE = "access.revoke"
    CONTRACT_TERMINATE = "contract.terminate"
    RESOURCE_REASSIGN = "resource.reassign"
    INSTRUMENT_LOCK = "instrument.lock"
    NOTIFICATION_SEND = "notification.send"
    RESOLUTION_QUERY = "resolution.query"
    ENFORCEMENT_ESCALATE = "enforcement.escalate"
    ENFORCEMENT_RECORD = "enforcement.record"

    # Extensions
    EXTENSION_CALL = "extension.call"
    EXTENSION_SYNC_CALL = "extension.sync_call"
    EXTENSION_ASYNC_CALL = "extension.async_call"
    EXTENSION_INSTALL = "extension.install"
    EXTENSION_UNINSTALL = "extension.uninstall"

    # Codex packages
    CODEX_INSTALL = "codex.install"
    CODEX_UNINSTALL = "codex.uninstall"
    CODEX_REVERT = "codex.revert"

    # Data visibility — read-scoped operations used by extension
    # entry_access gates (permission-based cutover of the old
    # "member"/"admin" levels).
    REALM_DATA_VIEW = "realm.data_view"

    # Raw database access (admin_dashboard, erd_explorer). db.manage can
    # mutate any entity, including users and permissions — treat it as
    # near-admin power.
    DB_EXPORT = "db.export"
    DB_MANAGE = "db.manage"

    # Demo/simulation controls across all extensions (demo_simulator and
    # the demo_* functions). Production realms simply never grant it.
    DEMO_MANAGE = "demo.manage"

    # Documents (department document store)
    DOCUMENT_MANAGE = "document.manage"

    # Procurement bidding
    BID_SUBMIT = "bid.submit"
    BID_EVALUATE = "bid.evaluate"

    # Identity attestation (codex-driven identity flows; registrar rules
    # are enforced in-code by the codex)
    IDENTITY_SUBMIT = "identity.submit"
    IDENTITY_REVIEW = "identity.review"

    # Self-service (any authenticated user)
    SELF_JOIN = "self.join"
    SELF_UPDATE_PUBLIC_PROFILE = "self.update_public_profile"
    SELF_UPDATE_PRIVATE_DATA = "self.update_private_data"
    SELF_CHANGE_QUARTER = "self.change_quarter"
    SELF_INVOICE_REFRESH = "self.invoice_refresh"
    # Read own data (dashboard, invoices, notifications, personal data)
    SELF_DATA_VIEW = "self.data_view"
    # Manage own records (payment accounts, own notifications, own zones)
    SELF_DATA_MANAGE = "self.data_manage"


OPERATIONS_CATALOG = {
    "all": {"category": "Super", "description": "Full unrestricted administrative access to every operation"},

    "user.add": {"category": "User Management", "description": "Register new users in the realm"},
    "user.edit": {"category": "User Management", "description": "Edit user profile information"},
    "user.delete": {"category": "User Management", "description": "Remove a user from the realm"},
    "user.update_status": {"category": "User Management", "description": "Change a user's active/suspended status"},
    "user.view": {"category": "User Management", "description": "View member profiles, lists, and notification history"},
    "invite.manage": {"category": "User Management", "description": "Create, revoke, and list registration codes and invites"},

    "organization.add": {"category": "Departments", "description": "Create a new department"},
    "organization.edit": {"category": "Departments", "description": "Edit department details"},
    "organization.delete": {"category": "Departments", "description": "Delete a department"},
    "document.manage": {"category": "Departments", "description": "Manage department documents and document store"},

    "transfer.create": {"category": "Finance", "description": "Create token transfers between accounts"},
    "transfer.delete": {"category": "Finance", "description": "Revert or cancel a pending transfer"},
    "invoice.refresh": {"category": "Finance", "description": "Recalculate and refresh invoice balances"},
    "treasury.view": {"category": "Finance", "description": "View vault balances, transactions, and subaccounts"},
    "treasury.manage": {"category": "Finance", "description": "Manage subaccounts and ledger-sync configuration"},
    "nft.mint": {"category": "Finance", "description": "Mint new NFT tokens (e.g. land parcels)"},
    "nft.force_transfer": {"category": "Finance", "description": "Force-transfer NFT ownership (registry-authority override)"},
    "nft.freeze": {"category": "Finance", "description": "Freeze or unfreeze NFTs during a dispute"},
    "token.force_transfer": {"category": "Finance", "description": "Force-transfer fungible token balances between accounts"},
    "token.freeze": {"category": "Finance", "description": "Freeze or unfreeze a token account"},
    "license.issue": {"category": "Finance", "description": "Issue a license to a user or organization"},
    "license.revoke": {"category": "Finance", "description": "Revoke an issued license"},
    "bid.submit": {"category": "Finance", "description": "Submit procurement bids"},
    "bid.evaluate": {"category": "Finance", "description": "Evaluate and score procurement bids"},

    "task.create": {"category": "Tasks", "description": "Create background tasks"},
    "task.edit": {"category": "Tasks", "description": "Edit task parameters"},
    "task.delete": {"category": "Tasks", "description": "Delete a task"},
    "task.run": {"category": "Tasks", "description": "Manually trigger a task to run"},
    "task.schedule": {"category": "Tasks", "description": "Schedule a task for periodic execution"},
    "task.cancel": {"category": "Tasks", "description": "Cancel a running or scheduled task"},
    "task.view": {"category": "Tasks", "description": "View tasks, executions, and logs"},

    "realm.admin": {"category": "Realm Administration", "description": "Full realm administrative access"},
    "realm.upgrade": {"category": "Realm Administration", "description": "Upgrade the realm canister to a new version"},
    "realm.configure": {"category": "Realm Administration", "description": "Change realm configuration settings"},
    "realm.configure.branding": {"category": "Realm Administration", "description": "Change realm logo, background image, and primary color"},
    "realm.configure.codex": {"category": "Realm Administration", "description": "Configure the governance codex"},
    "realm.configure.infrastructure": {"category": "Realm Administration", "description": "Configure infrastructure settings (registries, etc.)"},
    "realm.configure.tokens": {"category": "Realm Administration", "description": "Configure realm token settings"},
    "realm.configure.trust_policy": {"category": "Realm Administration", "description": "Configure marketplace approval and trust policy"},
    "realm.register": {"category": "Realm Administration", "description": "Register the realm with the registry"},
    "realm.data_view": {"category": "Realm Administration", "description": "Read realm-wide data exposed by extensions"},
    "realm.monitor": {"category": "Realm Administration", "description": "Monitor system health, cycles, memory, and DB stats"},
    "quarter.register": {"category": "Realm Administration", "description": "Register a new quarter (sub-realm)"},
    "quarter.deregister": {"category": "Realm Administration", "description": "Remove a quarter from the realm"},
    "quarter.configure": {"category": "Realm Administration", "description": "Configure quarter settings"},
    "quarter.secede": {"category": "Realm Administration", "description": "Allow a quarter to secede from the realm"},
    "quarter.join_federation": {"category": "Realm Administration", "description": "Join a federation of realms"},
    "shell.execute": {"category": "Realm Administration", "description": "Execute shell commands on the canister (developer)"},
    "db.export": {"category": "Realm Administration", "description": "Export raw database data"},
    "db.manage": {"category": "Realm Administration", "description": "Mutate any entity in the database (near-admin power)"},

    "orchestration.approve": {"category": "Governance", "description": "Approve or reject Baton orchestration actions for this realm"},
    "mandate.create": {"category": "Governance", "description": "Create governance mandates"},
    "mandate.assign_executor": {"category": "Governance", "description": "Assign an executor to a mandate"},
    "proposal.create": {"category": "Governance", "description": "Submit new governance proposals"},
    "proposal.vote": {"category": "Governance", "description": "Vote on governance proposals"},
    "proposal.manage": {"category": "Governance", "description": "Manage proposal lifecycle (open voting, finalize, execute)"},
    "federal_vote.propose": {"category": "Governance", "description": "Propose a realm-wide federal vote"},
    "federal_vote.manage": {"category": "Governance", "description": "Cancel a federal vote before its deadline"},
    "contract.create_under_mandate": {"category": "Governance", "description": "Create contracts under an active mandate"},
    "scope.authorize": {"category": "Governance", "description": "Authorize governance scopes"},
    "governance.update": {"category": "Governance", "description": "Update governance rules and parameters"},
    "permission.view": {"category": "Governance", "description": "View user permissions and access details"},
    "permission.revoke": {"category": "Governance", "description": "Revoke permissions from users"},
    "org.create": {"category": "Governance", "description": "Create organizations within the realm"},
    "org.appoint": {"category": "Governance", "description": "Appoint organization leaders and representatives"},
    "org.expel": {"category": "Governance", "description": "Expel members from an organization"},
    "org.set_policy": {"category": "Governance", "description": "Set organization policies"},
    "org.grant_authority": {"category": "Governance", "description": "Grant authority within an organization"},
    "org.revoke_authority": {"category": "Governance", "description": "Revoke authority within an organization"},
    "org.manage_budget": {"category": "Governance", "description": "Manage organization budgets"},
    "org.manage_members": {"category": "Governance", "description": "Add or remove organization members and positions"},
    "identity.submit": {"category": "Governance", "description": "Submit identity attestation requests"},
    "identity.review": {"category": "Governance", "description": "Review and decide identity attestation requests"},

    "role.assign": {"category": "Roles & Permissions", "description": "Assign profiles/roles to users"},
    "role.revoke": {"category": "Roles & Permissions", "description": "Revoke profiles/roles from users"},
    "permission.grant": {"category": "Roles & Permissions", "description": "Grant fine-grained permissions to users"},

    "dispute.create": {"category": "Justice", "description": "File a new dispute or complaint"},
    "dispute.view": {"category": "Justice", "description": "View disputes you are party to"},
    "dispute.accept": {"category": "Justice", "description": "Accept a dispute for adjudication"},
    "dispute.reject": {"category": "Justice", "description": "Reject a dispute filing"},
    "dispute.assign": {"category": "Justice", "description": "Assign a dispute to a judge"},
    "dispute.view_all": {"category": "Justice", "description": "View all disputes in the realm"},
    "evidence.evaluate": {"category": "Justice", "description": "Evaluate submitted evidence"},
    "resolution.draft": {"category": "Justice", "description": "Draft a dispute resolution"},
    "resolution.issue": {"category": "Justice", "description": "Issue an official resolution"},
    "resolution.link_contract": {"category": "Justice", "description": "Link a contract to a resolution"},
    "resolution.modify_terms": {"category": "Justice", "description": "Modify terms of a resolution"},
    "resolution.finalize": {"category": "Justice", "description": "Finalize and close a resolution"},
    "appeal.allow": {"category": "Justice", "description": "Allow an appeal to a resolution"},

    "trade.execute": {"category": "Enforcement", "description": "Execute trades as part of enforcement"},
    "fine.apply": {"category": "Enforcement", "description": "Apply fines to users"},
    "access.revoke": {"category": "Enforcement", "description": "Revoke a user's access as enforcement"},
    "contract.terminate": {"category": "Enforcement", "description": "Terminate a contract as enforcement"},
    "resource.reassign": {"category": "Enforcement", "description": "Reassign resources between users"},
    "instrument.lock": {"category": "Enforcement", "description": "Lock financial instruments"},
    "notification.send": {"category": "Enforcement", "description": "Send enforcement notifications"},
    "resolution.query": {"category": "Enforcement", "description": "Query past resolutions"},
    "enforcement.escalate": {"category": "Enforcement", "description": "Escalate an enforcement action"},
    "enforcement.record": {"category": "Enforcement", "description": "Record enforcement actions"},

    "extension.call": {"category": "Extensions", "description": "Call extension functions (generic)"},
    "extension.sync_call": {"category": "Extensions", "description": "Make synchronous extension calls"},
    "extension.async_call": {"category": "Extensions", "description": "Make asynchronous extension calls"},
    "extension.install": {"category": "Extensions", "description": "Install new extensions into the realm"},
    "extension.uninstall": {"category": "Extensions", "description": "Uninstall extensions from the realm"},
    "demo.manage": {"category": "Extensions", "description": "Control demo and simulation features across extensions"},

    "codex.install": {"category": "Codex", "description": "Install governance codex packages"},
    "codex.uninstall": {"category": "Codex", "description": "Uninstall governance codex packages"},
    "codex.revert": {
        "category": "Codex",
        "description": "Revert the realm codex overlay to the previous package, or toggle safe mode",
    },

    "self.join": {"category": "Self-service", "description": "Join the realm as a new member"},
    "self.update_public_profile": {"category": "Self-service", "description": "Update your own public profile"},
    "self.update_private_data": {"category": "Self-service", "description": "Update your own private data"},
    "self.change_quarter": {"category": "Self-service", "description": "Move to a different quarter"},
    "self.invoice_refresh": {"category": "Self-service", "description": "Refresh your own invoices"},
    "self.data_view": {"category": "Self-service", "description": "View your own data, invoices, and notifications"},
    "self.data_manage": {"category": "Self-service", "description": "Manage your own records, payment accounts, and zones"},
}


class Profiles:
    ADMIN = {"name": "admin", "allowed_to": [Operations.ALL]}
    MEMBER = {
        "name": "member",
        "allowed_to": [
            Operations.SELF_JOIN,
            Operations.SELF_UPDATE_PUBLIC_PROFILE,
            Operations.SELF_UPDATE_PRIVATE_DATA,
            Operations.SELF_CHANGE_QUARTER,
            Operations.SELF_INVOICE_REFRESH,
            Operations.SELF_DATA_VIEW,
            Operations.SELF_DATA_MANAGE,
            Operations.REALM_DATA_VIEW,
            Operations.PROPOSAL_VOTE,
            Operations.DISPUTE_CREATE,
            Operations.DISPUTE_VIEW,
            Operations.EXTENSION_SYNC_CALL,
            Operations.EXTENSION_ASYNC_CALL,
            # Baseline capabilities that preserve the pre-cutover extension
            # "member" access level (extension entry_access is now gated on
            # operations, not profile names). The sensitive ones among these
            # carry additional in-code checks (department head/registrar/
            # policy gates) inside the extensions themselves. Realm admins
            # can revoke any of these from the profile per realm.
            Operations.PROPOSAL_CREATE,
            Operations.PROPOSAL_MANAGE,
            Operations.FEDERAL_VOTE_PROPOSE,
            Operations.ORG_MANAGE_MEMBERS,
            Operations.ORG_MANAGE_BUDGET,
            Operations.DOCUMENT_MANAGE,
            Operations.BID_SUBMIT,
            Operations.BID_EVALUATE,
            Operations.IDENTITY_SUBMIT,
            Operations.IDENTITY_REVIEW,
        ],
    }
    # Observers can look but not act. Before the permission-based
    # entry_access cutover any registered User passed "member"-level gates;
    # observers keep read visibility only.
    OBSERVER = {
        "name": "observer",
        "allowed_to": [
            Operations.REALM_DATA_VIEW,
            Operations.SELF_DATA_VIEW,
            Operations.EXTENSION_SYNC_CALL,
        ],
    }
    LEGISLATOR = {
        "name": "legislator",
        "allowed_to": [
            Operations.MANDATE_CREATE,
            Operations.PROPOSAL_CREATE,
            Operations.FEDERAL_VOTE_PROPOSE,
            Operations.FEDERAL_VOTE_MANAGE,
            Operations.CONTRACT_CREATE_UNDER_MANDATE,
            Operations.GOVERNANCE_UPDATE,
            Operations.CODEX_REVERT,
        ],
    }
    EXECUTOR = {
        "name": "executor",
        "allowed_to": [
            Operations.MANDATE_ASSIGN_EXECUTOR,
            Operations.TRADE_EXECUTE,
            Operations.RESOURCE_REASSIGN,
            Operations.ENFORCEMENT_RECORD,
        ],
    }
    JUDGE = {
        "name": "judge",
        "allowed_to": [
            Operations.DISPUTE_ACCEPT,
            Operations.DISPUTE_REJECT,
            Operations.DISPUTE_ASSIGN,
            Operations.DISPUTE_VIEW_ALL,
            Operations.EVIDENCE_EVALUATE,
            Operations.RESOLUTION_DRAFT,
            Operations.RESOLUTION_ISSUE,
            Operations.RESOLUTION_FINALIZE,
            Operations.APPEAL_ALLOW,
        ],
    }
    ENFORCER = {
        "name": "enforcer",
        "allowed_to": [
            Operations.FINE_APPLY,
            Operations.ACCESS_REVOKE,
            Operations.CONTRACT_TERMINATE,
            Operations.INSTRUMENT_LOCK,
            Operations.USER_UPDATE_STATUS,
            Operations.ENFORCEMENT_ESCALATE,
        ],
    }
    TREASURER = {
        "name": "treasurer",
        "allowed_to": [
            Operations.TRANSFER_CREATE,
            Operations.TRANSFER_REVERT,
            Operations.LICENSE_ISSUE,
            Operations.LICENSE_REVOKE,
            Operations.INVOICE_REFRESH,
            Operations.TREASURY_VIEW,
            Operations.TREASURY_MANAGE,
        ],
    }
    MERCHANT = {
        "name": "merchant",
        "allowed_to": [
            Operations.TRADE_EXECUTE,
            Operations.NFT_MINT,
        ],
    }
    OPERATOR = {
        "name": "operator",
        "allowed_to": [
            Operations.REALM_ADMIN,
            Operations.REALM_UPGRADE,
            Operations.REALM_CONFIGURE,
            Operations.REALM_CONFIGURE_BRANDING,
            Operations.REALM_CONFIGURE_CODEX,
            Operations.REALM_CONFIGURE_INFRASTRUCTURE,
            Operations.REALM_CONFIGURE_TOKENS,
            Operations.REALM_CONFIGURE_TRUST_POLICY,
            Operations.REALM_MONITOR,
            Operations.TASK_VIEW,
            Operations.QUARTER_REGISTER,
            Operations.QUARTER_DEREGISTER,
            Operations.QUARTER_CONFIGURE,
            Operations.QUARTER_SECEDE,
            Operations.QUARTER_JOIN_FEDERATION,
            Operations.REALM_REGISTER,
        ],
    }
    DEVELOPER = {
        "name": "developer",
        "allowed_to": [
            Operations.SHELL_EXECUTE,
            Operations.EXTENSION_CALL,
            Operations.EXTENSION_SYNC_CALL,
            Operations.EXTENSION_ASYNC_CALL,
            Operations.DB_EXPORT,
            Operations.DB_MANAGE,
            Operations.TASK_VIEW,
            Operations.REALM_MONITOR,
        ],
    }
    USER_MANAGER = {
        "name": "user_manager",
        "allowed_to": [
            Operations.ROLE_ASSIGN,
            Operations.ROLE_REVOKE,
            Operations.PERMISSION_GRANT,
            Operations.PERMISSION_REVOKE,
            Operations.PERMISSION_VIEW,
            Operations.USER_ADD,
            Operations.USER_UPDATE_STATUS,
            Operations.USER_VIEW,
            Operations.INVITE_MANAGE,
            Operations.EXTENSION_SYNC_CALL,
        ],
    }

    ALL_PROFILES = [
        ADMIN,
        MEMBER,
        OBSERVER,
        LEGISLATOR,
        EXECUTOR,
        JUDGE,
        ENFORCER,
        TREASURER,
        MERCHANT,
        OPERATOR,
        DEVELOPER,
        USER_MANAGER,
    ]


OPERATIONS_SEPARATOR = ","


class UserProfile(Entity, TimestampedMixin):

    __alias__ = "name"
    name = String(max_length=256)
    description = String(max_length=256)
    allowed_to = String()
    # The User→profiles relation is unidirectional (issue #242): use
    # ``self.reverse_count("users")`` for the holder count; list holders by
    # scanning users (core.membership.users_with_profile).
    permissions = ManyToMany(["Permission"], "profiles")
    extensions = ManyToMany(["Extension"], "profiles")

    def __repr__(self):
        return f"UserProfile(name={self.name!r})"

    def add(self, operation: str):
        self.allowed_to = str(self.allowed_to or "").split(OPERATIONS_SEPARATOR)
        if operation not in self.allowed_to:
            self.allowed_to.append(operation)
        self.allowed_to = OPERATIONS_SEPARATOR.join(self.allowed_to)

    def remove(self, operation: str):
        self.allowed_to = str(self.allowed_to or "").split(OPERATIONS_SEPARATOR)
        if operation in self.allowed_to:
            self.allowed_to.remove(operation)
        self.allowed_to = OPERATIONS_SEPARATOR.join(self.allowed_to)

    def is_allowed(self, operation: str) -> bool:
        return operation in str(self.allowed_to or "").split(OPERATIONS_SEPARATOR)
