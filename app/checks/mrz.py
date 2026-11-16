"""MRZ reading and check digits (ICAO Doc 9303).

The MRZ (Machine Readable Zone) is the block of text with "<" signs at the
bottom of ID cards, passports and residence permits. It contains check
digits, so we can prove that a reading is correct.

Two layouts are supported:
- TD1: 3 lines of 30 characters (German ID card, residence permit card)
- TD3: 2 lines of 44 characters (passport)
"""

import re
from dataclasses import dataclass, field
from datetime import date

from app.checks.result import CheckResult, Status

_WEIGHTS = (7, 3, 1)
# Germany uses "D" (padded "D<<"), not the usual three letters.
# The official passport specimen (Bundesdruckerei) uses "BDR" as issuing state;
# we found no official source for it, but accept it (user decision, milestone 3).
GERMANY_CODES = frozenset({"D", "BDR"})


def char_value(ch: str) -> int:
    """Value of one MRZ character: 0-9 -> 0-9, A-Z -> 10-35, '<' -> 0."""
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    if ch == "<":
        return 0
    raise ValueError(f"Not an MRZ character: {ch!r}")


def check_digit(text: str) -> int:
    """Compute the ICAO check digit: weights 7, 3, 1 repeat; last digit of the sum.

    >>> check_digit("740812")
    2
    """
    return sum(char_value(ch) * _WEIGHTS[i % 3] for i, ch in enumerate(text)) % 10


def _digit_ok(data: str, digit: str) -> bool:
    """True if `digit` is the correct check digit for `data`."""
    if not digit.isdigit():
        # A filler "<" as check digit counts as 0 (ICAO rule for empty optional fields).
        return digit == "<" and check_digit(data) == 0
    return check_digit(data) == int(digit)


def mrz_date_to_iso(yymmdd: str, kind: str) -> str:
    """Turn an MRZ date (YYMMDD) into "YYYY-MM-DD".

    The MRZ has only two year digits, so we guess the century:
    - birth date: if the year would be in the future, it is 19xx
    - expiry date: always 20xx
    """
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if kind == "birth":
        this_year = date.today().year % 100
        year = 1900 + yy if yy > this_year else 2000 + yy
    else:
        year = 2000 + yy
    return date(year, mm, dd).isoformat()


def _name_part(text: str) -> str:
    """'ANNA<MARIA<<<<' -> 'ANNA MARIA'."""
    return " ".join(text.replace("<", " ").split())


@dataclass
class MrzResult:
    layout: str                 # "TD1" or "TD3"
    document_code: str          # "ID", "P", "AR", ...
    issuing_state: str          # "D" for Germany
    document_number: str
    nationality: str
    birth_date: str             # YYMMDD as printed in the MRZ
    sex: str                    # "M", "F", or "<" (not stated)
    expiry_date: str            # YYMMDD
    surname: str                # MRZ style: "MUELLER"
    given_names: str            # MRZ style: "JONAS PETER"
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def issued_in_germany(self) -> bool:
        return self.issuing_state in GERMANY_CODES


def _check(name: str, label: str, data: str, digit: str) -> CheckResult:
    if _digit_ok(data, digit):
        return CheckResult(name, Status.OK, f"{label}: check digit {digit} is correct.")
    return CheckResult(
        name, Status.FAIL,
        f"{label}: check digit {digit} is wrong (calculated {check_digit(data)}). "
        "A character was probably misread.",
    )


def clean_lines(text: str) -> list[str]:
    """Split OCR text into MRZ lines: uppercase, no spaces, empty lines removed."""
    lines = []
    for line in text.upper().splitlines():
        line = line.replace(" ", "").strip()
        if line:
            lines.append(line)
    return lines


def parse_td1(lines: list[str]) -> MrzResult:
    """Parse a 3 x 30 MRZ (ID card, residence permit)."""
    if len(lines) != 3 or any(len(x) != 30 for x in lines):
        raise ValueError("TD1 needs 3 lines of 30 characters")
    l1, l2, l3 = lines
    surname, _, given = l3.partition("<<")
    r = MrzResult(
        layout="TD1",
        document_code=l1[0:2].replace("<", ""),
        issuing_state=l1[2:5].replace("<", ""),
        document_number=l1[5:14].replace("<", ""),
        nationality=l2[15:18].replace("<", ""),
        birth_date=l2[0:6],
        sex=l2[7],
        expiry_date=l2[8:14],
        surname=_name_part(surname),
        given_names=_name_part(given),
    )
    r.checks = [
        _check("mrz_document_number", "Document number", l1[5:14], l1[14]),
        _check("mrz_birth_date", "Birth date", l2[0:6], l2[6]),
        _check("mrz_expiry_date", "Expiry date", l2[8:14], l2[14]),
        # Composite: covers line 1 positions 6-30 and parts of line 2.
        _check("mrz_composite", "Composite", l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29], l2[29]),
    ]
    return r


