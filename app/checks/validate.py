"""Format checks for single fields: dates, IBAN, Steuer-ID.

Sources:
- IBAN: ISO 13616 (check digits with modulo 97).
- Steuer-ID: ELSTER document "Prüfung der Steuer- und
  Steueridentifikationsnummer", section 2.2 (ISO 7064 MOD 11,10).
"""

from collections import Counter

from app.checks.normalize import normalize_date
from app.checks.result import CheckResult, Status

_IBAN_LENGTH = {"DE": 22}  # add more countries when needed


def check_date(text: str, field: str = "date") -> CheckResult:
    """The text must be a real date, for example 12.04.1990."""
    try:
        iso = normalize_date(text)
    except ValueError:
        return CheckResult(field, Status.FAIL, f"{text!r} is not a valid date.")
    return CheckResult(field, Status.OK, f"{text} is a valid date ({iso}).")


def check_issue_before_expiry(issue: str, expiry: str) -> CheckResult:
    """The expiry date must be after the issue date."""
    try:
        i, e = normalize_date(issue), normalize_date(expiry)
    except ValueError:
        return CheckResult("issue_expiry", Status.FAIL, "Issue or expiry date is not a valid date.")
    if e > i:  # ISO dates compare correctly as text
        return CheckResult("issue_expiry", Status.OK, "Expiry date is after issue date.")
    return CheckResult("issue_expiry", Status.FAIL, f"Expiry date {expiry} is not after issue date {issue}.")


def check_iban(iban: str) -> CheckResult:
    """Check length (for known countries) and the two IBAN check digits."""
    s = iban.replace(" ", "").upper()
    if len(s) < 5 or not s[:2].isalpha() or not s[2:4].isdigit() or not s.isalnum():
        return CheckResult("iban", Status.FAIL, "IBAN has a wrong format.")
    expected = _IBAN_LENGTH.get(s[:2])
    if expected and len(s) != expected:
        return CheckResult("iban", Status.FAIL, f"A {s[:2]} IBAN has {expected} characters, this one has {len(s)}.")
    # Move the first 4 characters to the end, turn letters into numbers (A=10 ... Z=35).
    rearranged = s[4:] + s[:4]
    number = int("".join(str(int(ch, 36)) for ch in rearranged))
    if number % 97 == 1:
        return CheckResult("iban", Status.OK, "IBAN check digits are correct.")
    return CheckResult("iban", Status.FAIL, "IBAN check digits are wrong. A digit was probably misread.")


def steuer_id_check_digit(first_ten: str) -> int:
    """Check digit of a German tax ID (ISO 7064 MOD 11,10), as published by ELSTER."""
    product = 10
    for ch in first_ten:
        total = (int(ch) + product) % 10
        if total == 0:
            total = 10
        product = (2 * total) % 11
    digit = 11 - product
    return 0 if digit == 10 else digit


def check_steuer_id(value: str, allow_test_ids: bool = False) -> CheckResult:
    """Check a German tax ID (Steuerliche Identifikationsnummer)."""
    s = value.replace(" ", "")
    if len(s) != 11 or not s.isdigit():
        return CheckResult("steuer_id", Status.FAIL, "Steuer-ID must have exactly 11 digits.")
    if s[0] == "0" and not allow_test_ids:
        return CheckResult("steuer_id", Status.FAIL, "Steuer-ID never starts with 0.")

    counts = Counter(s[:10])
    repeated = [d for d, c in counts.items() if c > 1]
    # Exactly one digit appears twice or three times in the first 10 digits.
    if len(repeated) != 1 or counts[repeated[0]] not in (2, 3):
        return CheckResult("steuer_id", Status.FAIL, "Steuer-ID digit pattern is not allowed.")
    # Three equal digits must not stand directly in a row.
    if counts[repeated[0]] == 3 and repeated[0] * 3 in s[:10]:
        return CheckResult("steuer_id", Status.FAIL, "Steuer-ID has three equal digits in a row.")

    if steuer_id_check_digit(s[:10]) != int(s[10]):
        return CheckResult("steuer_id", Status.FAIL, "Steuer-ID check digit is wrong.")
    return CheckResult("steuer_id", Status.OK, "Steuer-ID is valid.")
