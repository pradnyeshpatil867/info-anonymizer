"""The vault: the ONLY module that reads or writes `vault.db`.

`vault.db` is a SQLite database. It is the only place with real personal
data (spec section C). Phase 1: not encrypted. In phase 2 only this file
changes (for example to SQLCipher).

Tables:
- persons:     one row per person; links the real identity to the pseudonym
- identifiers: one row per anonymized value: the real value and its fake replacement
- document_files: one row per saved file fingerprint (SHA-256), to refuse the same file twice
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    person_id              TEXT PRIMARY KEY,
    real_first_name        TEXT NOT NULL,
    real_middle_name       TEXT,
    real_last_name         TEXT NOT NULL,
    real_birth_name        TEXT,
    real_date_of_birth     TEXT NOT NULL,          -- ISO: YYYY-MM-DD
    match_key              TEXT NOT NULL UNIQUE,   -- all given names + surname + birth date
    loose_key              TEXT NOT NULL,          -- first given name + surname + birth date
    pseudonym_first_name   TEXT NOT NULL,
    pseudonym_middle_name  TEXT,
    pseudonym_last_name    TEXT NOT NULL,
    pseudonym_birth_name   TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_persons_loose_key ON persons(loose_key);

-- Example row: P_7F3K9Q | address | "Hauptstr. 1, 10115 Berlin" | "742 Evergreen Lane, Springfield, IL 62704"
CREATE TABLE IF NOT EXISTS identifiers (
    id                 INTEGER PRIMARY KEY,
    person_id          TEXT NOT NULL REFERENCES persons(person_id),
    field              TEXT NOT NULL,
    real_value         TEXT NOT NULL,
    fake_value         TEXT NOT NULL,              -- the replacement shown in the JSON file
    used_for_matching  INTEGER NOT NULL CHECK (used_for_matching IN (0, 1)),
    created_at         TEXT NOT NULL,
    UNIQUE (person_id, field, real_value),         -- same real value -> same fake value, per person
    UNIQUE (person_id, field, fake_value)          -- one fake value -> one real value, per person
);
-- A document number (used_for_matching = 1) can belong to ONE person only.
-- This is a "partial index": the rule applies only to rows where the WHERE part is true.
CREATE UNIQUE INDEX IF NOT EXISTS ux_identifiers_matching
    ON identifiers(field, real_value) WHERE used_for_matching = 1;

-- Fingerprint (SHA-256 of the file bytes) of every saved file. No file names: they can
-- contain personal data. PRIMARY KEY: the same file can be saved only once.
CREATE TABLE IF NOT EXISTS document_files (
    file_sha256  TEXT PRIMARY KEY,
    person_id    TEXT NOT NULL REFERENCES persons(person_id),
    doc_id       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class PersonRow:
    person_id: str
    real_first_name: str
    real_middle_name: str | None
    real_last_name: str
    real_birth_name: str | None
    real_date_of_birth: str
    match_key: str
    loose_key: str
    pseudonym_first_name: str
    pseudonym_middle_name: str | None
    pseudonym_last_name: str
    pseudonym_birth_name: str | None
    created_at: str
    updated_at: str


class Vault:
    def __init__(self, path: str | Path, shared_between_threads: bool = False):
        """shared_between_threads=True: the web server uses this vault from several threads.
        The caller must then make sure only one thread uses it at a time (a lock).
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: we start and end transactions ourselves (see transaction()).
        self.conn = sqlite3.connect(self.path, isolation_level=None,
                                    check_same_thread=not shared_between_threads)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(identifiers)")}
        if columns and "fake_value" not in columns:
            self.conn.close()
            raise RuntimeError(f"{self.path} uses the old token format. It cannot be converted: "
                               "delete it (and the people/ folder) and save the documents again.")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        """All changes inside the `with` block are saved together, or none of them.

        Example:
            with vault.transaction():
                vault.add_person(...)
                vault.add_identifier(...)
        If anything fails, ROLLBACK undoes every change in the block.
        """
        self.conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ---------- persons ----------

    def _person(self, row: sqlite3.Row | None) -> PersonRow | None:
        return PersonRow(**dict(row)) if row else None

    def get_person(self, person_id: str) -> PersonRow | None:
        row = self.conn.execute("SELECT * FROM persons WHERE person_id = ?", (person_id,)).fetchone()
        return self._person(row)

    def find_by_match_key(self, match_key: str) -> PersonRow | None:
        row = self.conn.execute("SELECT * FROM persons WHERE match_key = ?", (match_key,)).fetchone()
        return self._person(row)

    def find_by_loose_key(self, loose_key: str) -> list[PersonRow]:
        rows = self.conn.execute("SELECT * FROM persons WHERE loose_key = ?", (loose_key,)).fetchall()
        return [self._person(r) for r in rows]

    def find_by_pseudonym(self, first_name: str, last_name: str) -> list[PersonRow]:
        rows = self.conn.execute(
            "SELECT * FROM persons WHERE lower(pseudonym_first_name) = lower(?) "
            "AND lower(pseudonym_last_name) = lower(?)", (first_name, last_name)).fetchall()
        return [self._person(r) for r in rows]

    def all_names(self) -> list[tuple[str, str, str]]:
        """(kind, first, last) for every real name and pseudonym. Used to keep fake names unique."""
        rows = self.conn.execute(
            "SELECT real_first_name, real_last_name, pseudonym_first_name, pseudonym_last_name FROM persons"
        ).fetchall()
        out = []
        for r in rows:
            out.append(("real", r[0], r[1]))
            out.append(("pseudonym", r[2], r[3]))
        return out

    def person_id_exists(self, person_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM persons WHERE person_id = ?", (person_id,)).fetchone() is not None

    def add_person(self, row: PersonRow) -> None:
        cols = list(row.__dict__)
        sql = f"INSERT INTO persons ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        self.conn.execute(sql, [getattr(row, c) for c in cols])

    def touch_person(self, person_id: str) -> None:
        self.conn.execute("UPDATE persons SET updated_at = ? WHERE person_id = ?", (now_utc(), person_id))

    # ---------- identifiers ----------

    def find_fake(self, person_id: str, field: str, real_value: str) -> str | None:
        """The fake value this person already has for this real value, or None."""
        row = self.conn.execute(
            "SELECT fake_value FROM identifiers WHERE person_id = ? AND field = ? AND real_value = ?",
            (person_id, field, real_value)).fetchone()
        return row["fake_value"] if row else None

    def pairs_for_field(self, person_id: str, field: str) -> list[tuple[str, str]]:
        """All (real value, fake value) pairs of this person for one field."""
        rows = self.conn.execute("SELECT real_value, fake_value FROM identifiers WHERE person_id = ? AND field = ?",
                                 (person_id, field)).fetchall()
        return [(r["real_value"], r["fake_value"]) for r in rows]

    def real_from_fake(self, person_id: str, field: str, fake_value: str) -> str | None:
        """Re-identification: fake value -> real value. Not shown in the UI in version 1."""
        row = self.conn.execute(
            "SELECT real_value FROM identifiers WHERE person_id = ? AND field = ? AND fake_value = ?",
            (person_id, field, fake_value)).fetchone()
        return row["real_value"] if row else None

    def find_person_by_identifier(self, field: str, real_value: str) -> str | None:
        """Find who owns a document number (only rows used for matching)."""
        row = self.conn.execute(
            "SELECT person_id FROM identifiers WHERE field = ? AND real_value = ? AND used_for_matching = 1",
            (field, real_value)).fetchone()
        return row["person_id"] if row else None

    def add_identifier(self, person_id: str, field: str, real_value: str, fake_value: str,
                       used_for_matching: bool) -> None:
        self.conn.execute(
            "INSERT INTO identifiers (person_id, field, real_value, fake_value, used_for_matching, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (person_id, field, real_value, fake_value, int(used_for_matching), now_utc()))

    # ---------- document files (fingerprints) ----------

    def find_document_file(self, file_sha256: str) -> tuple[str, str] | None:
        """(person_id, doc_id) of the document that already holds this exact file, or None."""
        row = self.conn.execute("SELECT person_id, doc_id FROM document_files WHERE file_sha256 = ?",
                                (file_sha256,)).fetchone()
        return (row["person_id"], row["doc_id"]) if row else None

    def add_document_file(self, file_sha256: str, person_id: str, doc_id: str) -> None:
        self.conn.execute("INSERT INTO document_files (file_sha256, person_id, doc_id, created_at) "
                          "VALUES (?, ?, ?, ?)", (file_sha256, person_id, doc_id, now_utc()))
