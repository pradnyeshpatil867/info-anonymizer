"""Pipeline tests.

Fast tests: the OCR engines and the LLM are replaced by fakes ("monkeypatch"),
so we test only the pipeline's own logic. Monkeypatch = pytest's way to swap
a function for the duration of one test.

Slow test: the real engines on the official ID card specimen.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from app import pipeline
from app.checks.result import Status
from app.identity.records import save_document
from app.llm.extractor import ExtractionResult
from app.llm.schema import ExtractedFields
from app.ocr.result import OcrLine, OcrResult

GERMAN_ID_MRZ = "IDD<<T220001293<<<<<<<<<<<<<<<\n6408125<2010315D<<<<<<<<<<<<<4\nMUSTERMANN<<ERIKA<<<<<<<<<<<<<"
FRONT_TEXT = ("BUNDESREPUBLIK DEUTSCHLAND\nPERSONALAUSWEIS\nT22000129\nMUSTERMANN\nGEB. GABLER\nERIKA\n"
              "12.08.1964 DEUTSCH\nBERLIN\n31.10.2020")
BACK_TEXT = "GRÜN\n51147 KÖLN\nHEIDESTRASSE 17\n160 cm\n01.11.10\nSTADT KÖLN\n" + GERMAN_ID_MRZ


def png(scan: int = 0) -> bytes:
    """A small white image. A different `scan` number gives different bytes (a new photo)."""
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 60), "white")
    img.putpixel((0, 0), (scan % 256, 0, 0))
    img.save(buf, format="PNG")
    return buf.getvalue()


def ocr(text: str, engine: str) -> OcrResult:
    return OcrResult(engine, [OcrLine(t, 1.0, (0, i * 10, 10, i * 10 + 5)) for i, t in enumerate(text.splitlines())])


@pytest.fixture
def fake_engines(monkeypatch):
    """Install fakes. Tests set state['paddle'], state['tess'], state['tess_mrz'], state['llm']."""
    state = {"paddle": [], "tess": [], "tess_mrz": None, "llm": {}}
    pages = iter(range(100))

    def paddle_read(img):
        return ocr(state["paddle"][next(pages) % len(state["paddle"])], "paddleocr")

    tess_pages = iter(range(100))

    def tess_read(img):
        return ocr(state["tess"][next(tess_pages) % len(state["tess"])], "tesseract")

    monkeypatch.setattr(pipeline.paddle, "read", paddle_read)
    monkeypatch.setattr(pipeline.tesseract, "read", tess_read)
    monkeypatch.setattr(pipeline.tesseract, "read_mrz", lambda img: state["tess_mrz"])
    monkeypatch.setattr(pipeline.extractor, "extract_fields",
                        lambda images, text, **kw: ExtractionResult(ExtractedFields(**state["llm"]), "fake", True, 0.0, ""))
    return state


def id_card_state(state, **llm_overrides):
    state["paddle"] = [FRONT_TEXT, BACK_TEXT]
    state["tess"] = [FRONT_TEXT, BACK_TEXT]
    state["llm"] = {"doc_type": "FORM_OTHER", "surname": "MUSTERMANN", "given_names": "ERIKA",
                    "birth_name": "GABLER", "date_of_birth": "12.08.1964", "place_of_birth": "BERLIN",
                    "nationality": "DEUTSCH", "id_card_number": "T22000129", "expiry_date": "31.10.2020",
                    "address": "51147 KÖLN HEIDESTRASSE 17", "issuing_authority": "STADT KÖLN", **llm_overrides}


def run(state, vault=None):
    files = [(f"page{i}.png", png()) for i in range(len(state["paddle"]))]
    return pipeline.analyse_document(files, vault)


def test_good_id_card_is_green(fake_engines):
    id_card_state(fake_engines)
    p = run(fake_engines)
    assert p.doc_type.doc_type == "DE_ID_CARD" and p.doc_type.source == "mrz"  # LLM said FORM_OTHER: ignored
    assert p.issuing_country == "DEU" and p.rejected is None
    assert p.fields["surname"].status is Status.OK
    assert p.fields["date_of_birth"].status is Status.OK
    assert p.fields["id_card_number"].status is Status.OK
    assert p.fields["nationality"].value == "DEU"
    assert p.status is Status.OK, [(f.name, f.messages) for f in p.fields.values() if f.status is not Status.OK]


def test_missing_dots_repaired_from_mrz(fake_engines):
    # A card of "Erika Müller": OCR and LLM read MULLER (dots lost), the MRZ says MUELLER.
    # The name line is not covered by check digits, so the other MRZ lines stay valid.
    id_card_state(fake_engines)
    mrz_line3 = "MUELLER<<ERIKA".ljust(30, "<")
    fake_engines["paddle"] = [FRONT_TEXT.replace("MUSTERMANN", "MULLER"),
                              BACK_TEXT.replace(GERMAN_ID_MRZ.splitlines()[2], mrz_line3)]
    fake_engines["tess"] = fake_engines["paddle"]
    fake_engines["llm"]["surname"] = "MULLER"
    p = run(fake_engines)
    f = p.fields["surname"]
    assert f.status is Status.WARN and f.value == "MÜLLER"


def test_birth_date_different_from_mrz_is_red(fake_engines):
    id_card_state(fake_engines, date_of_birth="12.08.1965")
    fake_engines["paddle"][0] = FRONT_TEXT.replace("1964", "1965")
    p = run(fake_engines)
    assert p.fields["date_of_birth"].status is Status.FAIL
    assert p.status is Status.FAIL


def test_invented_value_is_yellow(fake_engines):
    id_card_state(fake_engines, place_of_birth="HAMBURG")  # not in the OCR text
    p = run(fake_engines)
    assert p.fields["place_of_birth"].status is Status.WARN


def test_umlaut_from_second_engine(fake_engines):
    id_card_state(fake_engines, issuing_authority="STADT KOLN")
    fake_engines["paddle"][1] = BACK_TEXT.replace("STADT KÖLN", "STADT KOLN")  # Paddle lost the dots
    p = run(fake_engines)
    f = p.fields["issuing_authority"]
    assert f.value == "STADT KÖLN" and f.status is Status.WARN


def test_two_digit_year_is_yellow(fake_engines):
    fake_engines["paddle"] = ["FÜHRERSCHEIN\n1. Mustermann\n2. Desiré Jeanette\n3. 28.02.64 Bonn\n5. B072RRE2I51"]
    fake_engines["tess"] = [""]
    fake_engines["llm"] = {"surname": "Mustermann", "given_names": "Desiré Jeanette", "date_of_birth": "28.02.64",
                           "driving_licence_number": "B072RRE2I51"}
    p = run(fake_engines)
    assert p.doc_type.doc_type == "DE_DRIVING_LICENCE"
    assert p.fields["date_of_birth"].status is Status.WARN
    assert not any(c.name == "mrz" for c in p.document_checks)  # no MRZ needed for driving licences
    assert p.match is None


def test_front_side_only_asks_for_back(fake_engines):
    id_card_state(fake_engines)
    fake_engines["paddle"] = [FRONT_TEXT]
    fake_engines["tess"] = [FRONT_TEXT]
    p = run(fake_engines)
    assert p.doc_type.doc_type == "DE_ID_CARD"
    mrz_checks = [c for c in p.document_checks if c.name == "mrz"]
    assert mrz_checks and mrz_checks[0].status is Status.WARN


def test_not_issued_in_germany_is_rejected(fake_engines):
    utopia = "I<UTOD231458907<<<<<<<<<<<<<<<\n7408122F1204159UTO<<<<<<<<<<<6\nERIKSSON<<ANNA<MARIA<<<<<<<<<<"
    fake_engines["paddle"] = ["IDENTITY CARD\n" + utopia]
    fake_engines["tess"] = [""]
    fake_engines["llm"] = {"surname": "ERIKSSON"}
    p = run(fake_engines)
    assert p.rejected and p.status is Status.FAIL


def test_bad_iban_is_red(fake_engines):
    fake_engines["paddle"] = ["Kontoeröffnung (KYC)\nIBAN DE89 3704 0044 0532 0130 01"]
    fake_engines["tess"] = [""]
    fake_engines["llm"] = {"iban": "DE89 3704 0044 0532 0130 01"}
    p = run(fake_engines)
    assert p.doc_type.doc_type == "FORM_KYC"
    assert p.fields["iban"].status is Status.FAIL


def test_match_with_vault_and_approve(fake_engines, vault, store, gen):
    id_card_state(fake_engines)
    p = run(fake_engines, vault)
    assert p.match.kind == "new"
    values = {name: f.value for name, f in p.fields.items()}
    saved = save_document(vault, store, gen, pipeline.to_approved(p, values, [b"img"]), None)
    p2 = run(fake_engines, vault)
    assert p2.match.kind == "existing" and p2.match.person_id == saved.person_id


# --- Slow: real OCR + real LLM on the official specimen (needs Ollama and the specimen images) ---

SPECIMENS = Path(__file__).resolve().parent.parent / "samples" / "specimens"


@pytest.mark.slow
@pytest.mark.skipif(not (SPECIMENS / "de_id_card_2010_back.jpg").exists(), reason="specimens not downloaded")
def test_real_id_card_specimen():
    files = [(n, (SPECIMENS / n).read_bytes()) for n in ("de_id_card_2010_front.jpg", "de_id_card_2010_back.jpg")]
    p = pipeline.analyse_document(files)
    assert p.doc_type.doc_type == "DE_ID_CARD" and p.issuing_country == "DEU"
    assert p.fields["surname"].value == "MUSTERMANN" and p.fields["surname"].status is Status.OK
    assert p.fields["date_of_birth"].status is Status.OK
    assert p.fields["id_card_number"].status is Status.OK


# --- Fixes found in the first real run on the specimen ---

def test_two_digit_issue_year_does_not_make_expiry_red(fake_engines):
    id_card_state(fake_engines, issue_date="01.11.10")
    p = run(fake_engines)
    assert p.fields["issue_date"].status is Status.WARN
    assert p.fields["expiry_date"].status is Status.OK


def test_llm_gender_without_mrz_proof_is_yellow(fake_engines):
    id_card_state(fake_engines, gender="F")  # the specimen MRZ has "<" (sex not stated)
    p = run(fake_engines)
    assert p.fields["gender"].status is Status.WARN


def test_mrz_text_in_a_field_is_dropped(fake_engines):
    id_card_state(fake_engines, remarks="MUSTERMANN<<ERIKA<<<<<<<<<<<<<")
    p = run(fake_engines)
    assert "remarks" not in p.fields


def test_address_on_two_lines_is_found(fake_engines):
    id_card_state(fake_engines)
    fake_engines["paddle"][1] = BACK_TEXT.replace("51147 KÖLN\nHEIDESTRASSE 17", "51147KÖLN\n160cm\nHEIDESTRASSE 17")
    p = run(fake_engines)
    assert p.fields["address"].status is Status.OK


def test_field_not_on_this_document_type_is_yellow(fake_engines):
    # Real case: the LLM took the watermark "MUSTER" as birth name on a driving licence.
    fake_engines["paddle"] = ["FÜHRERSCHEIN\nMUSTER\n1. Mustermann\n2. Desiré Jeanette\n5. B072RRE2I51"]
    fake_engines["tess"] = [""]
    fake_engines["llm"] = {"surname": "Mustermann", "birth_name": "MUSTER", "driving_licence_number": "B072RRE2I51"}
    p = run(fake_engines)
    assert p.fields["birth_name"].status is Status.WARN
    assert p.fields["surname"].status is Status.OK


def test_residence_permit_allows_height_and_eye_colour():
    """Real bug (user report 2026-11-16): the back side of the residence permit shows height and
    eye colour, but they were marked "Check – probably a mix-up"."""
    allowed = pipeline.ALLOWED_FIELDS["DE_RESIDENCE_PERMIT"]
    assert {"height", "eye_colour"} <= allowed
    # Real mix-ups are still found: a residence permit has no ID card number.
    assert "id_card_number" not in allowed




def _td3(state_code: str, surname="MUELLER", given="JONAS", dob="900412", number="Z1234567<") -> str:
    """A valid TD3 passport MRZ (check digits computed), issued by `state_code`. Invented data."""
    from app.checks.mrz import check_digit
    exp = "301021"
    line1 = f"P<{state_code}{surname}<<{given}".ljust(44, "<")
    personal = "<" * 14
    body = number + str(check_digit(number)) + state_code + dob + str(check_digit(dob)) + "M" + exp + \
        str(check_digit(exp)) + personal + str(check_digit(personal))
    composite = body[0:10] + body[13:20] + body[21:43]
    return line1 + "\n" + body + str(check_digit(composite))


def test_foreign_passport_is_accepted_as_foreign_passport(fake_engines):
    fake_engines["paddle"] = ["REPUBLIC OF INDIA\nPASSPORT\n" + _td3("IND")]
    fake_engines["tess"] = [""]
    fake_engines["llm"] = {"surname": "MUELLER", "given_names": "JONAS", "date_of_birth": "12.04.1990",
                           "passport_number": "Z1234567"}
    p = run(fake_engines)
    assert p.mrz and p.mrz.valid, p.mrz and p.mrz.checks
    assert not p.rejected
    assert p.doc_type.doc_type == pipeline.FOREIGN_PASSPORT and p.issuing_country == "IND"
    assert any("already exists" in c.message for c in p.document_checks)
    assert p.fields["passport_number"].status is Status.OK          # compared with the MRZ number


def test_foreign_id_card_is_still_rejected_with_a_clear_label(fake_engines):
    utopia = "I<UTOD231458907<<<<<<<<<<<<<<<\n7408122F1204159UTO<<<<<<<<<<<6\nERIKSSON<<ANNA<MARIA<<<<<<<<<<"
    fake_engines["paddle"] = ["IDENTITY CARD\n" + utopia]
    fake_engines["tess"] = [""]
    fake_engines["llm"] = {"surname": "ERIKSSON"}
    p = run(fake_engines)
    assert p.rejected and p.doc_type.doc_type == "FOREIGN_DOCUMENT"   # not "DE_ID_CARD"
