"""MRZ tests.

Specimens:
- ICAO TD3 and TD1 specimens (Anna Maria Eriksson, Utopia) from ICAO Doc 9303.
- German ID card specimen (Erika Mustermann). Typed from memory; it passes
  all four check digits, so it is self-consistent.
"""

import pytest

from app.checks.mrz import (char_value, check_digit, clean_lines, mrz_date_to_iso,
                            parse_mrz)
from app.checks.result import Status

ICAO_TD3 = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
)
ICAO_TD1 = (
    "I<UTOD231458907<<<<<<<<<<<<<<<\n"
    "7408122F1204159UTO<<<<<<<<<<<6\n"
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<"
)
GERMAN_ID = (
    "IDD<<T220001293<<<<<<<<<<<<<<<\n"
    "6408125<2010315D<<<<<<<<<<<<<4\n"
    "MUSTERMANN<<ERIKA<<<<<<<<<<<<<"
)


def test_char_values():
    assert char_value("0") == 0
    assert char_value("9") == 9
    assert char_value("A") == 10
    assert char_value("Z") == 35
    assert char_value("<") == 0


@pytest.mark.parametrize("data, digit", [
    ("L898902C3", 6),       # ICAO passport number
    ("740812", 2),          # ICAO birth date
    ("120415", 9),          # ICAO expiry date
    ("ZE184226B<<<<<", 1),  # ICAO personal number
])
def test_check_digit_icao_specimen(data, digit):
    assert check_digit(data) == digit


def test_parse_icao_td3():
    r = parse_mrz(ICAO_TD3)
    assert r.layout == "TD3"
    assert r.valid
    assert r.document_code == "P"
    assert r.issuing_state == "UTO"
    assert r.surname == "ERIKSSON"
    assert r.given_names == "ANNA MARIA"
    assert r.document_number == "L898902C3"
    assert r.birth_date == "740812"
    assert r.sex == "F"


def test_parse_icao_td1():
    r = parse_mrz(ICAO_TD1)
    assert r.layout == "TD1"
    assert r.valid
    assert r.document_number == "D23145890"


def test_parse_german_id_card():
    r = parse_mrz(GERMAN_ID)
    assert r.valid
    assert r.document_code == "ID"
    assert r.issuing_state == "D"
    assert r.issued_in_germany
    assert r.nationality == "D"
    assert r.surname == "MUSTERMANN"
    assert r.given_names == "ERIKA"
    assert r.document_number == "T22000129"


def test_one_misread_digit_is_detected():
    # Birth date 640812 misread as 640813: the birth date check digit must fail.
    broken = GERMAN_ID.replace("6408125", "6408135")
    r = parse_mrz(broken)
    assert not r.valid
    failed = [c.name for c in r.checks if c.status is Status.FAIL]
    assert "mrz_birth_date" in failed


def test_misread_letter_in_document_number_is_detected():
    broken = GERMAN_ID.replace("T22000129", "T22OOO129")  # 0 read as O
    assert not parse_mrz(broken).valid


def test_not_issued_in_germany():
    assert not parse_mrz(ICAO_TD1).issued_in_germany


def test_clean_lines_removes_spaces_and_empty_lines():
    text = "  idd<<t22 0001293<<<<<<<<<<<<<<<\n\n"
    assert clean_lines(text) == ["IDD<<T220001293<<<<<<<<<<<<<<<"]


def test_unknown_layout_raises():
    with pytest.raises(ValueError):
        parse_mrz("ABC\nDEF")


def test_mrz_dates():
    assert mrz_date_to_iso("740812", "birth") == "1974-08-12"
    assert mrz_date_to_iso("100101", "birth") == "2010-01-01"
    assert mrz_date_to_iso("201031", "expiry") == "2020-10-31"


# --- Repairing OCR output (milestone 3) ---

from app.checks.mrz import extract_mrz_lines, fit_filler  # noqa: E402


