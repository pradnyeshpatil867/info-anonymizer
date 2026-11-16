"""Compare local Ollama models for field extraction (spec section M.3).

For every model and every test document:
  1. read the pages and run PaddleOCR (once per document, then cached),
  2. let the model fill in the fields,
  3. compare the answer with the known truth.

Test documents and their truth:
  samples/synthetic/truth.json   (made by scripts/make_synthetic.py)
  samples/specimens/truth.json   (official specimen documents)
A truth key like "a.png + b.png" means two pages of one document.

Usage:
  uv run python scripts/compare_llms.py                      # the three candidates
  uv run python scripts/compare_llms.py qwen2.5:1.5b-instruct --limit 1
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import ollama

# Make "import app" work when this file is run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import project_path, settings  # noqa: E402
from app.llm.extractor import ExtractionError, extract_fields, join_pages  # noqa: E402
from app.llm.schema import ExtractedFields  # noqa: E402
from app.ocr import paddle  # noqa: E402
from app.ocr.pdf import load_pages, upscale_if_small  # noqa: E402

CANDIDATES = ["qwen2.5:1.5b-instruct", "qwen3-vl:2b-instruct", "qwen3-vl:4b-instruct"]
TRUTH_FILES = [project_path("samples/synthetic/truth.json"), project_path("samples/specimens/truth.json")]
OUTPUT = project_path("samples/llm_comparison.json")
DATE_FIELDS = {"date_of_birth", "issue_date", "expiry_date"}
FIELDS = set(ExtractedFields.model_fields)


def load_documents() -> list[dict]:
    """Read all truth files. Each document: name, page files, truth fields."""
    docs = []
    for truth_file in TRUTH_FILES:
        if not truth_file.exists():
            print(f"Missing truth file, skipped: {truth_file}")
            continue
        for key, truth in json.loads(truth_file.read_text(encoding="utf-8")).items():
            pages = [truth_file.parent / name.strip() for name in key.split("+")]
            # Keys like "mrz" or "name_as_printed" are not LLM fields, so we ignore them.
            fields = {k: v for k, v in truth.items() if k in FIELDS}
            docs.append({"name": key, "pages": pages, "truth": fields})
    return docs


def same(field: str, expected: str, got: str | None) -> bool:
    """Lenient compare: ignore case, spaces and punctuation. Dates: compare only the digits.

    We want to measure if the model found the right text, not small format differences.
    The strict checks come later in app/checks/.
    """
    if got is None:
        return False
    if field in DATE_FIELDS:
        return re.sub(r"\D", "", expected) == re.sub(r"\D", "", got)
    simple = lambda s: re.sub(r"[\W_]+", "", s.casefold())  # noqa: E731
    return simple(expected) == simple(got)


def ocr_document(doc: dict, cache: dict) -> tuple[list, str]:
    """Return (page images, OCR text). OCR is slow, so each document is read only once."""
    if doc["name"] not in cache:
        images = [img for path in doc["pages"] for img in load_pages(path)]
        min_width = settings()["ocr_min_width"]
        texts = [paddle.read(upscale_if_small(img, min_width)).text for img in images]
        cache[doc["name"]] = (images, join_pages(texts))
    return cache[doc["name"]]


def warm_up(client, model: str) -> None:
    """Load the model into memory first, so loading time is not counted for the first document."""
    client.chat(model=model, messages=[{"role": "user", "content": "Say OK."}], options={"num_predict": 1})


def run_model(client, model: str, docs: list[dict], ocr_cache: dict) -> dict:
    warm_up(client, model)
    correct = total = 0
    seconds: list[float] = []
    per_doc = []
    for doc in docs:
        images, text = ocr_document(doc, ocr_cache)
        try:
            result = extract_fields(images, text, model=model, client=client)
            got = result.fields.model_dump()
            seconds.append(result.seconds)
            error, used_images = None, result.used_images
        except ExtractionError as e:
            got, error, used_images = {}, str(e), None
        wrong = {}
        for field, expected in doc["truth"].items():
            total += 1
            if same(field, expected, got.get(field)):
                correct += 1
            else:
                wrong[field] = {"expected": expected, "got": got.get(field)}
        per_doc.append({"document": doc["name"], "used_images": used_images,
                        "seconds": round(seconds[-1], 2) if error is None else None,
                        "error": error, "wrong": wrong, "answer": got})
        print(f"  {doc['name']}: {len(doc['truth']) - len(wrong)}/{len(doc['truth'])} correct")
    return {
        "model": model,
        "documents": len(docs),
        "correct": correct,
        "total": total,
        "accuracy": round(100 * correct / total, 1) if total else 0.0,
        "avg_seconds": round(sum(seconds) / len(seconds), 2) if seconds else None,
        "results": per_doc,
    }


def print_table(summaries: list[dict]) -> None:
    print()
    print(f"{'model':<26} {'docs':>4} {'correct':>9} {'accuracy':>9} {'avg s/doc':>10}")
    for s in summaries:
        avg = f"{s['avg_seconds']:.1f}" if s["avg_seconds"] is not None else "-"
        print(f"{s['model']:<26} {s['documents']:>4} {s['correct']:>4}/{s['total']:<4} "
              f"{s['accuracy']:>8.1f}% {avg:>10}")
    for s in summaries:
        print(f"\nWrong fields for {s['model']}:")
        for r in s["results"]:
            if r["error"]:
                print(f"  {r['document']}: ERROR {r['error'][:200]}")
            for field, w in r["wrong"].items():
                print(f"  {r['document']}: {field}: expected {w['expected']!r}, got {w['got']!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*", default=CANDIDATES, help="Ollama model names")
    parser.add_argument("--limit", type=int, default=None, help="only use the first N documents")
    args = parser.parse_args()

    client = ollama.Client(host=settings()["ollama_host"])
    installed = {m.model for m in client.list().models}
    models = [m for m in args.models if m in installed]
    for m in args.models:
        if m not in installed:
            print(f"Not installed, skipped: {m}")

    docs = load_documents()[: args.limit]
    print(f"{len(docs)} documents, {len(models)} models")

    ocr_cache: dict = {}
    summaries = []
    for model in models:
        print(f"\nModel {model}")
        start = time.perf_counter()
        summaries.append(run_model(client, model, docs, ocr_cache))
        print(f"  total {time.perf_counter() - start:.1f} s")

    print_table(summaries)
    OUTPUT.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {OUTPUT}")


if __name__ == "__main__":
    main()
