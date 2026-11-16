import re
import sqlite3

import pytest
from faker import Faker

from app.checks.validate import check_steuer_id
from app.identity.fakes import fake_for
from app.identity.fields import normalize_value
from app.identity.vault import PersonRow, Vault


def person(pid="P_AAAAAA", key="jonas mueller|1990-04-12", loose="jonas mueller|1990-04-12"):
    return PersonRow(pid, "Jonas", None, "Müller", None, "1990-04-12", key, loose,
                     "John", None, "Miller", None, "2026-01-01 00:00:00", "2026-01-01 00:00:00")


def test_tables_are_created(vault):
    names = {r[0] for r in vault.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"persons", "identifiers"} <= names


def test_add_and_find_person(vault):
    with vault.transaction():
        vault.add_person(person())
    assert vault.find_by_match_key("jonas mueller|1990-04-12").person_id == "P_AAAAAA"
    assert vault.find_by_match_key("nobody|2000-01-01") is None


def test_duplicate_match_key_is_refused(vault):
    with vault.transaction():
        vault.add_person(person())
    with pytest.raises(sqlite3.IntegrityError):
        with vault.transaction():
            vault.add_person(person(pid="P_BBBBBB"))


def test_rollback_undoes_everything(vault):
    """If one step fails, the earlier steps in the same transaction are undone too."""
    with pytest.raises(sqlite3.IntegrityError):
        with vault.transaction():
            vault.add_person(person(pid="P_AAAAAA", key="a|2000-01-01"))
            vault.add_person(person(pid="P_BBBBBB", key="a|2000-01-01"))  # duplicate key -> fails
    assert vault.get_person("P_AAAAAA") is None


FAKER = Faker("en_US")
FAKER.seed_instance(1)


def fk(vault, pid, field, value):
    """What the app does: store the value in one form, then get its fake value."""
    return fake_for(vault, FAKER, pid, field, normalize_value(field, value))


def test_document_number_belongs_to_one_person(vault):
    with vault.transaction():
        vault.add_person(person("P_AAAAAA", "a|2000-01-01"))
        vault.add_person(person("P_BBBBBB", "b|2000-01-01"))
        fk(vault, "P_AAAAAA", "id_card_number", "L01X00T47")
    with pytest.raises(sqlite3.IntegrityError):
        with vault.transaction():
            fk(vault, "P_BBBBBB", "id_card_number", "L01X00T47")


def test_shared_address_is_allowed_with_different_fakes(vault):
    with vault.transaction():
        vault.add_person(person("P_AAAAAA", "a|2000-01-01"))
        vault.add_person(person("P_BBBBBB", "b|2000-01-01"))
        f1 = fk(vault, "P_AAAAAA", "address", "Hauptstr. 1, 10115 Berlin")
        f2 = fk(vault, "P_BBBBBB", "address", "Hauptstr. 1, 10115 Berlin")
    assert f1 != f2 and "Hauptstr" not in f1 + f2


def test_same_value_same_person_same_fake(vault):
    with vault.transaction():
        vault.add_person(person())
        a = fk(vault, "P_AAAAAA", "id_card_number", "L01X00T47")
        b = fk(vault, "P_AAAAAA", "id_card_number", "l01x 00t47")  # other spelling, same number
    assert a == b != "L01X00T47"
    assert vault.real_from_fake("P_AAAAAA", "id_card_number", a) == "L01X00T47"


def test_fake_values_have_the_right_kind(vault):
    with vault.transaction():
        vault.add_person(person())
        number = fk(vault, "P_AAAAAA", "id_card_number", "T22000129")
        dob = fk(vault, "P_AAAAAA", "date_of_birth", "12.04.1990")
        height = fk(vault, "P_AAAAAA", "height", "160 cm")
        tax = fk(vault, "P_AAAAAA", "steuer_id", "12345678903")
    assert len(number) == 9 and number[0].isalpha() and number[1:].isdigit()   # same pattern as T22000129
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob) and dob != "1990-04-12"
    assert re.fullmatch(r"\d{3} cm", height) and height != "160 cm"
    assert len(tax) == 11 and check_steuer_id(tax).status.value == "ok"      # a valid-looking tax ID


def test_old_token_database_is_refused(tmp_path):
    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("CREATE TABLE identifiers (token TEXT PRIMARY KEY, person_id TEXT, field TEXT, real_value TEXT)")
    old.close()
    with pytest.raises(RuntimeError, match="old token format"):
        Vault(tmp_path / "old.db")


def test_data_survives_reopening(tmp_path):
    v = Vault(tmp_path / "vault.db")
    with v.transaction():
        v.add_person(person())
    v.close()
    v2 = Vault(tmp_path / "vault.db")
    assert v2.get_person("P_AAAAAA").real_last_name == "Müller"
    v2.close()
