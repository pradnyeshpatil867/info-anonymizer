import pytest

from app.checks.result import CheckResult, Status, worst
from app.checks.validate import (check_date, check_iban, check_issue_before_expiry,
                                 check_steuer_id, steuer_id_check_digit)


def test_check_date():
    assert check_date("12.04.1990").status is Status.OK
    assert check_date("31.02.1990").status is Status.FAIL


def test_issue_before_expiry():
    assert check_issue_before_expiry("01.03.2020", "28.02.2030").status is Status.OK
    assert check_issue_before_expiry("01.03.2030", "28.02.2020").status is Status.FAIL


# Widely used valid example IBAN for Germany.
def test_valid_german_iban():
    assert check_iban("DE89 3704 0044 0532 0130 00").status is Status.OK


def test_iban_with_one_wrong_digit():
    assert check_iban("DE89 3704 0044 0532 0130 01").status is Status.FAIL


def test_german_iban_wrong_length():
    assert check_iban("DE89 3704 0044 0532 0130").status is Status.FAIL


def test_steuer_id_check_digit():
    # Widely used example Steuer-ID 86095742719; the ELSTER algorithm gives 9.
    assert steuer_id_check_digit("8609574271") == 9


def test_valid_steuer_id():
    assert check_steuer_id("86095742719").status is Status.OK


@pytest.mark.parametrize("value, reason", [
    ("86095742718", "wrong check digit"),
    ("8609574271", "only 10 digits"),
    ("06095742719", "starts with 0"),
    ("12345678901", "no repeated digit"),
    ("11223456789", "two repeated digits"),
])
def test_invalid_steuer_id(value, reason):
    assert check_steuer_id(value).status is Status.FAIL, reason


def test_steuer_id_three_equal_digits_in_a_row():
    first_ten = "1112345678"
    value = first_ten + str(steuer_id_check_digit(first_ten))
    assert check_steuer_id(value).status is Status.FAIL


def test_steuer_id_three_equal_digits_not_in_a_row_is_allowed():
    first_ten = "1121345678"  # three 1s, but not all three in a row
    value = first_ten + str(steuer_id_check_digit(first_ten))
    assert check_steuer_id(value).status is Status.OK


def test_worst_status():
    ok = CheckResult("a", Status.OK, "")
    warn = CheckResult("b", Status.WARN, "")
    fail = CheckResult("c", Status.FAIL, "")
    assert worst([ok, ok]) is Status.OK
    assert worst([ok, warn]) is Status.WARN
    assert worst([warn, fail, ok]) is Status.FAIL
