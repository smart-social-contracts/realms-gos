"""Comunitat Valenciana: llengües oficials. Estatut d'Autonomia."""

from realms.legislation import above, extension


class Language(above("language")):
    co_official = extension(["ca-valencia"], legislation="Estatut d'Autonomia Art. 6")
