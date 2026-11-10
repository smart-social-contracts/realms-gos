from ic_python_db import Integer, String

from core.db import Json, Row


class Codex(Row):
    __alias__ = "id"
    __version__ = 2  # 2: voting_rule
    id = String()
    version = String(default="")
    checksum = String(default="")
    status = String(default="active", indexed=True)  # pending | active | inactive | reverted
    manifest = Json({})
    capabilities = Json([])
    source = String(default="")
    installed_at = Integer(default=0)
    previous = String(default="")
    # The realm's rule over the votes cast and its voting window as they were when this codex became active: what decides
    # a vote to revert to it (docs/codices/layered-codex.md §10.1). {} for a codex that became active before the realm
    # recorded them.
    voting_rule = Json({})

    @classmethod
    def migrate(cls, obj, from_version, to_version):
        obj.setdefault("voting_rule", {})  # a Json field a stored row lacks would read as the text of its default
        return obj
