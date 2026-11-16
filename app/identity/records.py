"""Save an APPROVED document: update vault.db and the person's JSON file.

This runs only after the user clicked Approve in the review screen (spec section H).

JSON layout (user decision 2026-11-16, option B): no person block. The file has
person_id, pseudonym and a list of document blocks. Each block holds ONLY the values
that were not saved before for this person (new fields, or a different value).
Example: residence permit first (all its values), then the passport: only the passport
number, its dates and anything else that is new.
In each block the values come first (fixed order), then "anonymized_fields", then "file_sha256".

Anonymization (spec section C): group A fields and the group B fields the user ticked
are replaced by FAKE values (fakes.py). "anonymized_fields" lists which values in the
block are fake. Values are always COMPARED by their real value.

Duplicates (spec section F):
4. exact same file(s) again                  -> refused (DuplicateDocumentError)
5. same document (same type + same number)   -> nothing new: refused; else a block with ONLY
                                                the new values and "updates": "<doc_id>"
6. any document with no new value at all     -> refused ("Nothing new")
"""

import hashlib
import re
import secrets
import string
from dataclasses import dataclass, field

from app.checks.normalize import normalize_date, normalize_name
from app.identity.fakes import fake_for
from app.identity.fields import (ALWAYS_ANONYMIZED, DATE_FIELDS, KNOWN_FIELDS, MATCH_FIELDS, OPTIONAL_ANONYMIZED,
                                 compare_key, normalize_value, same_value)
from app.identity.matcher import Identity, loose_key
from app.identity.pseudonym import PseudonymGenerator, taken_checker
from app.identity.vault import PersonRow, Vault, now_utc
from app.storage.people import PeopleStore

# Fields with one true value per person. A different value on a later document is kept,
# with a warning (for example a different nationality).
SINGLE_FIELDS = {"date_of_birth", "place_of_birth", "gender", "nationality", "height", "eye_colour", "steuer_id"}

# The order of the values inside a document block (easier to read). Unknown fields come last.
FIELD_ORDER = ["date_of_birth", "place_of_birth", "gender", "nationality", "height", "eye_colour",
               "address", "phone", "email", "iban", "steuer_id", "employer",
               "id_card_number", "passport_number", "residence_permit_number", "driving_licence_number",
               "permit_type", "issue_date", "expiry_date", "issuing_authority", "remarks"]

# The JSON key that lists which values are fake (point 4 of the decision 2026-11-16).
ANON_KEY = "anonymized_fields"
# Keys of a document entry that describe the entry itself, not the person's data.
# Clear text fields in which anonymized values were replaced (for example a passport number
# inside the remarks). Only written when not empty.
PARTLY_KEY = "partly_anonymized_fields"
META_KEYS = {"doc_id", "doc_type", "issuing_country", "file_sha256", "updates", ANON_KEY, PARTLY_KEY}
# Shorter values are not searched inside texts (too many false hits, for example "M" for gender).
MIN_HIDE_LENGTH = 4


@dataclass
class ApprovedDocument:
    """A document after the user approved all fields (real values)."""
    doc_type: str                     # for example "DE_ID_CARD", "FORM_KYC"
    issuing_country: str              # "DEU"
    identity: Identity
    fields: dict[str, str] = field(default_factory=dict)  # field name -> real value
    file_hashes: list[str] = field(default_factory=list)  # SHA-256 of each file (see file_hashes())
    anonymize: set[str] = field(default_factory=set)      # group B fields the user ticked "Anonymize"


@dataclass
class SaveResult:
    person_id: str
    created: bool
    warnings: list[str] = field(default_factory=list)


class DuplicateDocumentError(ValueError):
    """The document brings nothing new. Nothing was saved."""


def file_hashes(files: list[bytes]) -> list[str]:
    """Fingerprint of each file. Same bytes -> same code. Example: "a3f9..." (64 characters)."""
    return list(dict.fromkeys(hashlib.sha256(b).hexdigest() for b in files if b))


def exact_copy_message(vault: Vault, hashes: list[str]) -> str | None:
    """A message if EVERY file is already saved (rule 4), else None."""
    if not hashes:
        return None
    found = [vault.find_document_file(h) for h in hashes]
    if any(f is None for f in found):
        return None
    person_id, doc_id = found[0]
    return f"This file was already saved ({doc_id} of person {person_id}). Nothing was saved."


def merged_document(documents: list[dict], doc_id: str) -> dict:
    """The current values of one document, as stored: its entry plus all entries that update it."""
    merged: dict = {}
    for d in documents:
        if d.get("doc_id") == doc_id or d.get("updates") == doc_id:
            merged.update({k: v for k, v in d.items() if k not in META_KEYS})
    return merged


def _real_of(vault: Vault, person_id: str, f: str, value: str, is_fake: bool) -> str:
    """The real value behind a stored value. A clear-text value is already real."""
    if not is_fake:
        return value
    real = vault.real_from_fake(person_id, f, value)
    return real if real is not None else value


