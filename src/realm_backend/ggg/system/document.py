from ic_python_db import Integer, String

from core.db import Json, Row


class Document(Row):
    __alias__ = "id"
    id = String()
    department = String(indexed=True)
    title = String(default="")
    kind = String(default="")  # what the document is: minutes, an ordinance, an award
    languages = Json([])  # BCP 47 tags
    ref = String(default="")  # where the ciphertext lives (file registry path)
    hash = String(default="")
    author = String(default="")
    at = Integer(default=0)
    facts = Json({})  # what legislation reads about the document when it is shared
    public = Json(None)  # {"ref", "hash"} of the content in clear, once published; it holds no personal data
    publications = Json([])  # where journals published it: {"journal", "dispatch", "published_at", "ref"}
