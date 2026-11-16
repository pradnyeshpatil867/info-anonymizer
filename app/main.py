"""The web backend (FastAPI), milestone 6.

Start it (only this computer can reach it, because of --host 127.0.0.1):
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Then the API description is at http://127.0.0.1:8000/docs

Endpoints:
    POST /api/analyse                       upload the files of ONE document -> proposal
    GET  /api/analyses/{id}/pages/{n}       page image for the review screen
    POST /api/analyses/{id}/approve         save the approved values
    POST /api/persons/search                search (spec section L)
    GET  /api/persons/{person_id}           one person's anonymized JSON

Privacy:
- Uploaded files and proposals stay in MEMORY only, never on disk, and are
  removed after Approve (or when newer analyses push them out).
- Searches use POST with a JSON body, so real names never appear in URLs or logs.
"""

import io
import sqlite3
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from app import pipeline
from app.checks.normalize import match_key, normalize_date
from app.config import PROJECT_ROOT, project_path, settings
from app.identity.matcher import loose_key
from app.identity.pseudonym import PseudonymGenerator
from app.identity.records import DuplicateDocumentError, exact_copy_message, file_hashes, save_document
from app.identity.fields import ALWAYS_ANONYMIZED, MATCH_FIELDS, OPTIONAL_ANONYMIZED, normalize_value
from app.identity.vault import Vault
from app.llm.schema import ExtractedFields
from app.models import (AnalysisOut, ApproveIn, ApproveOut, CandidateOut, CheckOut, DocTypeOut, FieldOut,
                        MatchOut, PersonOut, SearchIn)
from app.ocr.pdf import load_pages
from app.storage.people import PeopleStore

MAX_FILES_PER_DOCUMENT = 4          # e.g. front + back; more is probably a mistake
MAX_FILE_BYTES = 20 * 1024 * 1024   # 20 MB
MAX_KEPT_ANALYSES = 20              # older analyses are removed from memory
EDITABLE_FIELDS = set(ExtractedFields.model_fields) - {"doc_type"}

app = FastAPI(title="info-anonymizer", description="Local pseudonymization of documents issued in Germany.")


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """The browser must ask the server again for the page and its scripts every time.

    Without this, the browser may reuse an OLD copy of app.js from its cache after an update.
    Real bug (2026-11-16): the new "Anonymize" boxes did not appear, because the browser used
    the old app.js. "no-cache" = "you may keep a copy, but check with the server first".
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- shared objects ("dependencies") ----------
# FastAPI passes these into the endpoint functions. Tests replace them with
# temporary ones, so tests never touch the real data/ and people/ folders.

@lru_cache
def get_vault() -> Vault:
    # Requests run in different threads; every use of the vault happens under _db_lock.
    return Vault(project_path(settings()["vault_path"]), shared_between_threads=True)


@lru_cache
def get_store() -> PeopleStore:
    return PeopleStore(project_path(settings()["people_dir"]))


@lru_cache
def get_generator() -> PseudonymGenerator:
    s = settings()
    return PseudonymGenerator(s["pseudonym_locale"], s["pseudonym_include_middle_name"], s["pseudonym_match_gender"])


# ---------- in-memory storage of running reviews ----------

@dataclass
class _Analysis:
    proposal: pipeline.Proposal
    files: list[tuple[str, bytes]]
    page_png: list[bytes]


_analyses: "OrderedDict[str, _Analysis]" = OrderedDict()
# One analysis at a time: the OCR and LLM models are shared and use most of the memory.
_analyse_lock = threading.Lock()
# One database user at a time (SQLite connection shared between request threads).
_db_lock = threading.RLock()


def _remember(analysis: _Analysis) -> str:
    analysis_id = uuid.uuid4().hex
    _analyses[analysis_id] = analysis
    while len(_analyses) > MAX_KEPT_ANALYSES:
        _analyses.popitem(last=False)  # forget the oldest
    return analysis_id


def _get_analysis(analysis_id: str) -> _Analysis:
    a = _analyses.get(analysis_id)
    if a is None:
        raise HTTPException(404, "This analysis is not known (any more). Please analyse the document again.")
    return a


def _file_kind(data: bytes) -> str | None:
    """Look at the first bytes ("magic numbers"), not at the file name, which can be wrong."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pseudonym_text(vault: Vault, person_id: str) -> str:
    p = vault.get_person(person_id)
    if p is None:
        return "?"
    return " ".join(x for x in (p.pseudonym_first_name, p.pseudonym_middle_name, p.pseudonym_last_name) if x)


