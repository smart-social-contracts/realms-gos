from ic_python_db import ManyToMany, String

from db import Row


class User(Row):
    __alias__ = "id"
    id = String()  # principal
    nickname = String(default="")
    avatar = String(default="")
    home_quarter = String(default="")
    private_data = String(default="")  # vetKeys ciphertext
    profiles = ManyToMany(["UserProfile"], "users")
    permissions = ManyToMany(["Permission"], "users")
