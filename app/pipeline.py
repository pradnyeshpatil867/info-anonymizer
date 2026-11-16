"""The pipeline: one document in, one PROPOSAL out (spec sections A, E, G, H).

Steps:
1. load the pages            5. LLM field extraction
2. OCR with both engines     6. checks for every field (green / yellow / red)
3. choose the MRZ            7. matching: "is this a known person?"
4. document type (rules)     8. the proposal for the review screen

NOTHING is saved here. Saving happens only after the user clicks Approve
(app/identity/records.py). Real values live only in memory and are never logged.

Try it:
    uv run python -m app.pipeline samples/specimens/de_id_card_2010_front.jpg samples/specimens/de_id_card_2010_back.jpg
"""

import re
import sys
import time
from dataclasses import dataclass, field

from app.checks import compare, validate
from app.checks.doctype import DocTypeResult, detect_doc_type
from app.checks.mrz import MrzResult, choose_mrz, extract_mrz_lines, mrz_date_to_iso
from app.checks.normalize import normalize_date
from app.checks.result import CheckResult, Status, worst
from app.config import settings
from app.identity.matcher import Identity, MatchResult, find_person
from app.identity.records import ApprovedDocument, file_hashes
from app.identity.fields import MATCH_FIELDS, normalize_value
from app.identity.vault import Vault
from app.llm import extractor
from app.llm.schema import ExtractedFields
from app.ocr import paddle, tesseract
from app.ocr.pdf import load_pages, upscale_if_small

# Documents that always have an MRZ (on the back of cards).
# A passport NOT issued in Germany (user decisions 2026-11-16): accepted like a German passport,
# for an existing or a new person. Other foreign documents are skipped.
FOREIGN_PASSPORT = "FOREIGN_PASSPORT"
MRZ_DOC_TYPES = {"DE_ID_CARD", "DE_PASSPORT", "DE_RESIDENCE_PERMIT", FOREIGN_PASSPORT}
# Which field holds the document number, per document type.
NUMBER_FIELD = {"DE_ID_CARD": "id_card_number", "DE_PASSPORT": "passport_number", FOREIGN_PASSPORT: "passport_number",
                "DE_RESIDENCE_PERMIT": "residence_permit_number", "DE_DRIVING_LICENCE": "driving_licence_number"}
# Fields that each official document type can have. A field outside this list is probably
# a mix-up by the LLM (real case: the watermark word "MUSTER" as birth name on a driving
# licence). Forms are not limited: they can contain anything.
_PERSON = {"surname", "given_names", "date_of_birth", "place_of_birth"}
ALLOWED_FIELDS = {
    "DE_ID_CARD": _PERSON | {"birth_name", "nationality", "address", "height", "eye_colour", "id_card_number",
                             "issue_date", "expiry_date", "issuing_authority", "gender"},
    "DE_PASSPORT": _PERSON | {"birth_name", "nationality", "gender", "height", "eye_colour", "passport_number",
                              "issue_date", "expiry_date", "issuing_authority", "address"},
    FOREIGN_PASSPORT: _PERSON | {"birth_name", "nationality", "gender", "height", "eye_colour", "passport_number",
                                 "issue_date", "expiry_date", "issuing_authority", "address"},
    # The back side of the residence permit (eAT) shows height and eye colour (user report 2026-11-16).
    "DE_RESIDENCE_PERMIT": _PERSON | {"nationality", "gender", "address", "residence_permit_number", "issue_date",
                                      "expiry_date", "issuing_authority", "permit_type", "remarks",
                                      "height", "eye_colour"},
    "DE_DRIVING_LICENCE": _PERSON | {"driving_licence_number", "issue_date", "expiry_date", "issuing_authority"},
    "DE_MELDEBESCHEINIGUNG": _PERSON | {"birth_name", "nationality", "gender", "address", "issue_date",
                                        "issuing_authority"},
}
DATE_FIELDS = ("date_of_birth", "issue_date", "expiry_date")
NAME_FIELDS = ("surname", "given_names", "birth_name")
_TWO_DIGIT_YEAR = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2}$")


@dataclass
class FieldProposal:
    name: str
    value: str | None
    status: Status = Status.OK
    messages: list[str] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        """Keep the worst status and collect every message."""
        self.messages.append(result.message)
        self.status = worst([CheckResult("", self.status, ""), result])


@dataclass
class Proposal:
    file_names: list[str]
    doc_type: DocTypeResult
    issuing_country: str | None
    rejected: str | None                       # set when the document must be skipped
    fields: dict[str, FieldProposal]
    document_checks: list[CheckResult]         # checks about the whole document
    mrz: MrzResult | None
    match: MatchResult | None
    timings: dict[str, float]
    ocr_text: str = ""                         # kept in memory for the review screen only

    @property
    def status(self) -> Status:
        results = list(self.document_checks)
        results += [CheckResult(f.name, f.status, "") for f in self.fields.values()]
        if self.rejected:
            return Status.FAIL
        return worst(results)


