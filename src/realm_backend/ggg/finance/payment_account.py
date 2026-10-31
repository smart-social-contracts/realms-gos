from ic_python_db import Boolean, String

from core.db import Row


class PaymentAccount(Row):
    __alias__ = "id"
    id = String()
    owner = String(indexed=True)
    network = String(default="")
    address = String(default="")
    symbol = String(default="")
    label = String(default="")
    verified = Boolean(default=False)
