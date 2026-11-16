"""PaddleOCR reader (main OCR engine).

Setup (spec section M):
- model family PP-OCRv6 ("medium" size), language German ("de")
- run with ONNX Runtime on the CPU, no big paddlepaddle framework
- model files are kept in the project folder models/paddlex (git-ignored),
  not in the user's home folder
"""

import os
import time
from functools import lru_cache

import numpy as np
from PIL import Image

from app.config import project_path, settings
from app.ocr.result import OcrLine, OcrResult, is_latin_text, sort_reading_order

# Must be set BEFORE paddleocr is imported, because PaddleX reads it at import time.
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(project_path(settings()["models_dir"]) / "paddlex"))
# Do not contact the model servers on every start, only when a model is missing.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@lru_cache(maxsize=1)
def _engine():
    """Create the PaddleOCR engine once. Loading models takes a few seconds."""
    from paddleocr import PaddleOCR  # imported here, after the environment is set

    return PaddleOCR(
        lang=settings()["paddle_lang"],
        ocr_version="PP-OCRv6",
        engine="onnxruntime",
        # Extra models for rotation and bent pages are off for now. They cost more
        # downloads and time; we switch them on if tests with phone photos need them.
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def read(img: Image.Image) -> OcrResult:
    """Read all text lines of one page image."""
    start = time.perf_counter()
    results = _engine().predict(np.array(img.convert("RGB")))
    lines: list[OcrLine] = []
    for res in results:
        for text, score, poly in zip(res["rec_texts"], res["rec_scores"], res["rec_polys"]):
            if not is_latin_text(str(text)):
                continue  # empty line or non-Latin characters (for example Chinese in icons)
            xs, ys = [int(p[0]) for p in poly], [int(p[1]) for p in poly]
            lines.append(OcrLine(text=str(text), score=float(score), box=(min(xs), min(ys), max(xs), max(ys))))
    return OcrResult("paddleocr", sort_reading_order(lines), time.perf_counter() - start)
