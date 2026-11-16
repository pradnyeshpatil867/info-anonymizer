"""Run both OCR engines on files and print the results side by side.

Usage:
    uv run python -m app.ocr.demo samples/specimens/de_id_card_2010_back.jpg
"""

import sys

from app.checks.mrz import choose_mrz, extract_mrz_lines, parse_mrz
from app.config import settings
from app.ocr import paddle, tesseract
from app.ocr.pdf import load_pages, upscale_if_small


def run(path: str) -> None:
    for n, page in enumerate(load_pages(path), 1):
        img = upscale_if_small(page, settings()["ocr_min_width"])
        print(f"\n===== {path}  page {n}  ({page.width}x{page.height} -> {img.width}x{img.height}) =====")
        p = paddle.read(img)
        t = tesseract.read(img)
        print(f"--- PaddleOCR ({p.seconds:.1f} s) ---")
        for line in p.lines:
            print(f"  {line.score:.2f}  {line.text}")
        print(f"--- Tesseract ({t.seconds:.1f} s) ---")
        for line in t.lines:
            print(f"  {line.score:.2f}  {line.text}")
        sources = {
            "PaddleOCR": "\n".join(extract_mrz_lines(p.text)) or None,
            "Tesseract MRZ mode": tesseract.read_mrz(page),
        }
        for name, mrz in sources.items():
            print(f"--- {name} ---")
            if not mrz:
                print("  (no MRZ found)")
                continue
            print("  " + mrz.replace("\n", "\n  "))
            try:
                r = parse_mrz(mrz)
                for c in r.checks:
                    print(f"  [{c.status.value}] {c.message}")
                print(f"  issuing_state={r.issuing_state!r} surname={r.surname!r} given={r.given_names!r}")
            except ValueError as e:
                print(f"  could not parse: {e}")
        chosen, verdict = choose_mrz(sources)
        print(f"--- Chosen MRZ: [{verdict.status.value}] {verdict.message}")
        if chosen:
            print(f"  issued in Germany: {chosen.issued_in_germany}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        run(arg)

