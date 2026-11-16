"""OCR accuracy on the synthetic test documents (scripts/make_synthetic.py).

samples/ is git-ignored. Create the files first with:
    uv run python scripts/make_synthetic.py
Tests are SKIPPED when the files are missing, and marked "slow".

Decision (milestone 3, option a): PaddleOCR must find every value.
Tesseract is not required to, because it fails on text inside form boxes.
"""

import json
import re
from pathlib import Path

import pytest

from app.checks.mrz import choose_mrz, extract_mrz_lines
from app.ocr.pdf import load_pages, upscale_if_small

SYNTH = Path(__file__).resolve().parent.parent / "samples" / "synthetic"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (SYNTH / "truth.json").exists(), reason="run scripts/make_synthetic.py first"),
]
NOT_PRINTED_AS_IS = {"doc_type", "issuing_country", "mrz", "gender", "nationality"}


def _truth() -> dict:
    return json.loads((SYNTH / "truth.json").read_text(encoding="utf-8"))


def _squash(s: str) -> str:
    """Ignore spaces and case: OCR often joins or splits words ('51147KÖLN')."""
    return re.sub(r"\s+", "", s).upper()


def _paddle_text(key: str) -> str:
    from app.ocr import paddle
    parts = []
    for name in key.split("+"):
        img = upscale_if_small(load_pages(SYNTH / name.strip())[0], 1600)
        parts.append(paddle.read(img).text)
    return "\n".join(parts)


@pytest.mark.parametrize("key", [
    "de_residence_permit_front.png + de_residence_permit_back.png",
    "de_meldebescheinigung.pdf",
    "form_kyc_krishnan.pdf",
    "form_kyc_mustermann.pdf",
])
def test_paddle_finds_every_value(key):
    truth = _truth()[key]
    text = _squash(_paddle_text(key))
    missing = [f"{k}={v!r}" for k, v in truth.items() if k not in NOT_PRINTED_AS_IS and _squash(v) not in text]
    assert not missing, f"PaddleOCR missed: {missing}"


def test_residence_permit_mrz():
    from app.ocr import paddle, tesseract
    img = upscale_if_small(load_pages(SYNTH / "de_residence_permit_back.png")[0], 1600)
    chosen, verdict = choose_mrz({
        "PaddleOCR": "\n".join(extract_mrz_lines(paddle.read(img).text)) or None,
        "Tesseract": tesseract.read_mrz(img),
    })
    assert verdict.ok
    assert chosen.document_code == "AR" and chosen.issued_in_germany and chosen.nationality == "IND"
    assert chosen.surname == "KRISHNAN" and chosen.document_number == "Y8A2B3C4D"
