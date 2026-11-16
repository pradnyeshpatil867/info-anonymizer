"""Decide the document type with fixed rules (user decision, milestone 4).

Why rules and not the LLM: in the model test, every LLM got the document
type wrong on 2 to 7 of 8 documents. Rules are simple and predictable.

Order of the rules:
1. MRZ document code (most reliable): ID = ID card, P = passport, A = residence permit.
2. Form key words: forms often MENTION other documents ("Personalausweis-Nr."),
   so forms are checked before the card key words.
3. Card key words: "AUFENTHALTSTITEL", "PERSONALAUSWEIS", "REISEPASS", "FÜHRERSCHEIN".
4. The LLM's answer, only if no rule matched.
"""

from dataclasses import dataclass

from app.checks.compare import to_mrz_style
from app.checks.mrz import MrzResult

# (key words, document type). The first matching entry wins, so the order matters.
# Key words are written without spaces and with umlauts as AE/OE/UE,
# because OCR often joins words ("BUNDESREPUBLIKDEUTSCHLAND") or drops dots.
FORM_RULES = [
    (("MELDEBESCHEINIGUNG",), "DE_MELDEBESCHEINIGUNG"),
    (("KYC", "KONTOEROEFFNUNG", "KONTOEROFFNUNG"), "FORM_KYC"),
]
CARD_RULES = [
    (("AUFENTHALTSTITEL", "RESIDENCEPERMIT"), "DE_RESIDENCE_PERMIT"),
    (("PERSONALAUSWEIS", "IDENTITYCARD"), "DE_ID_CARD"),
    (("REISEPASS", "PASSPORT"), "DE_PASSPORT"),
    (("FUEHRERSCHEIN", "FUHRERSCHEIN", "DRIVINGLICEN"), "DE_DRIVING_LICENCE"),
]


@dataclass(frozen=True)
class DocTypeResult:
    doc_type: str   # for example "DE_ID_CARD", or "UNKNOWN"
    source: str     # "mrz", "keyword", "llm" or "none"
    reason: str     # plain-English text for the review screen


def _squash(text: str) -> str:
    """Uppercase, umlauts as AE/OE/UE, no spaces or signs: 'Kontoeröffnung' -> 'KONTOEROEFFNUNG'."""
    return to_mrz_style(text).replace(" ", "")


def from_mrz(mrz: MrzResult | None) -> str | None:
    if mrz is None or not mrz.valid:
        return None  # an MRZ with failed check digits is not trusted
    code = mrz.document_code
    if code == "ID":
        return "DE_ID_CARD"
    if code.startswith("P"):
        return "DE_PASSPORT"
    if code.startswith("A"):
        return "DE_RESIDENCE_PERMIT"
    return None


def detect_doc_type(ocr_text: str, mrz: MrzResult | None = None, llm_doc_type: str | None = None) -> DocTypeResult:
    by_mrz = from_mrz(mrz)
    if by_mrz:
        return DocTypeResult(by_mrz, "mrz", f"MRZ document code {mrz.document_code!r}.")

    text = _squash(ocr_text)
    for rules in (FORM_RULES, CARD_RULES):
        for words, doc_type in rules:
            for word in words:
                if word in text:
                    return DocTypeResult(doc_type, "keyword", f"The document contains the word {word!r}.")

    if llm_doc_type and llm_doc_type != "UNKNOWN":
        return DocTypeResult(llm_doc_type, "llm", "No rule matched; the LLM's guess is used. Please check.")
    return DocTypeResult("UNKNOWN", "none", "The document type could not be found. Please choose it.")