def _to_out(analysis_id: str, a: _Analysis, vault: Vault) -> AnalysisOut:
    p = a.proposal
    match = None
    if p.match:
        ids = p.match.candidates or ([p.match.person_id] if p.match.person_id else [])
        match = MatchOut(kind=p.match.kind, person_id=p.match.person_id, reason=p.match.reason,
                         candidates=[CandidateOut(person_id=i, pseudonym=_pseudonym_text(vault, i)) for i in ids])
    return AnalysisOut(
        analysis_id=analysis_id, file_names=p.file_names, pages=len(a.page_png), status=p.status.value,
        doc_type=DocTypeOut(doc_type=p.doc_type.doc_type, source=p.doc_type.source, reason=p.doc_type.reason),
        issuing_country=p.issuing_country, rejected=p.rejected,
        document_checks=[CheckOut(status=c.status.value, message=c.message) for c in p.document_checks],
        fields=[FieldOut(name=f.name, value=f.value, status=f.status.value, messages=f.messages)
                for f in p.fields.values()],
        match=match, timings={k: round(v, 1) for k, v in p.timings.items()},
        always_anonymized=sorted(ALWAYS_ANONYMIZED & EDITABLE_FIELDS),
        optional_anonymized=sorted(OPTIONAL_ANONYMIZED & EDITABLE_FIELDS),
    )


# ---------- endpoints ----------

@app.post("/api/analyse", response_model=AnalysisOut)
def analyse(files: list[UploadFile] = File(...), vault: Vault = Depends(get_vault)) -> AnalysisOut:
    """Analyse ONE document. Send all its files together (for example front and back)."""
    if not files or len(files) > MAX_FILES_PER_DOCUMENT:
        raise HTTPException(400, f"Send 1 to {MAX_FILES_PER_DOCUMENT} files for one document.")
    loaded: list[tuple[str, bytes]] = []
    for f in files:
        data = f.file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{f.filename}: file is larger than 20 MB.")
        if _file_kind(data) is None:
            raise HTTPException(415, f"{f.filename}: only JPG, PNG and PDF files are allowed.")
        loaded.append((f.filename or "file", data))

    # Rule 4: the exact same file(s) again -> refuse at once, before the slow analysis.
    with _db_lock:
        message = exact_copy_message(vault, file_hashes([data for _, data in loaded]))
    if message:
        raise HTTPException(409, message)

    # Plain "def" endpoint: FastAPI runs it in a worker thread, so the server stays responsive.
    with _analyse_lock:
        try:
            # Without the vault: the slow part must not block searches and saves.
            proposal = pipeline.analyse_document(loaded, None)
        except Exception as e:  # show a clear message, never a stack trace with document text
            raise HTTPException(500, f"The analysis failed: {type(e).__name__}. Please check that Ollama is running.")
    page_png = [_png(img) for _, data in loaded for img in load_pages(data)]
    a = _Analysis(proposal, loaded, page_png)
    with _db_lock:
        if not proposal.rejected:
            proposal.match = pipeline.match_person(vault, proposal.fields)
        return _to_out(_remember(a), a, vault)


@app.get("/api/analyses/{analysis_id}/pages/{page}")
def page_image(analysis_id: str, page: int) -> Response:
    a = _get_analysis(analysis_id)
    if not 1 <= page <= len(a.page_png):
        raise HTTPException(404, "No such page.")
    return Response(a.page_png[page - 1], media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/analyses/{analysis_id}/approve", response_model=ApproveOut)
def approve(analysis_id: str, body: ApproveIn, vault: Vault = Depends(get_vault),
            store: PeopleStore = Depends(get_store), gen: PseudonymGenerator = Depends(get_generator)) -> ApproveOut:
    a = _get_analysis(analysis_id)
    if a.proposal.rejected:
        raise HTTPException(400, a.proposal.rejected)

    unknown = set(body.values) - EDITABLE_FIELDS
    if unknown:
        raise HTTPException(422, f"Unknown fields: {sorted(unknown)}")
    not_optional = set(body.anonymize) - OPTIONAL_ANONYMIZED
    if not_optional:
        raise HTTPException(422, f"These fields cannot be chosen for anonymization: {sorted(not_optional)}")
    values = {k: v.strip() for k, v in body.values.items() if v and v.strip()}
    for required in ("surname", "given_names", "date_of_birth"):
        if required not in values:
            raise HTTPException(422, f"{required} is required.")
    for date_field in ("date_of_birth", "issue_date", "expiry_date"):
        if date_field in values:
            try:
                normalize_date(values[date_field])
            except ValueError:
                raise HTTPException(422, f"{date_field}: please enter a full date like 12.04.1990.")

    with _db_lock:
        if body.person_id is not None and vault.get_person(body.person_id) is None:
            raise HTTPException(404, "The chosen person does not exist.")
        if body.person_id is None and a.proposal.doc_type.doc_type == pipeline.FOREIGN_PASSPORT:
            raise HTTPException(422, "A foreign passport can only be saved for a person who already exists. "
                                     "Save the German document (for example the residence permit) first.")
        if body.person_id is None:
            # Creating a NEW person who already exists would make a duplicate.
            existing = vault.find_by_match_key(match_key(values["given_names"], values["surname"],
                                                         values["date_of_birth"]))
            if existing:
                raise HTTPException(409, f"A person with this name and birth date already exists "
                                         f"({existing.person_id}). Choose that person instead.")
        doc = pipeline.to_approved(a.proposal, values, [data for _, data in a.files], set(body.anonymize))
        try:
            result = save_document(vault, store, gen, doc, body.person_id, settings()["tokenize_low_risk_fields"])
        except DuplicateDocumentError as e:
            raise HTTPException(409, str(e))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A document number on this document already belongs to another person. "
                                     "Please check the number or the chosen person.")
    _analyses.pop(analysis_id, None)  # done: forget the uploaded files
    return ApproveOut(person_id=result.person_id, created=result.created, warnings=result.warnings)


