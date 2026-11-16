"""Tesseract reader: second OCR engine and MRZ reader.

Two modes:
- read(img):      normal text, German + English
- read_mrz(img):  only the MRZ lines, with the characters limited to A-Z, 0-9 and "<"
"""

import re
import time

import pytesseract
from PIL import Image, ImageOps

from app.config import settings
from app.ocr.result import OcrLine, OcrResult, sort_reading_order

MRZ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_MRZ_LINE = re.compile(r"^[A-Z0-9<]{25,50}$")


def read(img: Image.Image) -> OcrResult:
    """Read all text lines. Tesseract returns words; we join them into lines."""
    start = time.perf_counter()
    data = pytesseract.image_to_data(
        img, lang=settings()["tesseract_languages"], config="--psm 3",
        output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list[int]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip() and float(data["conf"][i]) >= 0:
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append(i)
    lines = []
    for idx in groups.values():
        left = min(data["left"][i] for i in idx)
        top = min(data["top"][i] for i in idx)
        right = max(data["left"][i] + data["width"][i] for i in idx)
        bottom = max(data["top"][i] + data["height"][i] for i in idx)
        text = " ".join(data["text"][i] for i in idx)
        score = sum(float(data["conf"][i]) for i in idx) / len(idx) / 100
        lines.append(OcrLine(text, score, (left, top, right, bottom)))
    return OcrResult("tesseract", sort_reading_order(lines), time.perf_counter() - start)


def _mrz_lines_from(img: Image.Image) -> list[str]:
    config = f"--psm 6 -c tessedit_char_whitelist={MRZ_CHARS}"
    text = pytesseract.image_to_string(img, lang="eng", config=config)
    lines = [ln.replace(" ", "") for ln in text.splitlines()]
    return [ln for ln in lines if _MRZ_LINE.match(ln) and "<" in ln]


def read_mrz(img: Image.Image) -> str | None:
    """Find and read the MRZ. Returns the MRZ lines joined by newlines, or None.

    The MRZ is at the bottom of passports and ID cards, so we first try the
    lower part of the image. Grayscale and enlarging help Tesseract.
    """
    gray = ImageOps.grayscale(img)
    w, h = gray.size
    attempts = [
        gray.crop((0, int(h * 0.55), w, h)),   # bottom 45 %
        gray,                                  # whole image
    ]
    for part in attempts:
        if part.width < 2000:
            factor = 2000 / part.width
            part = part.resize((2000, round(part.height * factor)), Image.LANCZOS)
        lines = _mrz_lines_from(part)
        if len(lines) >= 2:
            return "\n".join(lines[-3:] if len(lines) >= 3 else lines)
    return None
