"""Compare readings: two OCR engines, printed text vs. MRZ, and the umlaut rule.

Background: OCR often drops the dots of Ä, Ö, Ü ("MÜLLER" -> "MULLER"),
but it rarely adds dots that are not there. The MRZ writes Ü as "UE",
so it tells us where the dots belong.
"""

import difflib
import itertools
import unicodedata

from app.checks.result import CheckResult, Status

_TO_MRZ = {"Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS", "ẞ": "SS"}
_DOTS = {"Ä": "A", "Ö": "O", "Ü": "U"}


def similarity(a: str, b: str) -> float:
    """Score from 0.0 (different) to 1.0 (identical), using Python's difflib."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def to_mrz_style(printed: str) -> str:
    """Write a printed name the way the MRZ writes it.

    >>> to_mrz_style("Müller-Lüdenscheidt")
    'MUELLER LUEDENSCHEIDT'
    """
    s = printed.upper()
    for ch, rep in _TO_MRZ.items():
        s = s.replace(ch, rep)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = "".join(ch if "A" <= ch <= "Z" else " " for ch in s)
    return " ".join(s.split())


def strip_dots(text: str) -> str:
    """'MÜLLER' -> 'MULLER' (what OCR produces when it misses the dots)."""
    return "".join(_DOTS.get(ch, ch) for ch in text.upper())


def umlaut_candidates(mrz_name: str) -> list[str]:
    """All printed spellings that the MRZ name could come from.

    'MUELLER' -> ['MUELLER', 'MÜLLER'], because UE can be Ü or really UE.
    """
    pieces: list[list[str]] = []
    i = 0
    pairs = {"AE": "Ä", "OE": "Ö", "UE": "Ü"}
    while i < len(mrz_name):
        two = mrz_name[i:i + 2]
        if two in pairs:
            pieces.append([two, pairs[two]])
            i += 2
        else:
            pieces.append([mrz_name[i]])
            i += 1
    return ["".join(p) for p in itertools.product(*pieces)]


def check_name_against_mrz(printed: str, mrz_name: str, field: str = "surname") -> tuple[CheckResult, str | None]:
    """Compare a printed (OCR) name with the MRZ name.

    Returns (result, best_value):
    - OK:   the printed name matches the MRZ. best_value = printed name.
    - WARN: dots were missing; best_value = repaired name, user must confirm.
    - FAIL: no match even with the umlaut rule. best_value = None.
    """
    mrz_name = " ".join(mrz_name.upper().split())
    if to_mrz_style(printed) == mrz_name:
        return CheckResult(f"{field}_vs_mrz", Status.OK,
                           f"{printed} matches the MRZ ({mrz_name})."), printed.upper()

    printed_plain = " ".join(strip_dots(printed).split())
    for candidate in umlaut_candidates(mrz_name):
        if candidate == mrz_name:
            continue  # the "no umlaut" spelling was already tested above
        if strip_dots(candidate) == printed_plain:
            return CheckResult(
                f"{field}_vs_mrz", Status.WARN,
                f"OCR read {printed}, MRZ says {mrz_name}. UE/OE/AE can mean Ü/Ö/Ä, "
                f"so the name is probably {candidate}. Please confirm.",
            ), candidate

    return CheckResult(f"{field}_vs_mrz", Status.FAIL,
                       f"{printed} does not match the MRZ ({mrz_name})."), None


def pick_reading(a: str, b: str, field: str = "value") -> tuple[CheckResult, str | None]:
    """Choose between two OCR readings of the same field.

    - Same text -> OK.
    - Same except one has umlaut dots -> WARN, keep the one with dots.
    - Otherwise -> FAIL, the user must decide.
    """
    if a == b:
        return CheckResult(f"{field}_engines", Status.OK, f"Both engines read {a}."), a
    if strip_dots(a) == strip_dots(b):
        with_dots = a if a.upper() != strip_dots(a) else b
        return CheckResult(
            f"{field}_engines", Status.WARN,
            f"Engines disagree ({a} / {b}) only in umlaut dots. Keeping {with_dots}.",
        ), with_dots
    return CheckResult(f"{field}_engines", Status.FAIL,
                       f"Engines disagree: {a} / {b}. Please check the image."), None