def parse_td3(lines: list[str]) -> MrzResult:
    """Parse a 2 x 44 MRZ (passport)."""
    if len(lines) != 2 or any(len(x) != 44 for x in lines):
        raise ValueError("TD3 needs 2 lines of 44 characters")
    l1, l2 = lines
    surname, _, given = l1[5:44].partition("<<")
    r = MrzResult(
        layout="TD3",
        document_code=l1[0:2].replace("<", ""),
        issuing_state=l1[2:5].replace("<", ""),
        document_number=l2[0:9].replace("<", ""),
        nationality=l2[10:13].replace("<", ""),
        birth_date=l2[13:19],
        sex=l2[20],
        expiry_date=l2[21:27],
        surname=_name_part(surname),
        given_names=_name_part(given),
    )
    r.checks = [
        _check("mrz_document_number", "Document number", l2[0:9], l2[9]),
        _check("mrz_birth_date", "Birth date", l2[13:19], l2[19]),
        _check("mrz_expiry_date", "Expiry date", l2[21:27], l2[27]),
        _check("mrz_personal_number", "Personal number", l2[28:42], l2[42]),
        _check("mrz_composite", "Composite", l2[0:10] + l2[13:20] + l2[21:43], l2[43]),
    ]
    return r


_MRZ_LIKE = re.compile(r"^[A-Z0-9<]{25,50}$")


def extract_mrz_lines(text: str) -> list[str]:
    """Pick the lines that look like MRZ lines from any OCR text.

    An MRZ line has only A-Z, 0-9 and "<", is 25-50 characters long and contains "<".
    """
    out = []
    for line in text.upper().splitlines():
        line = line.replace(" ", "").strip()
        if _MRZ_LIKE.match(line) and "<" in line:
            out.append(line)
    return out


def fit_filler(lines: list[str]) -> list[str]:
    """Repair the most common OCR error in the MRZ: too many or too few "<" at the end.

    Long runs of "<" are hard to count, so OCR often reads 28 or 31 instead of 30.
    We only add or remove "<" at the END of a line, never other characters.
    The check digits then decide if the repaired MRZ is correct.
    """
    target = {3: 30, 2: 44}.get(len(lines))
    if target is None:
        return lines
    fixed = []
    for line in lines:
        if len(line) < target and line.endswith("<"):
            line = line + "<" * (target - len(line))
        elif len(line) > target and line[target:].strip("<") == "":
            line = line[:target]
        elif len(line) > target and line.endswith("<"):
            # Extra "<" inside the trailing filler run: shorten that run.
            body = line.rstrip("<")
            line = body + "<" * max(target - len(body), 0)
        fixed.append(line)
    return fixed


def parse_mrz(text: str) -> MrzResult:
    """Detect the layout from the line count and length, then parse."""
    lines = fit_filler(clean_lines(text))
    if len(lines) == 3 and all(len(x) == 30 for x in lines):
        return parse_td1(lines)
    if len(lines) == 2 and all(len(x) == 44 for x in lines):
        return parse_td3(lines)
    raise ValueError(f"Unknown MRZ layout: {len(lines)} lines, lengths {[len(x) for x in lines]}")


def _try_parse(text: str | None) -> MrzResult | None:
    if not text:
        return None
    try:
        return parse_mrz(text)
    except ValueError:
        return None


def choose_mrz(sources: dict[str, str | None]) -> tuple[MrzResult | None, CheckResult]:
    """Pick the best MRZ reading from several OCR engines.

    `sources` maps an engine name to its MRZ text (or None if it found none).
    Rules (user decision, milestone 3):
    - Only readings whose check digits all pass can win.
    - If two readings pass but their names differ, the result is yellow (WARN),
      because the name is NOT covered by the check digits on ID cards.
    - The first source in `sources` wins a tie (put the main engine first).
    """
    parsed = {name: _try_parse(text) for name, text in sources.items()}
    valid = {name: r for name, r in parsed.items() if r is not None and r.valid}

    if not valid:
        found = [name for name, r in parsed.items() if r is not None]
        if not found:
            return None, CheckResult("mrz", Status.FAIL, "No MRZ could be read.")
        best = parsed[found[0]]
        return best, CheckResult("mrz", Status.FAIL,
                                 "An MRZ was read, but its check digits fail. Please check the MRZ in the image.")

    names = list(valid)
    winner = valid[names[0]]
    others = [valid[n] for n in names[1:]]
    for other in others:
        if (other.surname, other.given_names) != (winner.surname, winner.given_names):
            return winner, CheckResult(
                "mrz", Status.WARN,
                f"Both MRZ readings pass the check digits, but the names differ: "
                f"{winner.surname} {winner.given_names} ({names[0]}) / "
                f"{other.surname} {other.given_names}. Please check the name.")
    agree = " and ".join(names)
    return winner, CheckResult("mrz", Status.OK, f"MRZ read correctly ({agree}); all check digits pass.")
