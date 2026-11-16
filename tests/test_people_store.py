import pytest

from app.storage.people import PeopleStore


def test_save_and_load(tmp_path):
    s = PeopleStore(tmp_path)
    s.save("P_ABC123", {"person_id": "P_ABC123", "pseudonym": {"first_name": "Jöhn"}})
    assert s.load("P_ABC123")["pseudonym"]["first_name"] == "Jöhn"
    assert s.list_ids() == ["P_ABC123"]


def test_no_temp_file_left_after_save(tmp_path):
    s = PeopleStore(tmp_path)
    s.save("P_ABC123", {"a": 1})
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_save_keeps_old_file(tmp_path):
    s = PeopleStore(tmp_path)
    s.save("P_ABC123", {"version": 1})
    s.write_temp("P_ABC123", {"version": 2})   # crash before commit_temp
    assert s.load("P_ABC123") == {"version": 1}


@pytest.mark.parametrize("bad", ["../evil", "P_abc123", "P_ABC12", "x.json", ""])
def test_invalid_person_id_is_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        PeopleStore(tmp_path).path(bad)


def test_missing_person_returns_none(tmp_path):
    assert PeopleStore(tmp_path).load("P_ZZZZZZ") is None
