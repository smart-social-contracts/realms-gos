from ic_python_db import ManyToMany, String

from core.db import Row


class User(Row):
    __alias__ = "id"
    id = String()  # principal
    nickname = String(default="")
    avatar = String(default="")
    home_quarter = String(default="")
    language = String(default="")  # a BCP 47 tag, for notices to the user
    home_zone = String(default="")  # the id of a Zone the realm defined (api.assets.zone_define)
    private_data = String(default="")  # vetKeys ciphertext
    profiles = ManyToMany(["UserProfile"], "users")
    permissions = ManyToMany(["Permission"], "users")
