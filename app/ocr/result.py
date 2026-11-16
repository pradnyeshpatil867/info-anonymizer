"""Shared result types for all OCR engines."""

import unicodedata
from dataclasses import dataclass, field


@dataclass
class OcrLine:
    text: str
    score: float                 # engine confidence, 0.0 to 1.0
    box: tuple[int, int, int, int]  # left, top, right, bottom in pixels

    @property
    def top(self) -> int:
        return self.box[1]


@dataclass
class OcrResult:
    engine: str                  # "paddleocr" or "tesseract"
    lines: list[OcrLine] = field(default_factory=list)
    seconds: float = 0.0         # how long the engine needed

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def sort_reading_order(lines: list[OcrLine], row_tolerance: int = 12) -> list[OcrLine]:
    """Sort lines top to bottom, and left to right inside one row.

    Two boxes count as the same row when their tops differ by less than row_tolerance pixels.
    """
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda x: x.top):
        if rows and abs(rows[-1][0].top - line.top) < row_tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [ln for row in rows for ln in sorted(row, key=lambda x: x.box[0])]


def is_latin_text(text: str) -> bool:
    """True if every letter in the text is a Latin letter (A-Z, Ä, ß, é, ...).

    Documents issued in Germany use only Latin letters. PaddleOCR's model also
    knows Chinese and sometimes "sees" Chinese characters in small icons;
    such lines are dropped (user decision, milestone 3).
    """
    if not text.strip():
        return False
    for ch in text:
        if ch.isalpha() and "LATIN" not in unicodedata.name(ch, ""):
            return False
    return True
