"""Shapes of the data that the web API sends and receives (Pydantic models).

FastAPI uses these models to check incoming data and to describe the API.
Example: if the browser sends {"values": 5}, FastAPI refuses it with an
error, because `values` must be a dictionary of texts.
"""

from pydantic import BaseModel, Field


class CheckOut(BaseModel):
    status: str        # "ok" (green), "warn" (yellow) or "fail" (red)
    message: str


class FieldOut(BaseModel):
    name: str
    value: str | None
    status: str
    messages: list[str]


class DocTypeOut(BaseModel):
    doc_type: str
    source: str        # "mrz", "keyword", "llm" or "none"
    reason: str


class CandidateOut(BaseModel):
    """A possible match. Only the pseudonym is shown, never the stored real name."""
    person_id: str
    pseudonym: str


class MatchOut(BaseModel):
    kind: str          # "existing", "confirm", "conflict", "new" or "incomplete"
    person_id: str | None
    reason: str
    candidates: list[CandidateOut]


class AnalysisOut(BaseModel):
    analysis_id: str
    file_names: list[str]
    pages: int
    status: str
    doc_type: DocTypeOut
    issuing_country: str | None
    rejected: str | None
    document_checks: list[CheckOut]
    fields: list[FieldOut]
    match: MatchOut | None
    timings: dict[str, float]
    # Group A: always anonymized. The review screen shows their "Anonymize" box ticked and locked.
    always_anonymized: list[str]
    # Group B: shown in clear text unless the user ticks "Anonymize".
    optional_anonymized: list[str]


class ApproveIn(BaseModel):
    """What the review screen sends when the user clicks Approve."""
    values: dict[str, str | None] = Field(description="field name -> value, as approved (possibly edited)")
    person_id: str | None = Field(None, description="the confirmed existing person, or null for a NEW person")
    anonymize: list[str] = Field(default_factory=list,
                                 description="group B fields the user ticked: they get fake values too")


class ApproveOut(BaseModel):
    person_id: str
    created: bool
    warnings: list[str]


class SearchIn(BaseModel):
    """Exactly one way of searching (spec section L).

    Sent as POST with a JSON body, not as a URL, because URLs end up in
    server logs and the browser history, and a real name must not.
    """
    name: str | None = None            # real name, with date_of_birth
    date_of_birth: str | None = None
    pseudonym: str | None = None       # the fake name
    person_id: str | None = None
    document_number: str | None = None


class PersonOut(BaseModel):
    person_id: str
    pseudonym: str
    match_reason: str
    data: dict                         # the person JSON file: pseudonym and fake values, no real personal data
