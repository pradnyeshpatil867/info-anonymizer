"""Read and write person JSON files in the `people/` folder.

Each person has one file: people/P_7F3K9Q.json. It contains no real
personal data: only the pseudonym, fake values and clear group B values.
"""

import json
import os
import re
from pathlib import Path

_PERSON_ID = re.compile(r"^P_[A-Z0-9]{6}$")


class PeopleStore:
    def __init__(self, folder: str | Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def path(self, person_id: str) -> Path:
        # Only accept real person ids, so a bad value like "../x" can never write outside the folder.
        if not _PERSON_ID.match(person_id):
            raise ValueError(f"Invalid person_id: {person_id!r}")
        return self.folder / f"{person_id}.json"

    def exists(self, person_id: str) -> bool:
        return self.path(person_id).exists()

    def load(self, person_id: str) -> dict | None:
        p = self.path(person_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def write_temp(self, person_id: str, data: dict) -> Path:
        """Step 1 of a safe save: write to a temporary file next to the real one."""
        tmp = self.path(person_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return tmp

    def commit_temp(self, person_id: str, tmp: Path) -> None:
        """Step 2: replace the real file in one step (atomic rename).

        If the computer stops during step 1, the old file is still complete.
        """
        os.replace(tmp, self.path(person_id))

    def save(self, person_id: str, data: dict) -> None:
        self.commit_temp(person_id, self.write_temp(person_id, data))

    def list_ids(self) -> list[str]:
        return sorted(p.stem for p in self.folder.glob("P_*.json"))
