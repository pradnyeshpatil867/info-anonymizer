from app.checks.compare import (check_name_against_mrz, pick_reading, similarity,
                                strip_dots, to_mrz_style, umlaut_candidates)
from app.checks.result import Status


def test_similarity():
    assert similarity("abc", "abc") == 1.0
    assert round(similarity("MULLER", "MUELLER"), 2) == 0.92


def test_to_mrz_style():
    assert to_mrz_style("Müller") == "MUELLER"
    assert to_mrz_style("Strauß") == "STRAUSS"
    assert to_mrz_style("Müller-Lüdenscheidt") == "MUELLER LUEDENSCHEIDT"
    assert to_mrz_style("Lefèvre") == "LEFEVRE"


def test_strip_dots():
    assert strip_dots("Müller") == "MULLER"


def test_umlaut_candidates():
    assert set(umlaut_candidates("MUELLER")) == {"MUELLER", "MÜLLER"}
    assert umlaut_candidates("SCHMIDT") == ["SCHMIDT"]


# --- Printed name vs. MRZ (the four situations from the learning page) ---

def test_correct_umlaut_is_ok():
    result, value = check_name_against_mrz("MÜLLER", "MUELLER")
    assert result.status is Status.OK
    assert value == "MÜLLER"


def test_real_ue_spelling_is_ok():
    # Some people really are called "Mueller". That must stay OK, not be "repaired".
    result, value = check_name_against_mrz("MUELLER", "MUELLER")
    assert result.status is Status.OK
    assert value == "MUELLER"


def test_missing_dots_are_repaired_with_warning():
    result, value = check_name_against_mrz("MULLER", "MUELLER")
    assert result.status is Status.WARN
    assert value == "MÜLLER"


def test_wrong_name_fails():
    result, value = check_name_against_mrz("MILLER", "MUELLER")
    assert result.status is Status.FAIL
    assert value is None


def test_given_names_with_space():
    result, _ = check_name_against_mrz("Anna Maria", "ANNA MARIA", field="given_names")
    assert result.status is Status.OK


# --- Two OCR engines ---

def test_engines_agree():
    result, value = pick_reading("MÜLLER", "MÜLLER")
    assert result.status is Status.OK and value == "MÜLLER"


def test_engines_differ_only_in_dots_keeps_dots():
    result, value = pick_reading("MULLER", "MÜLLER")
    assert result.status is Status.WARN and value == "MÜLLER"
    result, value = pick_reading("MÜLLER", "MULLER")
    assert value == "MÜLLER"


def test_engines_really_disagree():
    result, value = pick_reading("MÜLLER", "MILLER")
    assert result.status is Status.FAIL and value is None
