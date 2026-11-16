"""Fake values: an invented value of the same kind replaces a real value (spec section C).

Example: the real address "Hauptstr. 1, 10115 Berlin" becomes
"742 Evergreen Lane, Springfield, IL 62704" in the JSON file.

Rules:
- Same person + same field + same real value -> always the same fake value.
- A fake value is never equal to the real value.
- The vault keeps the link fake value <-> real value (table identifiers).
Most values come from the Faker library (the same library as the fake names).
Document numbers, the tax ID, height and eye colour use small generators below.
"""

from datetime import date, timedelta

from faker import Faker

from app.checks.validate import steuer_id_check_digit
from app.identity.fields import MATCH_FIELDS, same_value
from app.identity.vault import Vault

# Letters used in German document numbers (no vowels, so no words can appear).
DOC_LETTERS = "CFGHJKLMNPRTVWXYZ"
EYE_COLOURS = ["BLUE", "BROWN", "GREEN", "GREY", "HAZEL"]
COUNTRY_CODES = ["AUT", "BEL", "CAN", "DNK", "ESP", "FIN", "FRA", "GBR", "IRL", "ITA", "NLD", "NOR",
                 "POL", "PRT", "SWE", "USA"]
PERMIT_TYPES = ["AUFENTHALTSERLAUBNIS", "NIEDERLASSUNGSERLAUBNIS", "BLAUE KARTE EU", "ICT-KARTE"]


def _same_format(fake: Faker, real: str) -> str:
    """Same length and pattern: a letter becomes a letter, a digit a digit. "T22000129" -> "L8K40P217"."""
    out = []
    for ch in real:
        if ch.isdigit():
            out.append(fake.random.choice("0123456789"))
        elif ch.isalpha():
            out.append(fake.random.choice(DOC_LETTERS))
        else:
            out.append(ch)
    return "".join(out) or fake.bothify("?#######", letters=DOC_LETTERS)


def _steuer_id(fake: Faker, _real: str) -> str:
    """11 digits that pass the official checks: in the first 10 digits exactly one digit
    appears twice, the first digit is not 0, and the last digit is the check digit."""
    while True:
        digits = fake.random.sample("0123456789", 9)      # 9 different digits
        digits.append(fake.random.choice(digits))        # one of them a second time
        fake.random.shuffle(digits)
        if digits[0] != "0":
            first_ten = "".join(digits)
            return first_ten + str(steuer_id_check_digit(first_ten))


def _date_near(fake: Faker, real: str, years: int) -> str:
    """A date within +/- `years` of the real date (keeps it plausible, for example expiry in the future)."""
    try:
        base = date.fromisoformat(real)
    except ValueError:
        base = date.today()
    return (base + timedelta(days=fake.random.randint(-365 * years, 365 * years))).isoformat()


GENERATORS = {
    "date_of_birth": lambda f, r: f.date_of_birth(minimum_age=18, maximum_age=90).isoformat(),
    "issue_date": lambda f, r: _date_near(f, r, 2),
    "expiry_date": lambda f, r: _date_near(f, r, 2),
    "place_of_birth": lambda f, r: f.city(),
    "address": lambda f, r: f.address().replace("\n", ", "),
    "phone": lambda f, r: f.phone_number(),
    "email": lambda f, r: f.email(),
    "iban": lambda f, r: f.iban(),
    "steuer_id": _steuer_id,
    "employer": lambda f, r: f.company(),
    "issuing_authority": lambda f, r: f"City of {f.city()}",
    "height": lambda f, r: f"{f.random.randint(150, 200)} cm",
    "eye_colour": lambda f, r: f.random.choice(EYE_COLOURS),
    "gender": lambda f, r: f.random.choice(["F", "M", "X"]),
    "nationality": lambda f, r: f.random.choice(COUNTRY_CODES),
    "issuing_country": lambda f, r: f.random.choice(COUNTRY_CODES),
    "permit_type": lambda f, r: f.random.choice(PERMIT_TYPES),
    "remarks": lambda f, r: f.sentence(nb_words=4).rstrip(".").upper(),
    **{n: _same_format for n in MATCH_FIELDS},
}


def fake_for(vault: Vault, fake: Faker, person_id: str, field: str, real: str, max_tries: int = 200) -> str:
    """Return the fake value for this person's real value. Create it if it does not exist yet.

    `real` must already be in its stored form (see fields.normalize_value).
    Call this inside `with vault.transaction():`.
    """
    existing = vault.find_fake(person_id, field, real)
    if existing is not None:
        return existing
    # The same value in another spelling ("Chennai" / "CHENNAI", "KÖLN" / "KOELN") gets the same fake.
    for saved_real, saved_fake in vault.pairs_for_field(person_id, field):
        if same_value(saved_real, real):
            return saved_fake
    make = GENERATORS.get(field)
    if make is None:
        raise ValueError(f"No fake value generator for field {field!r}")
    for _ in range(max_tries):
        value = make(fake, real)
        # Not the real value, and not already used for another real value of this person.
        if not same_value(value, real) and vault.real_from_fake(person_id, field, value) is None:
            vault.add_identifier(person_id, field, real, value, used_for_matching=field in MATCH_FIELDS)
            return value
    raise RuntimeError(f"Could not make a new fake value for {field!r}")
