"""Shared test helpers ("fixtures").

A fixture is a function pytest runs before a test and passes in by name.
`tmp_path` is a built-in fixture: a new empty folder for every test, so
tests never touch the real data/ or people/ folders.
"""

import pytest

from app.identity.pseudonym import PseudonymGenerator
from app.identity.vault import Vault
from app.storage.people import PeopleStore


@pytest.fixture
def vault(tmp_path):
    v = Vault(tmp_path / "vault.db")
    yield v
    v.close()


@pytest.fixture
def store(tmp_path):
    return PeopleStore(tmp_path / "people")


@pytest.fixture
def gen():
    return PseudonymGenerator(locale="en_US", seed=42)
