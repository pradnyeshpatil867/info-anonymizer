"""Which fields exist, which are anonymized, and the one stored form of each value.

Spec section C (user decisions 2026-11-16):
- Group A: always anonymized (they identify the person).
- Group B: clear text, unless the user ticks "Anonymize" in the review screen.
- An anonymized value is replaced by a FAKE value of the same kind (see fakes.py),
  for example a fake address instead of the real address.
"""

import re

from app.checks.normalize import normalize_date

# Umlauts can be written two ways on German documents: "KÖLN" or "KOELN".
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Every field the app can save (names are handled separately: they get the pseudonym).
KNOWN_FIELDS = {
    "date_of_birth", "place_of_birth", "address", "id_card_number", "passport_number",
    "residence_permit_number", "driving_licence_number", "issue_date", "expiry_date",
    "issuing_authority", "phone", "email", "iban", "steuer_id", "employer", "height",
    "eye_colour", "permit_type", "remarks", "gender", "nationality", "doc_type", "issuing_country",
}

# Document numbers identify exactly one person ("used for matching").
MATCH_FIELDS = {"id_card_number", "passport_number", "residence_permit_number", "driving_licence_number"}

# Dates are stored in ISO form.
DATE_FIELDS = {"date_of_birth", "issue_date", "expiry_date"}

# Group A: ALWAYS anonymized. Names are replaced by the pseudonym, the rest by fake values.
ALWAYS_ANONYMIZED = ({"surname", "given_names", "birth_name", "date_of_birth"} | MATCH_FIELDS
                     | {"address", "phone", "email", "iban", "steuer_id"})

# Group B: clear text unless the user ticks "Anonymize" (or config tokenize_low_risk_fields is true).
# The document type always stays in clear text: without it the JSON file cannot be read.
OPTIONAL_ANONYMIZED = KNOWN_FIELDS - ALWAYS_ANONYMIZED - {"doc_type"}


def risk_level(field: str) -> str:
    return "high" if field in ALWAYS_ANONYMIZED else "low"


def normalize_value(field: str, value: str) -> str:
    """Store values in one form, so the same value is always recognized.

    Document numbers, IBANs and the tax ID: uppercase, no spaces ("de89 3704" -> "DE893704").
    Dates: ISO form ("25 02 2020" and "25.02.2020" -> "2020-02-25").
    Other fields: only outer spaces removed.
    """
    v = value.strip()
    if field in DATE_FIELDS:
        try:
            return normalize_date(v)
        except ValueError:
            return v  # not a full date: keep it as it is (the API refuses such dates before saving)
    if field in MATCH_FIELDS or field in {"iban", "steuer_id"}:
        v = "".join(v.split()).upper()
    return v


def compare_key(value: str) -> str:
    """The form used to COMPARE two values (never stored).

    Ignores letter case, all spaces, and the two ways of writing umlauts (decision 2026-11-16):
    "CHENNAI" = "Chennai", "175cm" = "175 cm", "KÖLN" = "KOELN" = "Koeln", "Straße" = "STRASSE".
    Not covered: abbreviations ("Hauptstr." vs "Hauptstraße") and word vs code ("DEUTSCH" vs "DEU").
    """
    return re.sub(r"\s+", "", str(value).lower().translate(_UMLAUTS))


def same_value(a, b) -> bool:
    return a is not None and b is not None and compare_key(a) == compare_key(b)
