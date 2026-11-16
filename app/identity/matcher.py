"""Find out if a document belongs to a person we already know (spec section E).

Order of the checks:
1. match_key (all given names + surname + birth date)  -> "existing" (if no conflict)
2. document number already known                         -> "confirm" (user decides)
3. loose_key (FIRST given name + surname + birth date)   -> "confirm"
4. birth name (Geburtsname) instead of surname            -> "confirm"
5. nothing found                                          -> "new"

The app never merges two people without the user, except for case 1.
"""

from dataclasses import dataclass, field

from app.checks.normalize import match_key, normalize_date, normalize_name
from app.identity.fields import MATCH_FIELDS, normalize_value
from app.identity.vault import Vault


@dataclass
class Identity:
    """Identity data extracted from one document (real values)."""
    given_names: str
    surname: str
    date_of_birth: str                       # any accepted date format, for example 12.04.1990
    birth_name: str | None = None
    gender: str | None = None                # "M", "F" or None
    document_numbers: dict[str, str] = field(default_factory=dict)  # field -> number

    @property
    def key(self) -> str:
        return match_key(self.given_names, self.surname, self.date_of_birth)

    @property
    def loose(self) -> str:
        return loose_key(self.given_names, self.surname, self.date_of_birth)


def loose_key(given_names: str, surname: str, date_of_birth: str) -> str:
    """Like match_key, but with the first given name only.

    >>> loose_key("Jonas Peter", "Müller", "12.04.1990")
    'jonas mueller|1990-04-12'
    """
    first = normalize_name(given_names).split(" ")[0] if normalize_name(given_names) else ""
    return f"{' '.join(x for x in (first, normalize_name(surname)) if x)}|{normalize_date(date_of_birth)}"


@dataclass
class MatchResult:
    kind: str                     # "existing", "confirm", "conflict" or "new"
    person_id: str | None
    reason: str
    candidates: list[str] = field(default_factory=list)


def find_person(vault: Vault, ident: Identity) -> MatchResult:
    # Who owns the document numbers on this document?
    owners: dict[str, str] = {}
    for f, number in ident.document_numbers.items():
        if f in MATCH_FIELDS and number:
            pid = vault.find_person_by_identifier(f, normalize_value(f, number))
            if pid:
                owners[f] = pid

    # 1. Exact match on name + birth date.
    person = vault.find_by_match_key(ident.key)
    if person:
        others = {pid for pid in owners.values() if pid != person.person_id}
        if others:
            return MatchResult("conflict", person.person_id,
                               f"Name and birth date match {person.person_id}, but a document number "
                               f"belongs to {', '.join(sorted(others))}. Please check.",
                               [person.person_id, *sorted(others)])
        return MatchResult("existing", person.person_id, "Exact match on name and birth date.")

    # 2. A known document number.
    if owners:
        pids = sorted(set(owners.values()))
        if len(pids) > 1:
            return MatchResult("conflict", None, "Document numbers belong to different persons. Please check.", pids)
        f = next(iter(owners))
        return MatchResult("confirm", pids[0],
                           f"Same {f.replace('_', ' ')} as {pids[0]}, but the name or birth date differs.", pids)

    # 3. Same first given name, surname and birth date (other given names may be missing).
    loose = vault.find_by_loose_key(ident.loose)
    if loose:
        pids = [p.person_id for p in loose]
        return MatchResult("confirm", pids[0] if len(pids) == 1 else None,
                           "Same first given name, surname and birth date, but other given names differ.", pids)

    # 4. Name changed (for example after marriage): the new document's birth name
    #    is the known person's surname.
    if ident.birth_name:
        by_birth = vault.find_by_loose_key(loose_key(ident.given_names, ident.birth_name, ident.date_of_birth))
        if by_birth:
            pids = [p.person_id for p in by_birth]
            return MatchResult("confirm", pids[0] if len(pids) == 1 else None,
                               "The birth name matches a known person's surname. Maybe the name changed.", pids)

    return MatchResult("new", None, "Nobody matches. A new person will be created.")
