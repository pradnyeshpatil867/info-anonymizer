import pytest

from app.checks.normalize import match_key, normalize_date, normalize_name, split_name


@pytest.mark.parametrize("raw, expected", [
    ("MÜLLER", "mueller"),
    ("Müller", "mueller"),
    ("Mueller", "mueller"),
    ("Strauß", "strauss"),
    ("Hans-Jürgen", "hans juergen"),
    ("Dr. Anna Schmidt", "anna schmidt"),
    ("Prof. Dr. Anna Schmidt", "anna schmidt"),
    ("Dr. phil. Anna Schmidt", "anna schmidt"),
    ("Dipl.-Ing. Tom Bauer", "tom bauer"),
    ("Phil Weber", "phil weber"),          # "Phil" is a name, not a title
    ("François Lefèvre", "francois lefevre"),
    ("  Anna   SCHMIDT ", "anna schmidt"),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_umlaut_and_ue_spelling_give_same_result():
    assert normalize_name("Jonas Müller") == normalize_name("JONAS MUELLER")


def test_missing_dots_give_a_different_result():
    # OCR error "Muller" is NOT the same as "Müller". The identifiers table
    # and the review screen handle this case (spec section E).
    assert normalize_name("Muller") != normalize_name("Müller")


@pytest.mark.parametrize("raw, expected", [
    ("MÜLLER, Jonas Peter", ("Jonas Peter", "MÜLLER")),
    ("Jonas Müller", ("Jonas", "Müller")),
    ("Jonas Peter Müller", ("Jonas Peter", "Müller")),
    ("Müller", ("", "Müller")),
])
def test_split_name(raw, expected):
    assert split_name(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("12.04.1990", "1990-04-12"),
    ("1.4.1990", "1990-04-01"),
    ("12/04/1990", "1990-04-12"),
    ("12-04-1990", "1990-04-12"),
    ("1990-04-12", "1990-04-12"),
    (" 12.04.1990 ", "1990-04-12"),
])
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize("raw", ["31.02.1990", "12.13.1990", "1990", "12.04.90", "abc"])
def test_normalize_date_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_date(raw)


def test_match_key():
    assert match_key("Jonas", "Müller", "12.04.1990") == "jonas mueller|1990-04-12"


def test_match_key_same_for_different_spellings():
    a = match_key("Jonas", "MÜLLER", "12.04.1990")
    b = match_key("JONAS", "Mueller", "1990-04-12")
    c = match_key("Jonas", "Dr. Müller", "12/04/1990")
    assert a == b == c


def test_match_key_rejects_empty_name():
    with pytest.raises(ValueError):
        match_key("", "", "12.04.1990")


def test_date_with_spaces_as_printed_on_newer_cards():
    assert normalize_date("14 03 1988") == "1988-03-14"
    assert normalize_date("1 2 1988") == "1988-02-01"
