"""Estado: lenguas oficiales. Constitución Española."""

from realms.legislation import above, extension


class Language(above("language")):
    co_official = extension(["es"], legislation="Constitución Art. 3")
