from ic_python_db import Boolean, Integer, String

from db import Row


class NFTToken(Row):
    __alias__ = "id"
    id = String()  # symbol
    canister = String(default="")
    name = String(default="")
    description = String(default="")
    transferable = Boolean(default=True)
    required_license = String(default="")
    supply_cap = Integer(default=0)
    total_supply = Integer(default=0)
