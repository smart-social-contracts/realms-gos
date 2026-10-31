from _cdk import Opt, Principal, Record, Variant, Vec, nat, text


class PaginationInfo(Record):
    page_num: int
    page_size: int
    total_items_count: int
    total_pages: int


class CanisterInfo(Record):
    canister_id: text
    canister_type: text


class QuarterInfoRecord(Record):
    name: text
    canister_id: text
    population: nat
    status: text
    index: nat


class StatusRecord(Record):
    version: text
    status: text
    users_count: nat
    organizations_count: nat
    realms_count: nat
    mandates_count: nat
    tasks_count: nat
    transfers_count: nat
    instruments_count: nat
    codexes_count: nat
    disputes_count: nat
    licenses_count: nat
    trades_count: nat
    proposals_count: nat
    votes_count: nat
    commit: text
    commit_datetime: text
    extensions: Vec[text]
    test_mode: bool
    test_mode_ii_bypass: bool
    test_mode_user_self_registration: bool
    test_mode_demo_data: bool
    test_mode_skip_terms: bool
    test_mode_skip_passport_zkproof: bool
    realm_name: text
    realm_manifesto: text
    realm_welcome_message: text
    user_profiles_count: nat
    canisters: Vec[CanisterInfo]
    registries: Vec[CanisterInfo]
    dependencies: Vec[text]
    python_version: text
    quarters: Vec[QuarterInfoRecord]
    is_quarter: bool
    parent_realm_canister_id: text
    accounting_currency: text
    accounting_currency_decimals: nat
    logo_url: text
    background_image_url: text
    file_registry_canister_id: text
    marketplace_canister_id: text
    gos_environment: text


class UserGetRecord(Record):
    principal: Principal
    profiles: Vec[text]
    departments: Vec[text]
    nickname: text
    avatar: text
    private_data: text
    assigned_quarter: text


# New GGG response records
class ObjectsListRecordPaginated(Record):
    objects: Vec[text]  # JSON string of object data
    pagination: PaginationInfo


class ObjectsListRecord(Record):
    objects: Vec[text]  # JSON string of object data


class ExtensionsListRecord(Record):
    extensions: Vec[text]


class RealmResponseData(Variant):
    status: StatusRecord
    userGet: UserGetRecord
    error: text
    message: text
    objectsList: ObjectsListRecord
    objectsListPaginated: ObjectsListRecordPaginated
    extensionsList: ExtensionsListRecord


class RealmResponse(Record):
    success: bool
    data: RealmResponseData


# Crypto envelope & group types


class EnvelopeRecord(Record):
    scope: text
    principal_id: text
    wrapped_dek: text


class EnvelopeListRecord(Record):
    envelopes: Vec["EnvelopeRecord"]


class ScopeListRecord(Record):
    scopes: Vec[text]


class GroupRecord(Record):
    name: text
    description: text


class GroupListRecord(Record):
    groups: Vec["GroupRecord"]


class GroupMemberRecord(Record):
    principal_id: text
    role: text


class GroupMembersRecord(Record):
    members: Vec["GroupMemberRecord"]


class CryptoResponseData(Variant):
    envelope: EnvelopeRecord
    envelopeList: EnvelopeListRecord
    scopeList: ScopeListRecord
    group: GroupRecord
    groupList: GroupListRecord
    groupMembers: GroupMembersRecord
    error: text
    message: text


class CryptoResponse(Record):
    success: bool
    data: CryptoResponseData


# Extension call types
class ExtensionCallArgs(Record):
    extension_name: text
    function_name: text
    args: text


class ExtensionCallResponse(Record):
    success: bool
    response: text