# ---------- small helpers ----------

def _squash(text: str) -> str:
    """Compare text without spaces, signs or case: OCR often joins words."""
    return re.sub(r"[^0-9A-ZÄÖÜß]", "", text.upper())


def _found_in_text(value: str, squashed_text: str) -> bool:
    """True if every word of the value is in the OCR text.

    Word by word, because values like an address are often printed on two lines
    with other text in between ("51147 KÖLN" ... "HEIDESTRASSE 17").
    """
    words = [_squash(w) for w in value.split()]
    return all(w in squashed_text for w in words if w)


def _is_valid_date(value: str) -> bool:
    try:
        normalize_date(value)
        return True
    except ValueError:
        return False


def _nationality_code(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    return "DEU" if v in {"D", "DEU", "DEUTSCH", "GERMAN", "DEUTSCHLAND"} else v


def _umlaut_hint(value: str, other_text: str) -> str | None:
    """If OCR text elsewhere has the same word WITH dots, return the dotted spelling.

    Example: the LLM says "KOLN", Tesseract read "KÖLN" -> return "KÖLN".
    OCR often drops dots but rarely adds them (milestone 1, compare.py).
    """
    words = {w.strip(".,;:()") for w in other_text.split()}
    changed = False
    out = []
    for w in value.split():
        better = next((o for o in words if compare.strip_dots(o) == w.upper() and o.upper() != w.upper()), None)
        if better and w.upper() == compare.strip_dots(w):
            out.append(better if w.isupper() else better.capitalize())
            changed = True
        else:
            out.append(w)
    return " ".join(out) if changed else None


# ---------- the pipeline ----------

def analyse_document(files: list[tuple[str, bytes]], vault: Vault | None = None) -> Proposal:
    """Analyse ONE document. `files` = (file name, file bytes) for every page or side.

    Example: an ID card is two files, front and back.
    """
    timings: dict[str, float] = {}
    t = time.perf_counter()
    min_width = settings()["ocr_min_width"]
    pages = [upscale_if_small(p, min_width) for _, data in files for p in load_pages(data)]
    timings["load"] = time.perf_counter() - t

    # 2. OCR with both engines.
    t = time.perf_counter()
    paddle_results = [paddle.read(p) for p in pages]
    tess_results = [tesseract.read(p) for p in pages]
    paddle_text = "\n".join(r.text for r in paddle_results)
    tess_text = "\n".join(r.text for r in tess_results)
    timings["ocr"] = time.perf_counter() - t

    # 3. MRZ from both engines; only readings with correct check digits can win.
    t = time.perf_counter()
    tess_mrz = next((m for m in (tesseract.read_mrz(p) for p in pages) if m), None)
    mrz, mrz_check = choose_mrz({"PaddleOCR": "\n".join(extract_mrz_lines(paddle_text)) or None,
                                 "Tesseract": tess_mrz})
    timings["mrz"] = time.perf_counter() - t

    # 5. LLM (before step 4, because the document type rules use its guess as a last resort).
    t = time.perf_counter()
    llm = extractor.extract_fields(pages, extractor.join_pages([r.text for r in paddle_results]))
    timings["llm"] = time.perf_counter() - t

    # 4. Document type by rules.
    doc_type = detect_doc_type(paddle_text, mrz if mrz and mrz.valid else None, llm.fields.doc_type)

    document_checks: list[CheckResult] = [CheckResult("doc_type", Status.OK if doc_type.source in ("mrz", "keyword")
                                                      else Status.WARN, f"Document type: {doc_type.reason}")]

    # Issuing country (spec A.7).
    rejected = None
    issuing_country = None
    if mrz and mrz.valid:
        if mrz.issued_in_germany:
            issuing_country = "DEU"
        elif mrz.document_code.startswith("P"):
            # A foreign passport: accepted like a German passport (existing or new person).
            issuing_country = mrz.issuing_state
            doc_type = DocTypeResult(FOREIGN_PASSPORT, "mrz",
                                     f"Passport issued by {mrz.issuing_state!r} (MRZ document code {mrz.document_code!r}).")
            document_checks = [CheckResult("doc_type", Status.OK, f"Document type: {doc_type.reason}")]
        else:
            # Not a passport and not from Germany: skipped. The type label must not say "DE_...".
            doc_type = DocTypeResult("FOREIGN_DOCUMENT", "mrz",
                                     f"Issued by {mrz.issuing_state!r} (MRZ document code {mrz.document_code!r}).")
            document_checks = [CheckResult("doc_type", Status.OK, f"Document type: {doc_type.reason}")]
            rejected = (f"Not issued in Germany (MRZ issuing state {mrz.issuing_state!r}). Of the documents "
                        "not issued in Germany, only passports are accepted. This document is skipped.")
    elif doc_type.doc_type.startswith("DE_") and "DEUTSCHLAND" in _squash(paddle_text):
        issuing_country = "DEU"
        document_checks.append(CheckResult("issuing_country", Status.OK, "The document says DEUTSCHLAND."))
    else:
        issuing_country = "DEU"
        document_checks.append(CheckResult("issuing_country", Status.WARN,
                                           "Issuing country not printed. Assumed Germany – please confirm."))

    # MRZ only where this document type has one (milestone 3 note).
    if doc_type.doc_type in MRZ_DOC_TYPES:
        if mrz is None:
            document_checks.append(CheckResult("mrz", Status.WARN,
                                               "No MRZ found. If this is only the front side, add the back side."))
        else:
            document_checks.append(mrz_check)

    # 6. Field checks.
    fields = _check_fields(llm.fields, doc_type.doc_type, mrz if mrz and mrz.valid else None,
                           paddle_text, tess_text)

    # 7. Matching (read-only lookup in the vault).
    match = None
    if vault is not None and not rejected:
        match = match_person(vault, fields)

    return Proposal(
        file_names=[name for name, _ in files], doc_type=doc_type, issuing_country=issuing_country,
        rejected=rejected, fields=fields, document_checks=document_checks, mrz=mrz, match=match,
        timings=timings, ocr_text=paddle_text,
    )


def _check_fields(extracted: ExtractedFields, doc_type: str, mrz: MrzResult | None,
                  paddle_text: str, tess_text: str) -> dict[str, FieldProposal]:
    values = extracted.model_dump(exclude={"doc_type"})
    # Text with "<<" is MRZ text. The LLM sometimes copies it into a field; it is never a real value.
    fields = {name: FieldProposal(name, value) for name, value in values.items() if value and "<<" not in value}
    all_text = _squash(paddle_text + "\n" + tess_text)

    # Values from the MRZ are proven by check digits, so they fill or override some fields.
    from_mrz: set[str] = set()
    if mrz:
        sex = {"F": "F", "M": "M"}.get(mrz.sex)
        if sex:
            fields["gender"] = FieldProposal("gender", sex, Status.OK, ["Taken from the MRZ."])
            from_mrz.add("gender")
        fields["nationality"] = FieldProposal("nationality", _nationality_code(mrz.nationality), Status.OK,
                                              ["Taken from the MRZ."])
        from_mrz.add("nationality")
    if "nationality" in fields and "nationality" not in from_mrz:
        fields["nationality"].value = _nationality_code(fields["nationality"].value)

    allowed = ALLOWED_FIELDS.get(doc_type)
    for name, f in fields.items():
        if name in from_mrz:
            continue

        if allowed is not None and name not in allowed:
            f.add(CheckResult(name, Status.WARN,
                              "This field is not normally on this document type. Probably a mix-up – please check."))

        # One letter like "F" is found in any text, so that check proves nothing for gender.
        # German ID cards do not print the sex at all, so an LLM gender is often a guess.
        if name == "gender":
            f.add(CheckResult(name, Status.WARN, "Sex is not proven by the MRZ. Please confirm or clear it."))
            continue

        # Every LLM value must be visible in the OCR text. Otherwise the model may have invented it.
        if not _found_in_text(f.value, all_text):
            f.add(CheckResult(name, Status.WARN, "This value was not found in the OCR text. Please compare with the image."))

        # Umlaut dots: another OCR reading may have the dots that this value lacks.
        if name in NAME_FIELDS + ("place_of_birth", "address", "issuing_authority"):
            hint = _umlaut_hint(f.value, paddle_text + "\n" + tess_text)
            if hint:
                f.add(CheckResult(name, Status.WARN, f"The OCR also read {hint!r} (with umlaut dots). Proposed."))
                f.value = hint

        if name in DATE_FIELDS:
            if _TWO_DIGIT_YEAR.match(f.value):
                f.add(CheckResult(name, Status.WARN, "Two-digit year. Please enter the full year (for example 1964)."))
            else:
                f.add(validate.check_date(f.value, name))
        elif name == "iban":
            f.add(validate.check_iban(f.value))
        elif name == "steuer_id":
            f.add(validate.check_steuer_id(f.value))

    # Printed values vs. MRZ.
    if mrz:
        for name, mrz_value in (("surname", mrz.surname), ("given_names", mrz.given_names)):
            if name in fields:
                result, best = compare.check_name_against_mrz(fields[name].value, mrz_value, name)
                fields[name].add(result)
                if best and result.status is Status.WARN:
                    fields[name].value = best
            elif mrz_value:
                fields[name] = FieldProposal(name, mrz_value, Status.WARN,
                                             ["Not found in the printed text; taken from the MRZ (no umlauts there)."])
        _compare_date(fields, "date_of_birth", mrz_date_to_iso(mrz.birth_date, "birth"))
        _compare_date(fields, "expiry_date", mrz_date_to_iso(mrz.expiry_date, "expiry"))
        number_field = NUMBER_FIELD.get(doc_type)
        if number_field:
            f = fields.get(number_field)
            if f is None:
                fields[number_field] = FieldProposal(number_field, mrz.document_number, Status.OK,
                                                     ["Taken from the MRZ (check digit correct)."])
            elif normalize_value(number_field, f.value) == mrz.document_number:
                f.add(CheckResult(number_field, Status.OK, "Matches the MRZ (check digit correct)."))
            else:
                f.add(CheckResult(number_field, Status.FAIL,
                                  f"Does not match the MRZ number {mrz.document_number}. Please check."))

    # Compare issue and expiry date only when both are clear dates.
    # An unclear date (for example a two-digit year) is already reported on its own field.
    if ("issue_date" in fields and "expiry_date" in fields
            and _is_valid_date(fields["issue_date"].value) and _is_valid_date(fields["expiry_date"].value)):
        r = validate.check_issue_before_expiry(fields["issue_date"].value, fields["expiry_date"].value)
        if r.status is Status.FAIL:
            fields["expiry_date"].add(r)
    return fields


def _compare_date(fields: dict[str, FieldProposal], name: str, mrz_iso: str) -> None:
    f = fields.get(name)
    if f is None:
        fields[name] = FieldProposal(name, ".".join(reversed(mrz_iso.split("-"))), Status.OK, ["Taken from the MRZ."])
        return
    try:
        printed = normalize_date(f.value)
    except ValueError:
        return  # already marked as unclear
    if printed == mrz_iso:
        f.add(CheckResult(name, Status.OK, "Matches the MRZ."))
    else:
        f.add(CheckResult(name, Status.FAIL, f"Does not match the MRZ date ({mrz_iso}). Please check."))


def _identity(fields: dict[str, FieldProposal]) -> Identity | None:
    def val(n):
        return fields[n].value if n in fields else None
    if not (val("surname") and val("given_names") and val("date_of_birth")):
        return None
    try:
        normalize_date(val("date_of_birth"))
    except ValueError:
        return None
    numbers = {n: val(n) for n in MATCH_FIELDS if val(n)}
    return Identity(val("given_names"), val("surname"), val("date_of_birth"), val("birth_name"),
                    val("gender"), numbers)


def match_person(vault: Vault, fields: dict[str, FieldProposal]) -> MatchResult:
    """Read-only lookup: is the person on this document already known?"""
    ident = _identity(fields)
    if ident is None:
        return MatchResult("incomplete", None, "Name or birth date is missing or unclear. Correct it to find the person.")
    return find_person(vault, ident)


def to_approved(proposal: Proposal, values: dict[str, str | None], files: list[bytes] = (),
                anonymize: set[str] = frozenset()) -> ApprovedDocument:
    """Build the document to save from the values the user approved (possibly edited)."""
    v = {k: (x.strip() if isinstance(x, str) else x) for k, x in values.items() if x}
    numbers = {n: v.pop(n) for n in list(v) if n in MATCH_FIELDS}
    ident = Identity(v.pop("given_names"), v.pop("surname"), v.pop("date_of_birth"),
                     v.pop("birth_name", None), v.pop("gender", None), numbers)
    return ApprovedDocument(proposal.doc_type.doc_type, proposal.issuing_country or "DEU", ident, v,
                            file_hashes(list(files)), set(anonymize))


# ---------- command line demo ----------

def _print(p: Proposal) -> None:
    mark = {Status.OK: "green ", Status.WARN: "YELLOW", Status.FAIL: "RED   "}
    print(f"\nFiles: {p.file_names}")
    print(f"Overall: {mark[p.status]}   type: {p.doc_type.doc_type} ({p.doc_type.source})   country: {p.issuing_country}")
    if p.rejected:
        print(f"REJECTED: {p.rejected}")
    for c in p.document_checks:
        print(f"  [{mark[c.status]}] {c.message}")
    for f in p.fields.values():
        print(f"  {mark[f.status]}  {f.name:24} {f.value!r}")
        for m in f.messages:
            if f.status is not Status.OK:
                print(f"            - {m}")
    if p.match:
        print(f"Match: {p.match.kind} {p.match.person_id or ''} – {p.match.reason}")
    print("Timings: " + ", ".join(f"{k} {v:.1f}s" for k, v in p.timings.items()))


if __name__ == "__main__":
    from pathlib import Path
    files = [(Path(a).name, Path(a).read_bytes()) for a in sys.argv[1:]]
    _print(analyse_document(files))
