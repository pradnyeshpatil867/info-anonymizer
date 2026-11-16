"""Field extraction with a local Ollama LLM (spec sections G and M).

The OCR engines give us lines of text, but not which text is which field.
The LLM reads the OCR text (and the page images, if the model can see
images) and fills in `ExtractedFields`.

Rules:
- Only the local Ollama server is used. Nothing leaves the computer.
- Ollama gets the JSON schema of ExtractedFields ("structured output"),
  so the answer always has the right shape.
- We do no real checks here. All checks happen later in app/checks/.
"""

import io
import time
from dataclasses import dataclass

import ollama
from PIL import Image
from pydantic import ValidationError

from app.config import settings
from app.llm.schema import ExtractedFields, llm_json_schema

SYSTEM_PROMPT = """\
You read documents issued in Germany: ID cards, passports, residence permits,
driving licences, Meldebescheinigungen and forms. You fill in a JSON object
with the fields of the document.

Rules:
1. Copy every value EXACTLY as it is printed. Keep umlauts and special
   letters (Ä, Ö, Ü, ß, é). Keep the date format of the document
   (for example 12.08.1964 or 05.11.99). Do not translate values.
2. Never invent or guess a value. If a field is not on the document,
   use null.
3. The OCR text may join words that belong to different things.
   Examples: "51147KÖLN" is the postcode 51147 and the city KÖLN.
   "GEB.GABLER" means the birth name (Geburtsname) is GABLER.
4. Driving licences print field numbers before the values, for example
   "1. Mustermann" (surname), "2. Erika" (given names), "3. 12.08.1964 Berlin"
   (date and place of birth), "4a." issue date, "4b." expiry date,
   "4c." issuing authority, "5." licence number. Do not copy the numbers.
5. On forms a name may be written as "SURNAME, Given names".
   Put the part before the comma into surname and the rest into given_names.
6. Lines with many "<" characters are the machine readable zone (MRZ).
   Do not copy names from the MRZ when the name is printed elsewhere
   on the document.
7. Labels such as "Name", "Vornamen", "Geburtstag" are not values.
"""

USER_PROMPT = """\
Here is the OCR text of the document. Pages are separated by "--- page N ---".
{images_note}
OCR text:
{ocr_text}
"""

IMAGES_NOTE = "The page images are attached as well. Use them to check the OCR text.\n"

# Upper limit for the answer length (in tokens). A full answer needs about 300.
# Small models sometimes repeat the same text forever; this limit stops them.
# A cut-off answer is not valid JSON, so it ends as an ExtractionError.
MAX_ANSWER_TOKENS = 1500

# Seconds to wait for Ollama. Vision models on a CPU can be slow, but never this slow.
TIMEOUT_SECONDS = 600


class ExtractionError(Exception):
    """The LLM answer could not be turned into ExtractedFields."""


@dataclass
class ExtractionResult:
    fields: ExtractedFields
    model: str          # the Ollama model that was used
    used_images: bool   # True if the page images were sent to the model
    seconds: float      # how long the LLM call took
    raw: str            # the raw answer text, kept for debugging


# model name -> its capabilities, for example ["completion", "vision", "thinking"].
# Asking Ollama costs a request, so we ask once per model.
_vision_cache: dict[str, list[str]] = {}


def _get(obj, key: str):
    """Read a value from an Ollama response.

    Ollama's response objects allow both obj.key and obj["key"]; plain dicts
    (for example from a fake client in tests) allow only obj["key"].
    """
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def capabilities(client, model: str) -> list[str]:
    """What the model can do, as Ollama lists it under "capabilities"."""
    if model not in _vision_cache:
        _vision_cache[model] = list(_get(client.show(model), "capabilities") or [])
    return _vision_cache[model]


def supports_vision(client, model: str) -> bool:
    """True if the model can read images."""
    return "vision" in capabilities(client, model)


def is_thinking_model(client, model: str) -> bool:
    """True if the model reasons step by step before answering ("thinking").

    For copying values from a document, thinking only costs time, so we switch it off.
    We send the switch only to thinking models, because other models may refuse it.
    """
    return "thinking" in capabilities(client, model)


def image_to_png(img: Image.Image, max_side: int) -> bytes:
    """Shrink the image so its longest side is at most max_side, and return PNG bytes.

    Big images make vision models slow and use a lot of memory.
    Small images are not enlarged.
    """
    img = img.convert("RGB")
    longest = max(img.size)
    if longest > max_side:
        factor = max_side / longest
        img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def join_pages(page_texts: list[str]) -> str:
    """Put the OCR text of several pages into one text, with the page marker the prompt explains."""
    return "\n".join(f"--- page {i} ---\n{text}" for i, text in enumerate(page_texts, start=1))


def _clean(fields: ExtractedFields) -> ExtractedFields:
    """Strip spaces and turn empty strings into None.

    Small models sometimes answer "" instead of null. Both mean "not found".
    """
    data = fields.model_dump()
    for key, value in data.items():
        if isinstance(value, str) and key != "doc_type":
            value = value.strip()
            data[key] = value or None
    return ExtractedFields(**data)


def extract_fields(images: list[Image.Image], ocr_text: str, model: str | None = None,
                   client=None) -> ExtractionResult:
    """Ask the local LLM which text belongs to which field.

    images: the page images of one document (sent only to vision models).
    ocr_text: the OCR text of all pages.
    client: an ollama.Client; tests pass a fake one.
    """
    cfg = settings()
    model = model or cfg["llm_model"]
    client = client or ollama.Client(host=cfg["ollama_host"], timeout=TIMEOUT_SECONDS)

    use_images = bool(images) and supports_vision(client, model)
    user_message = {
        "role": "user",
        "content": USER_PROMPT.format(images_note=IMAGES_NOTE if use_images else "", ocr_text=ocr_text),
    }
    if use_images:
        user_message["images"] = [image_to_png(img, cfg["llm_max_image_side"]) for img in images]

    extra = {"think": False} if is_thinking_model(client, model) else {}

    start = time.perf_counter()
    response = client.chat(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, user_message],
        format=llm_json_schema(),
        options={"temperature": cfg["llm_temperature"], "num_predict": MAX_ANSWER_TOKENS},
        **extra,
    )
    seconds = time.perf_counter() - start

    raw = _get(_get(response, "message"), "content") or ""
    try:
        fields = ExtractedFields.model_validate_json(raw)
    except ValidationError as e:
        raise ExtractionError(f"Model {model} gave an answer that is not valid field JSON: {e}") from e
    return ExtractionResult(fields=_clean(fields), model=model, used_images=use_images, seconds=seconds, raw=raw)