@app.post("/api/persons/search", response_model=list[PersonOut])
def search(body: SearchIn, vault: Vault = Depends(get_vault), store: PeopleStore = Depends(get_store)):
    """Find persons (spec section L). Returns the anonymized JSON only, never real values."""
    given = [k for k in ("name", "pseudonym", "person_id", "document_number") if getattr(body, k)]
    if len(given) != 1:
        raise HTTPException(422, "Search by exactly one of: name (with date_of_birth), pseudonym, "
                                 "person_id, document_number.")
    with _db_lock:
        return _search(body, vault, store)


def _search(body: SearchIn, vault: Vault, store: PeopleStore) -> list[PersonOut]:
    found: dict[str, str] = {}  # person_id -> why it matched

    if body.name:
        if not body.date_of_birth:
            raise HTTPException(422, "Searching by real name also needs the date of birth.")
        # People type names in both orders: "Erika Mustermann" or "MUSTERMANN Erika"
        # (as printed on cards). So we try: last word = surname, and first word = surname.
        words = body.name.replace(",", " ").split()
        if len(words) < 2:
            raise HTTPException(422, "Please enter at least a given name and a surname.")
        orders = [(" ".join(words[:-1]), words[-1]), (" ".join(words[1:]), words[0])]
        try:
            for given_names, surname in orders:
                exact = vault.find_by_match_key(match_key(given_names, surname, body.date_of_birth))
                if exact:
                    found.setdefault(exact.person_id, "Same name and birth date.")
            for given_names, surname in orders:
                for p in vault.find_by_loose_key(loose_key(given_names, surname, body.date_of_birth)):
                    found.setdefault(p.person_id, "Same first given name, surname and birth date.")
        except ValueError:
            raise HTTPException(422, "Please enter a valid date of birth like 12.04.1990.")
    elif body.pseudonym:
        parts = body.pseudonym.split()
        if len(parts) < 2:
            raise HTTPException(422, "Please enter the fake first name and last name.")
        for p in vault.find_by_pseudonym(parts[0], parts[-1]):
            found[p.person_id] = "Same pseudonym."
    elif body.person_id:
        if vault.get_person(body.person_id.strip().upper()):
            found[body.person_id.strip().upper()] = "Same person_id."
    else:
        for f in sorted(MATCH_FIELDS):
            pid = vault.find_person_by_identifier(f, normalize_value(f, body.document_number))
            if pid:
                found[pid] = f"Same {f.replace('_', ' ')}."

    out = []
    for pid, reason in found.items():
        data = store.load(pid)
        if data is not None:
            out.append(PersonOut(person_id=pid, pseudonym=_pseudonym_text(vault, pid), match_reason=reason, data=data))
    return out


@app.get("/api/persons/{person_id}", response_model=PersonOut)
def get_person(person_id: str, vault: Vault = Depends(get_vault), store: PeopleStore = Depends(get_store)):
    try:
        data = store.load(person_id)
    except ValueError:
        raise HTTPException(404, "Unknown person.")
    if data is None:
        raise HTTPException(404, "Unknown person.")
    with _db_lock:
        pseudonym = _pseudonym_text(vault, person_id)
    return PersonOut(person_id=person_id, pseudonym=pseudonym, match_reason="", data=data)


# ---------- frontend (milestone 7) ----------

_WEB = PROJECT_ROOT / "app" / "web"
app.mount("/static", StaticFiles(directory=_WEB), name="static")


@app.get("/", include_in_schema=False)
def index():
    page = _WEB / "index.html"
    if not page.exists():
        return Response("The web pages are built in milestone 7. The API is ready: see /docs", media_type="text/plain")
    return FileResponse(page)
