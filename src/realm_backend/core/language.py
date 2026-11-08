"""BCP 47 language tags (``en``, ``es-ES``, ``zh-Hant``): the shape only. The realm carries no registry of
languages, so a well-formed tag it has no translations for is still a tag; the people who write in it decide."""
from core.db import Fail


def well_formed(tag):
    """A primary subtag of two or three letters, then subtags of one to eight letters or digits, all ASCII."""
    parts = tag.split("-") if isinstance(tag, str) else [""]
    first, rest = parts[0], parts[1:]
    return 2 <= len(first) <= 3 and first.isascii() and first.isalpha() \
        and all(1 <= len(p) <= 8 and p.isascii() and p.isalnum() for p in rest)


def require(tag):
    """Refuse a ``tag`` that is not a BCP 47 tag."""
    if not well_formed(tag):
        raise Fail(f"'{tag}' is not a BCP 47 language tag such as en or es-ES")


def require_all(tags):
    """Refuse ``tags`` unless it is a list of BCP 47 tags."""
    if not isinstance(tags, list):
        raise Fail("languages are a list of BCP 47 tags such as [en, es-ES]")
    for tag in tags:
        require(tag)