def test_extract_mrz_lines_ignores_normal_text():
    text = "Augenfarbe/Colour of eyes\nGRÜN\nIDD<<T220001293<<<<<<<<<<<<<<<\nBUNDESDRUCKEREI"
    assert extract_mrz_lines(text) == ["IDD<<T220001293<<<<<<<<<<<<<<<"]


def test_fit_filler_adds_missing_fillers():
    # Real Tesseract output from the ID card back: line 1 had only 28 characters.
    lines = ["IDD<<T220001293<<<<<<<<<<<<<", "6408125<2010315D<<<<<<<<<<<<<4", "MUSTERMANN<<ERIKA<<<<<<<<<<<<<"]
    assert [len(x) for x in fit_filler(lines)] == [30, 30, 30]


def test_fit_filler_removes_extra_fillers():
    lines = ["IDD<<T220001293<<<<<<<<<<<<<<<<<", "6408125<2010315D<<<<<<<<<<<<<4", "MUSTERMANN<<ERIKA<<<<<<<<<<<<<"]
    assert fit_filler(lines)[0] == "IDD<<T220001293<<<<<<<<<<<<<<<"


def test_fit_filler_never_changes_letters_or_digits():
    line = "MUSTERMANN<<ERIKAK<<<<<<<<<<<<<"  # 31 chars, extra "K" from OCR
    fixed = fit_filler([GERMAN_ID.splitlines()[0], GERMAN_ID.splitlines()[1], line])[2]
    assert fixed.rstrip("<") == "MUSTERMANN<<ERIKAK"  # the wrong K stays; the name check must catch it


def test_repaired_mrz_parses():
    text = "IDD<<T220001293<<<<<<<<<<<<<\n6408125<2010315D<<<<<<<<<<<<<4\nMUSTERMANN<<ERIKA<<<<<<<<<<<<<"
    assert parse_mrz(text).valid


# --- Choosing between MRZ readings, and BDR (milestone 3 decisions) ---

from app.checks.mrz import choose_mrz  # noqa: E402

GERMAN_PASSPORT = (  # from the official passport specimen (Bundesdruckerei)
    "P<BDRMUSTERMANN<<ERIKA<<<<<<<<<<<<<<<<<<<<<<\n"
    "C1000000<7D<<6408125F1809244<<<<<<<<<<<<<<<8"
)


def test_bdr_counts_as_germany():
    r = parse_mrz(GERMAN_PASSPORT)
    assert r.valid and r.issuing_state == "BDR" and r.issued_in_germany


def test_choose_mrz_both_agree():
    chosen, verdict = choose_mrz({"PaddleOCR": GERMAN_ID, "Tesseract": GERMAN_ID})
    assert verdict.status is Status.OK and chosen.given_names == "ERIKA"


def test_choose_mrz_names_differ_is_yellow():
    # Real case from the ID card back: Tesseract read ERIKAK, check digits still pass.
    tess = GERMAN_ID.replace("ERIKA<<<<<<<<<<<<<", "ERIKAK<<<<<<<<<<<<")
    chosen, verdict = choose_mrz({"PaddleOCR": GERMAN_ID, "Tesseract": tess})
    assert verdict.status is Status.WARN
    assert chosen.given_names == "ERIKA"  # main engine wins the tie


def test_choose_mrz_only_valid_reading_wins():
    broken = GERMAN_ID.replace("6408125", "6408135")
    chosen, verdict = choose_mrz({"PaddleOCR": broken, "Tesseract": GERMAN_ID})
    assert verdict.status is Status.OK and chosen.valid


def test_choose_mrz_all_fail():
    broken = GERMAN_ID.replace("6408125", "6408135")
    chosen, verdict = choose_mrz({"PaddleOCR": broken, "Tesseract": None})
    assert verdict.status is Status.FAIL and chosen is not None and not chosen.valid


def test_choose_mrz_nothing_found():
    chosen, verdict = choose_mrz({"PaddleOCR": None, "Tesseract": "no mrz here"})
    assert verdict.status is Status.FAIL and chosen is None
