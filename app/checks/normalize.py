"""Normalization: write names and dates in one standard form.

We use this to compare values and to build the `match_key` that finds
the same person again (spec section E). Normalized values are only used
for comparing. They are never shown or stored as the person's real name.

Example:
    match_key("Jonas", "Müller", "12.04.1990")  ->  "jonas mueller|1990-04-12"
"""

import re
import unicodedata
from datetime import date

# German special letters. We map them the same way the MRZ does (Ü -> UE),
# so "Müller" and "Mueller" become the same text.
_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}

# Academic titles are not part of the name. "Dr. Anna Schmidt" = "Anna Schmidt".
# "Dr", "Prof" and "Dipl" are always removed. Words like "med." or "phil." are
# removed only WITH a dot, so the given name "Phil" stays: "Dr. phil. Anna" -> "anna",
# but "Phil Weber" -> "phil weber".
_TITLE_RE = re.compile(
    r"\b(?:prof|dr|dipl)\b\.?(?:-?\s*(?:med|phil|rer|nat|habil|jur|ing|kfm|oec)\.)*",
    re.IGNORECASE,
)


def normalize_name(text: str) -> str:
    """Return a name in lowercase, without titles, umlauts, accents or punctuation.

    >>> normalize_name("Dr. Hans-Jürgen MÜLLER")
    'hans juergen mueller'
    """
    s = text.lower()
    s = _TITLE_RE.sub(" ", s)
    for umlaut, plain in _UMLAUTS.items():
        s = s.replace(umlaut, plain)
    # Remove other accents: é -> e, ç -> c. NFKD splits "é" into "e" + accent mark.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # Hyphens, commas, dots and other signs become spaces.
    s = re.sub(r"[^a-z]+", " ", s)
    return " ".join(s.split())


def split_name(raw: str) -> tuple[str, str]:
    """Split a full name into (given_names, surname).

    Two common ways of printing a name are handled:
    - "SURNAME, Given Names"  (comma: surname comes first)
    - "Given Names Surname"   (no comma: last word is the surname)

    >>> split_name("MÜLLER, Jonas Peter")
    ('Jonas Peter', 'MÜLLER')
    >>> split_name("Jonas Müller")
    ('Jonas', 'Müller')
    """
    raw = raw.strip()
    if "," in raw:
        surname, given = raw.split(",", 1)
        return given.strip(), surname.strip()
    parts = raw.split()
    if len(parts) < 2:
        return "", raw
    return " ".join(parts[:-1]), parts[-1]


# Date formats we accept from documents and forms.
_DATE_PATTERNS = [
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), "dmy"),  # 12.04.1990 (German)
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "dmy"),    # 12/04/1990
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), "dmy"),    # 12-04-1990
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),    # 1990-04-12 (ISO)
]


def normalize_date(text: str) -> str:
    """Return a date as ISO text "YYYY-MM-DD".

    Raises ValueError if the text is not a real date, for example "31.02.1990".

    >>> normalize_date("12.04.1990")
    '1990-04-12'
    """
    s = text.strip()
    # Newer German cards print dates with spaces: "14 03 1988". Turn them into "14.03.1988".
    if re.fullmatch(r"\d{1,2}\s+\d{1,2}\s+\d{4}", s):
        s = re.sub(r"\s+", ".", s)
    s = s.replace(" ", "")
    for pattern, order in _DATE_PATTERNS:
        m = pattern.match(s)
        if m:
            a, b, c = (int(x) for x in m.groups())
            year, month, day = (c, b, a) if order == "dmy" else (a, b, c)
            # date() raises ValueError for impossible dates such as 31 February.
            return date(year, month, day).isoformat()
    raise ValueError(f"Unknown date format: {text!r}")


def match_key(given_names: str, surname: str, date_of_birth: str) -> str:
    """Build the key that identifies a person: normalized name + ISO birth date.

    >>> match_key("Jonas", "MÜLLER", "12.04.1990")
    'jonas mueller|1990-04-12'
    """
    name = normalize_name(f"{given_names} {surname}")
    if not name:
        raise ValueError("Name is empty")
    return f"{name}|{normalize_date(date_of_birth)}"
