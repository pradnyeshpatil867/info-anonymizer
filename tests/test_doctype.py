import json
from pathlib import Path

import pytest

from app.checks.doctype import detect_doc_type
from app.checks.mrz import parse_mrz

GERMAN_ID_MRZ = (
    "IDD<<T220001293<<<<<<<<<<<<<<<\n"
    "6408125<2010315D<<<<<<<<<<<<<4\n"
    "MUSTERMANN<<ERIKA<<<<<<<<<<<<<"
)
PASSPORT_MRZ = (
    "P<BDRMUSTERMANN<<ERIKA<<<<<<<<<<<<<<<<<<<<<<\n"
    "C1000000<7D<<6408125F1809244<<<<<<<<<<<<<<<8"
)
PERMIT_MRZ = (
    "ARD<<Y8A2B3C4D1<<<<<<<<<<<<<<<\n"
    "9211236F2806309IND<<<<<<<<<<<2\n"
    "KRISHNAN<<ANANYA<<<<<<<<<<<<<<"
)


@pytest.mark.parametrize("mrz, expected", [
    (GERMAN_ID_MRZ, "DE_ID_CARD"),
    (PASSPORT_MRZ, "DE_PASSPORT"),
    (PERMIT_MRZ, "DE_RESIDENCE_PERMIT"),
])
def test_mrz_decides_first(mrz, expected):
    r = detect_doc_type("any text", parse_mrz(mrz))
    assert r.doc_type == expected and r.source == "mrz"


def test_broken_mrz_is_not_trusted():
    broken = parse_mrz(GERMAN_ID_MRZ.replace("6408125", "6408135"))
    assert detect_doc_type("FÜHRERSCHEIN", broken).doc_type == "DE_DRIVING_LICENCE"


@pytest.mark.parametrize("text, expected", [
    ("BUNDESREPUBLIKDEUTSCHLAND\nPERSONALAUSWEIS\nIDENTITY CARD", "DE_ID_CARD"),
    ("PASSPORT\nREISEPASS-PASSEPORT", "DE_PASSPORT"),
    ("AUFENTHALTSTITEL / RESIDENCE PERMIT", "DE_RESIDENCE_PERMIT"),
    ("FÜHRERSCHEIN BUNDESREPUBLIK DEUTSCHLAND", "DE_DRIVING_LICENCE"),
    ("FUHRERSCHEIN", "DE_DRIVING_LICENCE"),                     # dots missing
    ("Meldebescheinigung\nEinfache Meldebescheinigung", "DE_MELDEBESCHEINIGUNG"),
    ("Kontoeröffnung – Angaben zur Person (KYC)", "FORM_KYC"),
])
def test_keywords(text, expected):
    assert detect_doc_type(text).doc_type == expected


def test_form_that_mentions_a_card_is_still_a_form():
    text = "Kontoeröffnung – Angaben zur Person (KYC)\nAufenthaltstitel-Nr.\nPersonalausweis-Nr."
    assert detect_doc_type(text).doc_type == "FORM_KYC"


def test_llm_is_only_the_last_resort():
    r = detect_doc_type("no known words here", None, llm_doc_type="DE_PASSPORT")
    assert r.doc_type == "DE_PASSPORT" and r.source == "llm"


def test_unknown():
    r = detect_doc_type("no known words here", None, llm_doc_type="UNKNOWN")
    assert r.doc_type == "UNKNOWN" and r.source == "none"


# --- On real OCR output of all 8 test documents ---

ROOT = Path(__file__).resolve().parent.parent
TRUTH_FILES = [ROOT / "samples" / "synthetic" / "truth.json", ROOT / "samples" / "specimens" / "truth.json"]


def _cases():
    out = []
    for f in TRUTH_FILES:
        if f.exists():
            for key, truth in json.loads(f.read_text(encoding="utf-8")).items():
                out.append((f.parent, key, truth["doc_type"]))
    return out


@pytest.mark.slow
@pytest.mark.skipif(not _cases(), reason="test documents not created")
@pytest.mark.parametrize("folder, key, expected", _cases())
def test_rules_on_real_ocr(folder, key, expected):
    from app.checks.mrz import choose_mrz, extract_mrz_lines
    from app.ocr import paddle
    from app.ocr.pdf import load_pages, upscale_if_small

    texts = [paddle.read(upscale_if_small(load_pages(folder / n.strip())[0], 1600)).text for n in key.split("+")]
    text = "\n".join(texts)
    mrz, _ = choose_mrz({"PaddleOCR": "\n".join(extract_mrz_lines(text)) or None})
    assert detect_doc_type(text, mrz).doc_type == expected
