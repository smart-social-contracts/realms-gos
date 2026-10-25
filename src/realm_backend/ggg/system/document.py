from ic_python_db import Integer, String

from db import Row


class Document(Row):
    __alias__ = "id"
    id = String()
    department = String(indexed=True)
    title = String(default="")
    ref = String(default="")  # where the ciphertext lives (file registry path)
    hash = String(default="")
    author = String(default="")
    at = Integer(default=0)