def _merged_real(vault: Vault, person_id: str, documents: list[dict], doc_id: str) -> dict:
    """Like merged_document(), but with the REAL values (fake values are looked up in the vault)."""
    merged: dict = {}
    for d in documents:
        if d.get("doc_id") == doc_id or d.get("updates") == doc_id:
            fake = set(d.get(ANON_KEY, []))
            merged.update({k: _real_of(vault, person_id, k, v, k in fake)
                           for k, v in d.items() if k not in META_KEYS})
    return merged


def _find_same_document(vault: Vault, person_id: str, documents: list[dict], doc_type: str,
                        real: dict) -> dict | None:
    """The saved block of the SAME real document (same type + same document number), or None."""
    numbers = {f: v for f, v in real.items() if f in MATCH_FIELDS}
    if not numbers:
        return None
    for d in documents:
        if d.get("updates") or d.get("doc_type") != doc_type:
            continue
        merged = _merged_real(vault, person_id, documents, d["doc_id"])
        if any(same_value(merged.get(f), v) for f, v in numbers.items()):
            return d
    return None


def _known_real(vault: Vault, person_id: str, documents: list[dict]) -> dict[str, set[str]]:
    """Every REAL value already saved for this person, per field, from all document blocks.

    Returned as compare keys (see fields.compare_key), so "CHENNAI" and "Chennai" are one value."""
    known: dict[str, set[str]] = {}
    for d in documents:
        fake = set(d.get(ANON_KEY, []))
        for k, v in d.items():
            if k not in META_KEYS:
                known.setdefault(k, set()).add(compare_key(_real_of(vault, person_id, k, v, k in fake)))
    return known


def _hide_inside(text: str, replacements: list[tuple[str, str, str]]) -> str:
    """Replace anonymized values that also appear INSIDE a clear text (decision 2026-11-16, option b).

    replacements: (field, real value, fake value).
    Example: remarks "... PASS(ERSATZ) X1234567 BIS ..." with passport_number X1234567 -> W5919091
             becomes "... PASS(ERSATZ) W5919091 BIS ...".
    - Numbers (document numbers, IBAN, tax ID): found also with spaces inside ("DE89 3704 ...").
    - Dates (stored as ISO "1988-03-14"): found as "14.03.1988", "14 03 1988", "14/03/1988";
      the fake date is written in the same style.
    - Other values (address, phone, email, ...): found as they are, upper or lower case.
    """
    for f, real, fake in replacements:
        if len(real) < MIN_HIDE_LENGTH:
            continue
        if f in DATE_FIELDS and re.fullmatch(r"\d{4}-\d{2}-\d{2}", real) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", fake):
            y, m, d = real.split("-")
            fy, fm, fd = fake.split("-")
            pattern = rf"(?<!\d)0?{int(d)}\s*([./ -])\s*0?{int(m)}\s*\1\s*{y}(?!\d)"
            text = re.sub(pattern, lambda mt: f"{fd}{mt.group(1)}{fm}{mt.group(1)}{fy}", text)
        elif f in MATCH_FIELDS or f in {"iban", "steuer_id"}:
            pattern = r"(?<![A-Za-z0-9])" + r"\s*".join(map(re.escape, real)) + r"(?![A-Za-z0-9])"
            text = re.sub(pattern, fake, text, flags=re.IGNORECASE)
        else:
            text = re.sub(re.escape(real), lambda _: fake, text, flags=re.IGNORECASE)
    return text


def _ordered(fields) -> list[str]:
    rank = {f: i for i, f in enumerate(FIELD_ORDER)}
    return sorted(fields, key=lambda f: (rank.get(f, len(rank)), f))


def new_person_id(vault: Vault) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        pid = "P_" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not vault.person_id_exists(pid):
            return pid


def _split_given(given_names: str) -> tuple[str, str | None]:
    parts = given_names.split()
    return (parts[0] if parts else "", " ".join(parts[1:]) or None)


