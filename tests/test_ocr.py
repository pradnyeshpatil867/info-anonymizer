"""OCR tests on the official specimen images in samples/specimens/.

samples/ is git-ignored, so these tests are SKIPPED when the images are missing.
They are marked "slow" because PaddleOCR needs a few seconds per image.
Run only the fast tests with:  uv run pytest -m "not slow"
"""

from pathlib import Path

import pytest
from PIL import Image

from app.checks.mrz import extract_mrz_lines, parse_mrz
from app.ocr.pdf import load_pages, upscale_if_small

SPECIMENS = Path(__file__).resolve().parent.parent / "samples" / "specimens"
needs_specimens = pytest.mark.skipif(not SPECIMENS.exists(), reason="specimen images not downloaded")


def test_load_image_and_pdf(tmp_path):
    img = Image.new("RGB", (400, 200), "white")
    img.save(tmp_path / "a.png")
    img.save(tmp_path / "a.pdf")  # Pillow can write a one-page PDF
    assert len(load_pages(tmp_path / "a.png")) == 1
    pages = load_pages(tmp_path / "a.pdf")
    assert len(pages) == 1 and pages[0].mode == "RGB"


def test_upscale_if_small():
    small = Image.new("RGB", (500, 300))
    assert upscale_if_small(small, 1600).size == (1600, 960)
    big = Image.new("RGB", (2000, 1000))
    assert upscale_if_small(big, 1600) is big


@pytest.mark.slow
@needs_specimens
def test_paddle_reads_id_card_back_mrz():
    from app.ocr import paddle
    img = upscale_if_small(load_pages(SPECIMENS / "de_id_card_2010_back.jpg")[0], 1600)
    r = parse_mrz("\n".join(extract_mrz_lines(paddle.read(img).text)))
    assert r.valid and r.surname == "MUSTERMANN" and r.given_names == "ERIKA" and r.issuing_state == "D"


@pytest.mark.slow
@needs_specimens
def test_paddle_reads_passport_mrz():
    from app.ocr import paddle
    img = upscale_if_small(load_pages(SPECIMENS / "de_passport.jpg")[0], 1600)
    r = parse_mrz("\n".join(extract_mrz_lines(paddle.read(img).text)))
    assert r.valid and r.layout == "TD3" and r.document_number == "C1000000"


@pytest.mark.slow
@needs_specimens
def test_tesseract_mrz_mode_on_id_card_back():
    from app.ocr import tesseract
    mrz = tesseract.read_mrz(load_pages(SPECIMENS / "de_id_card_2010_back.jpg")[0])
    assert mrz is not None
    assert parse_mrz(mrz).valid  # check digits pass after the filler repair


from app.ocr.result import is_latin_text  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("MUSTERMANN", True),
    ("Größe/Height/Taille", True),
    ("Desiré Jeanette", True),
    ("10.06.82", True),
    ("IDD<<T220001293<<<", True),
    ("招道", False),       # real output from the driving licence back
    ("福", False),
    ("", False),
    ("   ", False),
])
def test_is_latin_text(text, expected):
    assert is_latin_text(text) is expected


@pytest.mark.slow
@needs_specimens
def test_no_chinese_on_driving_licence_back():
    from app.ocr import paddle
    img = upscale_if_small(load_pages(SPECIMENS / "de_driving_licence_back.jpg")[0], 1600)
    assert all(is_latin_text(line.text) for line in paddle.read(img).lines)
