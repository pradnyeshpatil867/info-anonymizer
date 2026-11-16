"""Fake names (pseudonyms) made with the Faker library (spec section D)."""

from dataclasses import dataclass
from typing import Callable

from faker import Faker

from app.checks.normalize import normalize_name


@dataclass(frozen=True)
class Pseudonym:
    first_name: str
    middle_name: str | None
    last_name: str
    birth_name: str | None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)


class PseudonymGenerator:
    def __init__(self, locale: str = "en_US", include_middle_name: bool = True,
                 match_gender: bool = True, seed: int | None = None):
        self.fake = Faker(locale)
        if seed is not None:
            self.fake.seed_instance(seed)  # same seed -> same names (used in tests)
        self.include_middle_name = include_middle_name
        self.match_gender = match_gender

    def _first(self, gender: str | None) -> str:
        if self.match_gender and gender == "F":
            return self.fake.first_name_female()
        if self.match_gender and gender == "M":
            return self.fake.first_name_male()
        return self.fake.first_name()

    def generate(self, gender: str | None, has_middle_name: bool, has_birth_name: bool,
                 is_taken: Callable[[str, str], bool], max_tries: int = 200) -> Pseudonym:
        """Make a fake name that is not taken.

        `is_taken(first, last)` must return True if this first + last name is
        already used as a real name or as another pseudonym.
        """
        for _ in range(max_tries):
            first = self._first(gender)
            last = self.fake.last_name()
            if is_taken(first, last):
                continue
            middle = self._first(gender) if (has_middle_name and self.include_middle_name) else None
            birth = None
            if has_birth_name:
                birth = self.fake.last_name()
                while birth == last:
                    birth = self.fake.last_name()
            return Pseudonym(first, middle, last, birth)
        raise RuntimeError("Could not find a free fake name. Try another locale.")


def taken_checker(names: list[tuple[str, str, str]]) -> Callable[[str, str], bool]:
    """Build an `is_taken` function from the vault's list of real names and pseudonyms."""
    used = {normalize_name(f"{first} {last}") for _, first, last in names}
    return lambda first, last: normalize_name(f"{first} {last}") in used