def save_document(vault: Vault, store: PeopleStore, gen: PseudonymGenerator, doc: ApprovedDocument,
                  person_id: str | None, tokenize_low_risk: bool = False) -> SaveResult:
    """Save one approved document.

    person_id: the person the user confirmed, or None to create a new person.
    tokenize_low_risk: config switch; True anonymizes ALL group B fields, ticked or not.
    """
    ident = doc.identity
    warnings: list[str] = []
    created = person_id is None
    tmp = None

    def fake(f: str, real: str) -> str:
        return fake_for(vault, gen.fake, person_id, f, real)

    with vault.transaction():
        if created:
            person_id = new_person_id(vault)
            first, middle = _split_given(ident.given_names)
            ps = gen.generate(ident.gender, has_middle_name=middle is not None,
                              has_birth_name=bool(ident.birth_name), is_taken=taken_checker(vault.all_names()))
            ts = now_utc()
            vault.add_person(PersonRow(
                person_id=person_id, real_first_name=first, real_middle_name=middle,
                real_last_name=ident.surname, real_birth_name=ident.birth_name,
                real_date_of_birth=normalize_date(ident.date_of_birth), match_key=ident.key,
                loose_key=loose_key(ident.given_names, ident.surname, ident.date_of_birth),
                pseudonym_first_name=ps.first_name, pseudonym_middle_name=ps.middle_name,
                pseudonym_last_name=ps.last_name, pseudonym_birth_name=ps.birth_name,
                created_at=ts, updated_at=ts))
            data = {
                "person_id": person_id,
                "pseudonym": {"first_name": ps.first_name, "middle_name": ps.middle_name,
                              "last_name": ps.last_name, "birth_name": ps.birth_name},
                "documents": [],
            }
        else:
            person = vault.get_person(person_id)
            if person is None:
                raise ValueError(f"Unknown person {person_id}")
            data = store.load(person_id) or {"person_id": person_id, "documents": []}
            if normalize_name(f"{ident.given_names} {ident.surname}") != normalize_name(
                    f"{person.real_first_name} {person.real_middle_name or ''} {person.real_last_name}"):
                warnings.append("The name on this document differs from the saved name. The saved name is kept.")
            vault.touch_person(person_id)

        # Rule 4: the exact same file(s) again. File names are never stored (they can contain
        # personal data), only the fingerprint of each file.
        message = exact_copy_message(vault, doc.file_hashes)
        if message:
            raise DuplicateDocumentError(message)

        # All values found on this document. Document numbers and the birth date come from
        # the identity; the birth date is anonymized like any group A field.
        all_fields = {"date_of_birth": ident.date_of_birth, **ident.document_numbers, **doc.fields}
        if ident.gender:
            all_fields.setdefault("gender", ident.gender)

        # Which fields get a fake value: group A always, group B when ticked (or by config).
        anonymize = set(ALWAYS_ANONYMIZED) | (
            set(OPTIONAL_ANONYMIZED) if tokenize_low_risk else (doc.anonymize & OPTIONAL_ANONYMIZED))

        # The REAL values, in their stored form (dates ISO, numbers without spaces).
        real: dict[str, str] = {}
        for f, v in all_fields.items():
            if not v:
                continue
            if f not in KNOWN_FIELDS:
                warnings.append(f"Unknown field {f!r} was not saved.")
                continue
            real[f] = normalize_value(f, v)

        # Option (b) for texts: group A values must not stay readable inside a clear text field.
        # Only group A (as approved): a ticked group B value such as place of birth "Köln" must not
        # change "Stadt Köln" in the issuing authority, which is a different fact.
        replacements = [(f, r, fake(f, r)) for f, r in real.items() if f in ALWAYS_ANONYMIZED]
        partly: list[str] = []
        for f, r in real.items():
            if f not in anonymize and f not in DATE_FIELDS:
                hidden = _hide_inside(r, replacements)
                if hidden != r:
                    real[f] = hidden
                    partly.append(f)

        # Option (B): keep only the values that are not saved yet for this person.
        known = _known_real(vault, person_id, data["documents"])
        new = {f: r for f, r in real.items() if compare_key(r) not in known.get(f, set())}
        original = _find_same_document(vault, person_id, data["documents"], doc.doc_type, real)
        if not new:
            where = f" (this document is already saved as {original['doc_id']})" if original else ""
            raise DuplicateDocumentError(f"Nothing new: every value on this document is already saved{where}. "
                                         "Nothing was saved.")
        for f in new:
            if f in SINGLE_FIELDS and known.get(f):
                warnings.append(f"{f}: this document shows a different value than an earlier document. "
                                "Both values are kept.")

        country_real = normalize_value("issuing_country", doc.issuing_country)
        country = fake("issuing_country", country_real) if "issuing_country" in anonymize else country_real
        doc_id = f"DOC_{len(data['documents']) + 1:04d}"
        entry: dict = {"doc_id": doc_id, "doc_type": doc.doc_type, "issuing_country": country}
        if original:
            # Rule 5: the same real document again, with new values: link the block to the first one.
            entry["updates"] = original["doc_id"]
            warnings.append(f"This document was already saved as {original['doc_id']}. "
                            f"Only the new or changed values were added (as {doc_id}).")
        fields = _ordered(new)
        for f in fields:
            entry[f] = fake(f, new[f]) if f in anonymize else new[f]
        entry[ANON_KEY] = _ordered([f for f in fields if f in anonymize]) + (
            ["issuing_country"] if "issuing_country" in anonymize else [])
        partly_here = _ordered([f for f in partly if f in new])
        if partly_here:
            entry[PARTLY_KEY] = partly_here
            warnings.append("Anonymized values were also found inside " + ", ".join(partly_here) +
                            " and replaced there by their fake values.")
        entry["file_sha256"] = doc.file_hashes

        for h in doc.file_hashes:
            if vault.find_document_file(h) is None:  # a file can be shared with an earlier upload
                vault.add_document_file(h, person_id, doc_id)
        data["documents"].append(entry)
        # Write the JSON to a temp file INSIDE the transaction: if this fails, the database rolls back.
        tmp = store.write_temp(person_id, data)

    # Database is committed. Now replace the JSON file in one step.
    store.commit_temp(person_id, tmp)
    return SaveResult(person_id, created, warnings)
