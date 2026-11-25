"""Comunitat Valenciana: admissió de l'alumnat en centres públics i privats concertats. Decret 48/2024."""

from realms.legislation import above, fixed


class Education(above("education")):
    # When a school has more applications than places, these go first, in this order (Art. 21); points decide
    # within each of them and among the rest (Art. 22).
    preferences = fixed(["same_school", "attached_school", "foster_care", "residential_care",
                         "gender_violence_or_terrorism", "eviction_or_social_exclusion",
                         "music_dance_or_high_performance_sport", "own_school_bachillerato"],
                        legislation="Decret 48/2024 Art. 21")
    # A tie goes to the criteria in this order (Art. 38.1), and what is still tied after them to a public draw (Art. 38.2).
    tie_breaks = fixed(["siblings", "proximity", "income", "inclusion_income", "parent_works_at_school",
                        "large_family", "multiple_birth", "single_parent", "disability", "music_or_dance",
                        "elite_athlete", "specific_circumstance", "academic_record"],
                       legislation="Decret 48/2024 Art. 38")

    sibling = fixed(15, legislation="Decret 48/2024 Arts. 24.1 and 33")
    # An area of influence is the whole municipality unless the territorial directorate splits it (Art. 6.1): in a
    # town that is one area, as València is, every address in it is in the area of every school in it.
    proximity = fixed({"area": 10, "bordering": 5}, legislation="Decret 48/2024 Arts. 6.1 and 25.1")
    # The family's yearly income per member, two tax years before the application, against the IPREM of 14
    # payments: at most half of it, the IPREM, 1.5 times and twice. In halves of the IPREM: a chapter has no fractions.
    income = fixed([[1, 7], [2, 6], [3, 5], [4, 4]], legislation="Decret 48/2024 Art. 26.3")
    inclusion_income = fixed(7, legislation="Decret 48/2024 Art. 27.1")
    parent_works_at_school = fixed(7, legislation="Decret 48/2024 Art. 28")
    large_family = fixed({"general": 5, "special": 7}, legislation="Decret 48/2024 Art. 29")
    multiple_birth = fixed(1, legislation="Decret 48/2024 Art. 30.1")
    single_parent = fixed({"general": 5, "special": 7}, legislation="Decret 48/2024 Art. 31.1")
    # A degree of disability in percent, from the highest floor down.
    disability_pupil = fixed([[65, 7], [33, 4]], legislation="Decret 48/2024 Art. 32.1")
    disability_relative = fixed([[65, 5], [33, 3]], legislation="Decret 48/2024 Art. 32.2")
    music_or_dance = fixed(2, legislation="Decret 48/2024 Art. 34")
    elite_athlete = fixed(2, legislation="Decret 48/2024 Art. 35")
    specific_circumstance = fixed(1, legislation="Decret 48/2024 Art. 36.1")

    @fixed(legislation="Decret 48/2024 Arts. 22 and 24 to 36")
    def points(self, application):
        """The points of ``application`` for a place at one school: the total, and each criterion that scores.

        ``application`` holds what the family declares (Art. 19.2):
        ``siblings``, the brothers and sisters enrolled at the school, and those admitted to it in this process when
        the family applies for the first time in the Comunitat or after moving town (Arts. 24 and 33);
        ``home``, "area", "bordering" or None: where the family's home or a parent's workplace is (Art. 25);
        ``income`` and ``iprem``, the family's yearly income per member and the IPREM, in one unit;
        ``inclusion_income``, whether the family receives the renta valenciana d'inclusió, which then takes the place
        of the income criterion (Art. 26.1);
        ``parent_works_at_school``, ``specific_circumstance``, True or False;
        ``music_or_dance`` and ``elite_athlete``, True when the pupil does not get in by the preference of Art. 21.7;
        ``large_family`` and ``single_parent``, "general", "special" or None, which add up (Art. 31.2);
        ``same_birth``, the brothers and sisters born in the same birth (Art. 30);
        ``disability``, the pupil's degree in percent, 0 for none, and ``relatives_disability``, the degree of each
        parent, guardian, brother or sister who has one (Art. 32).
        Bachillerato also counts the average mark of ESO (Art. 37), which is not a number of points and is left out.
        """
        scores = [
            ["siblings", self.sibling * application["siblings"]],
            ["proximity", self.proximity.get(application["home"], 0)],
            ["income", 0 if application["inclusion_income"] else self._income(application["income"], application["iprem"])],
            ["inclusion_income", self.inclusion_income if application["inclusion_income"] else 0],
            ["parent_works_at_school", self.parent_works_at_school if application["parent_works_at_school"] else 0],
            ["large_family", self.large_family.get(application["large_family"], 0)],
            ["multiple_birth", self.multiple_birth * application["same_birth"]],
            ["single_parent", self.single_parent.get(application["single_parent"], 0)],
            ["disability", self._disability(application["disability"], application["relatives_disability"])],
            ["music_or_dance", self.music_or_dance if application["music_or_dance"] else 0],
            ["elite_athlete", self.elite_athlete if application["elite_athlete"] else 0],
            ["specific_circumstance", self.specific_circumstance if application["specific_circumstance"] else 0],
        ]
        total = 0
        for criterion in scores:
            total = total + criterion[1]
        return {"total": total, "by_criterion": {criterion[0]: criterion[1] for criterion in scores if criterion[1]}}

    def _income(self, income, iprem):
        for bracket in self.income:
            if 2 * income <= bracket[0] * iprem:
                return bracket[1]
        return 0

    def _disability(self, pupil, relatives):
        score = self._degree(self.disability_pupil, pupil)
        for degree in relatives:
            score = score + self._degree(self.disability_relative, degree)
        return score

    def _degree(self, table, degree):
        for floor in table:
            if degree >= floor[0]:
                return floor[1]
        return 0
