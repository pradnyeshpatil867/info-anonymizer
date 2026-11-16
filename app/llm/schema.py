"""The fields the LLM must fill in (spec section B).

The LLM gets the JSON schema of `ExtractedFields`, so Ollama forces its answer
into exactly this shape. The `description` texts are part of that schema:
they are short hints for the model, so keep them clear.

Every field may be None, because most documents show only some fields.
Field names match KNOWN_FIELDS in app/identity/fields.py, so the result can
go straight into the anonymization step.
"""

from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal[
    "DE_ID_CARD",
    "DE_PASSPORT",
    "DE_RESIDENCE_PERMIT",
    "DE_DRIVING_LICENCE",
    "DE_MELDEBESCHEINIGUNG",
    "FORM_KYC",
    "FORM_OTHER",
    "UNKNOWN",
]

_DATE = "exactly as printed, e.g. 12.04.1990"


class ExtractedFields(BaseModel):
    doc_type: DocType = Field(
        "UNKNOWN",
        description="type of document: ID card, passport, residence permit (Aufenthaltstitel), "
        "driving licence, Meldebescheinigung, KYC form, other form, or UNKNOWN",
    )

    # Personal fields
    surname: str | None = Field(None, description="family name (Name / Nachname / surname) as printed")
    given_names: str | None = Field(None, description="all first names (Vornamen / given names) as printed")
    birth_name: str | None = Field(None, description="Geburtsname / birth name, often after 'geb.'")
    date_of_birth: str | None = Field(None, description=f"Geburtsdatum / date of birth, {_DATE}")
    place_of_birth: str | None = Field(None, description="Geburtsort / place of birth")
    nationality: str | None = Field(None, description="Staatsangehörigkeit / nationality as printed")
    gender: str | None = Field(None, description="Geschlecht / sex as printed, e.g. F, M, W")
    address: str | None = Field(None, description="Anschrift / address: street, house number, postcode and city")
    phone: str | None = Field(None, description="telephone number as printed")
    email: str | None = Field(None, description="e-mail address as printed")
    iban: str | None = Field(None, description="IBAN bank account number as printed")
    steuer_id: str | None = Field(None, description="Steuer-ID / Steueridentifikationsnummer (11 digits)")
    employer: str | None = Field(None, description="Arbeitgeber / employer name")
    height: str | None = Field(None, description="Größe / height as printed, e.g. 160 cm")
    eye_colour: str | None = Field(None, description="Augenfarbe / colour of eyes as printed")

    # Document fields
    id_card_number: str | None = Field(None, description="ID card (Personalausweis) document number")
    passport_number: str | None = Field(None, description="passport (Reisepass) number")
    residence_permit_number: str | None = Field(None, description="residence permit (Aufenthaltstitel) card number")
    driving_licence_number: str | None = Field(None, description="driving licence (Führerschein) number, field 5.")
    issue_date: str | None = Field(None, description=f"Ausstellungsdatum / date of issue, {_DATE}")
    expiry_date: str | None = Field(None, description=f"gültig bis / date of expiry, {_DATE}")
    issuing_authority: str | None = Field(None, description="Behörde / issuing authority as printed")
    permit_type: str | None = Field(None, description="Art des Titels / type of residence permit")
    remarks: str | None = Field(None, description="Anmerkungen / remarks on a residence permit")
    # No issuing_country here (user decision, milestone 4): the app knows it from the
    # MRZ (D / BDR) or the document type, so the LLM does not have to guess it.


# All field names except doc_type. Handy for loops and tests.
TEXT_FIELDS = [name for name in ExtractedFields.model_fields if name != "doc_type"]


def llm_json_schema() -> dict:
    """The JSON schema we send to Ollama: like ExtractedFields, but every key is required.

    Why: Pydantic marks fields with a default as optional. Then Ollama lets the
    model write the keys in any order or skip them, and small models got lost
    and repeated text forever (test with qwen2.5:1.5b). When every key is
    required, the model walks through the fields in a fixed order and writes
    null for missing ones. Values may still be null, so nothing is forced.
    """
    schema = ExtractedFields.model_json_schema()
    schema["required"] = list(schema["properties"])
    return schema
